"""Read-only JSON API for Homepage customapi widgets. Python stdlib only."""
import calendar
import http.client
import json
import os
import random
import re
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import panels

MOCK = os.environ.get("MOCK") == "1"
HOSTFS = os.environ.get("HOSTFS", "/hostfs")
PROC = "/proc"
SYS = "/sys"
ENERGY_DIR = os.environ.get("ENERGY_DIR", HOSTFS + "/home/swastik/energy-tracker")
DB_PATH = ENERGY_DIR + "/energy_tracker.db"
LIVE_PATH = ENERGY_DIR + "/live.json"
RATE_INR = float(os.environ.get("RATE_INR_PER_KWH", "5"))
DOCKER_SOCK = "/var/run/docker.sock"
IMMICH_URL = os.environ.get("IMMICH_URL", "http://192.168.31.125:2283").rstrip("/")
IMMICH_KEY = os.environ.get("IMMICH_API_KEY", "")
GH_TOKEN = os.environ.get("DASH_GITHUB_TOKEN", "")
GH_ORG = os.environ.get("GITHUB_ORG", "swastik-labs")
GH_RUNNER = os.environ.get("GITHUB_RUNNER", "homelab-laptop")

_cache = {}
_cache_lock = threading.Lock()


def cached(ttl):
    def deco(fn):
        lock = threading.Lock()

        def wrapper():
            now = time.time()
            hit = _cache.get(fn.__name__)
            if hit and now - hit[0] < ttl:
                return hit[1]
            with lock:  # one refresh at a time per endpoint
                hit = _cache.get(fn.__name__)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]
                try:
                    val = fn()
                except Exception as e:  # never crash: report and keep serving
                    val = {"error": str(e)[:200]}
                with _cache_lock:
                    _cache[fn.__name__] = (time.time(), val)
                return val
        return wrapper
    return deco


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def read_num(path):
    v = read(path)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def human_secs(s):
    if s is None:
        return None
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")


def human_bytes(b):
    if b is None:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024 or unit == "TB":
            return f"{b:.1f} {unit}" if unit != "B" else f"{int(b)} B"
        b /= 1024


def jitter(v, pct=0.05):
    return round(v * (1 + random.uniform(-pct, pct)), 2)


# ---------------------------------------------------------------- host processes
def list_procs():
    out = []
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except Exception:
        return out
    for pid in pids:
        stat = read(f"{PROC}/{pid}/stat")
        if not stat:
            continue
        try:
            rpar = stat.rindex(")")
            comm = stat[stat.index("(") + 1:rpar]
            fields = stat[rpar + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
            out.append((int(pid), comm, ticks))
        except Exception:
            continue
    return out


def cmdline_match(needle):
    try:
        for pid in os.listdir(PROC):
            if pid.isdigit():
                try:
                    with open(f"{PROC}/{pid}/cmdline", "rb") as f:
                        if needle.encode() in f.read():
                            return True
                except Exception:
                    pass
    except Exception:
        return None
    return False


# ---------------------------------------------------------------- /health/system
_cpu_prev = {"total": None, "idle": None, "procs": {}, "t": None}


def cpu_times():
    line = read(f"{PROC}/stat", "").splitlines()[0].split()[1:]
    vals = [int(x) for x in line]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals[:8]), idle


def cpu_temp():
    try:
        for hw in os.listdir(f"{SYS}/class/hwmon"):
            name = read(f"{SYS}/class/hwmon/{hw}/name")
            if name in ("coretemp", "k10temp", "zenpower"):
                v = read_num(f"{SYS}/class/hwmon/{hw}/temp1_input")
                if v is not None:
                    return round(v / 1000, 1)
    except Exception:
        pass
    try:
        for tz in sorted(os.listdir(f"{SYS}/class/thermal")):
            if read(f"{SYS}/class/thermal/{tz}/type") in ("x86_pkg_temp", "TCPU", "acpitz"):
                v = read_num(f"{SYS}/class/thermal/{tz}/temp")
                if v is not None:
                    return round(v / 1000, 1)
    except Exception:
        pass
    return None


@cached(15)
def health_system():
    if MOCK:
        cpu = jitter(14, 0.6)
        return {
            "cpu_percent": cpu, "cpu_temp_c": jitter(52, 0.08),
            "ram_used_bytes": int(jitter(4.1e9)), "ram_total_bytes": int(7.6e9), "ram_percent": jitter(54),
            "disk_used_bytes": int(142e9), "disk_total_bytes": int(234e9), "disk_percent": 60.7,
            "uptime_seconds": 1_036_800 + int(time.time()) % 86400, "uptime": "12d 4h",
            "last_reboot": iso(time.time() - 1_036_800),
            "top1": f"java {jitter(6.2, 0.3)}%", "top2": f"immich {jitter(3.1, 0.3)}%", "top3": f"python3 {jitter(1.4, 0.3)}%",
            "top_processes": [{"name": "java", "cpu": 6.2}, {"name": "immich", "cpu": 3.1}, {"name": "python3", "cpu": 1.4}],
        }
    res = {}
    # CPU % and top processes from the delta since the previous call
    total, idle = cpu_times()
    procs = {pid: (comm, ticks) for pid, comm, ticks in list_procs()}
    prev = _cpu_prev
    if prev["total"] is None:
        time.sleep(0.5)
        prev.update(total=total, idle=idle, procs=procs)
        total, idle = cpu_times()
        procs = {pid: (comm, ticks) for pid, comm, ticks in list_procs()}
    dt, di = total - prev["total"], idle - prev["idle"]
    res["cpu_percent"] = round(100 * (dt - di) / dt, 1) if dt > 0 else None
    ncpu = os.cpu_count() or 1
    usage = {}
    for pid, (comm, ticks) in procs.items():
        p = prev["procs"].get(pid)
        if p and dt > 0:
            usage[comm] = usage.get(comm, 0) + (ticks - p[1])
    top = sorted(usage.items(), key=lambda kv: -kv[1])[:3]
    top = [{"name": n, "cpu": round(100 * t * ncpu / dt, 1)} for n, t in top]
    prev.update(total=total, idle=idle, procs=procs)
    res["top_processes"] = top
    for i in range(3):
        res[f"top{i+1}"] = f"{top[i]['name']} {top[i]['cpu']}%" if i < len(top) else None
    # RAM
    mem = {}
    for line in (read(f"{PROC}/meminfo", "") or "").splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.split()[0]) * 1024 if v.split() else 0
    if mem.get("MemTotal"):
        used = mem["MemTotal"] - mem.get("MemAvailable", 0)
        res.update(ram_used_bytes=used, ram_total_bytes=mem["MemTotal"],
                   ram_percent=round(100 * used / mem["MemTotal"], 1))
    # disk (host root via read-only bind)
    try:
        st = os.statvfs(HOSTFS)
        tot, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        used = tot - st.f_bfree * st.f_frsize
        res.update(disk_used_bytes=used, disk_total_bytes=tot, disk_percent=round(100 * used / (used + free), 1))
    except Exception:
        res.update(disk_used_bytes=None, disk_total_bytes=None, disk_percent=None)
    up = read(f"{PROC}/uptime")
    up = float(up.split()[0]) if up else None
    res["uptime_seconds"] = int(up) if up else None
    res["uptime"] = human_secs(up)
    btime = None
    for line in (read(f"{PROC}/stat", "") or "").splitlines():
        if line.startswith("btime"):
            btime = int(line.split()[1])
    res["last_reboot"] = iso(btime)
    res["cpu_temp_c"] = cpu_temp()
    return res


# ---------------------------------------------------------------- /health/battery
@cached(30)
def health_battery():
    if MOCK:
        return {"health_percent": 71.4, "charge_percent": random.choice([100, 100, 99]),
                "cycle_count": 412, "status": "Full"}
    base = f"{SYS}/class/power_supply/BAT0"
    if not os.path.isdir(base):
        return {"health_percent": None, "charge_percent": None, "cycle_count": None, "status": None}
    full = read_num(f"{base}/energy_full") or read_num(f"{base}/charge_full")
    design = read_num(f"{base}/energy_full_design") or read_num(f"{base}/charge_full_design")
    cyc = read_num(f"{base}/cycle_count")
    cap = read_num(f"{base}/capacity")
    return {
        "health_percent": round(100 * full / design, 1) if full and design else None,
        "charge_percent": cap,
        "cycle_count": int(cyc) if cyc is not None else None,
        "status": read(f"{base}/status"),
    }


# ---------------------------------------------------------------- /health/ssd
@cached(30)
def health_ssd():
    if MOCK:
        return {"nvme_temp_c": jitter(41, 0.05)}
    try:
        for hw in sorted(os.listdir(f"{SYS}/class/hwmon")):
            if read(f"{SYS}/class/hwmon/{hw}/name") == "nvme":
                v = read_num(f"{SYS}/class/hwmon/{hw}/temp1_input")
                if v is not None:
                    return {"nvme_temp_c": round(v / 1000, 1)}
    except Exception:
        pass
    return {"nvme_temp_c": None}


# ---------------------------------------------------------------- /health/updates
@cached(30)
def health_updates():
    if MOCK:
        return {"updates": 7, "security_updates": 2, "reboot_required": "no", "reboot_pkgs": None}
    txt = read(f"{HOSTFS}/var/lib/update-notifier/updates-available")
    updates = sec = None
    if txt is not None:
        updates, sec = 0, 0
        for line in txt.splitlines():
            m = re.match(r"\s*(\d+)\s", line)
            if not m:
                continue
            if "security" in line.lower():
                sec = int(m.group(1))
            elif "can be applied" in line or "updates can be" in line or "update can be" in line:
                updates = int(m.group(1))
    rr = None
    for p in (f"{HOSTFS}/run/reboot-required", f"{HOSTFS}/var/run/reboot-required"):
        if os.path.isdir(os.path.dirname(p)):
            rr = os.path.exists(p)
            if rr:
                break
    pkgs = read(f"{HOSTFS}/run/reboot-required.pkgs")
    return {"updates": updates, "security_updates": sec,
            "reboot_required": None if rr is None else ("yes" if rr else "no"),
            "reboot_pkgs": ", ".join(pkgs.split()) if pkgs else None}


# ---------------------------------------------------------------- energy DB + live.json
def db_rows(since):
    if MOCK:
        rows, d = [], since
        today = date.today()
        rnd = random.Random(42)
        while d < today:
            cpu = round(rnd.uniform(0.04, 0.09), 3)
            rows.append({"date": d.isoformat(), "cpu_kwh": cpu, "total_kwh": round(cpu + 0.216, 3),
                         "cost_inr": round((cpu + 0.216) * RATE_INR, 2),
                         "rx_bytes": int(rnd.uniform(1.5e9, 9e9)), "tx_bytes": int(rnd.uniform(0.4e9, 3e9)), "hours": 24})
            d += timedelta(days=1)
        return rows
    if not os.path.exists(DB_PATH):
        return None
    uri = f"file:{DB_PATH}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
        con.execute("select 1 from daily_reports limit 1")
    except sqlite3.Error:
        con = sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True, timeout=2)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute("select date, cpu_kwh, total_kwh, cost_inr, rx_bytes, tx_bytes, hours "
                          "from daily_reports where date >= ? order by date", (since.isoformat(),))
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def live():
    if MOCK:
        now = datetime.now()
        h = now.hour + now.minute / 60
        return {"date": date.today().isoformat(), "cpu_kwh_so_far": round(0.0028 * h, 4),
                "total_kwh_so_far": round(0.0118 * h, 4), "cost_so_far": round(0.059 * h, 2),
                "rx_bytes": int(2.2e8 * h), "tx_bytes": int(6e7 * h), "hours": round(h, 2),
                "updated_at": now.astimezone().isoformat(timespec="seconds")}
    try:
        with open(LIVE_PATH) as f:
            d = json.load(f)
        return d if d.get("date") == date.today().isoformat() else None
    except Exception:
        return None


def _f(v):
    return float(v) if v is not None else 0.0


@cached(30)
def energy_data():
    today = date.today()
    start = min((today.replace(day=1) - timedelta(days=1)).replace(day=1), today - timedelta(days=31))
    rows = db_rows(start)
    return {"rows": rows, "live": live()}


@cached(30)
def energy():
    d = energy_data()
    rows, lv = d["rows"], d["live"]
    today = date.today()
    yday = (today - timedelta(days=1)).isoformat()
    month_prefix = today.strftime("%Y-%m")
    y = next((r for r in rows or [] if r["date"] == yday), None)
    month_rows = [r for r in rows or [] if r["date"].startswith(month_prefix) and r["date"] != today.isoformat()]
    res = {
        "today_kwh": round(lv["total_kwh_so_far"], 3) if lv else None,
        "today_cost_inr": round(lv.get("cost_so_far", lv["total_kwh_so_far"] * RATE_INR), 2) if lv else None,
        "yesterday_kwh": round(_f(y["total_kwh"]), 3) if y else None,
        "yesterday_cost_inr": round(_f(y["cost_inr"]), 2) if y else None,
        "updated_at": lv.get("updated_at") if lv else None,
    }
    if rows is None and not lv:
        res.update(month_kwh=None, month_cost_inr=None, projected_bill_inr=None, projected_kwh=None)
        return res
    m_kwh = sum(_f(r["total_kwh"]) for r in month_rows) + (lv["total_kwh_so_far"] if lv else 0)
    m_cost = sum(_f(r["cost_inr"]) for r in month_rows) + (res["today_cost_inr"] or 0)
    elapsed_days = len(month_rows) + ((lv.get("hours") or 0) / 24 if lv else 0)
    dim = calendar.monthrange(today.year, today.month)[1]
    proj_kwh = m_kwh / elapsed_days * dim if elapsed_days > 0.1 else None
    res.update(month_kwh=round(m_kwh, 2), month_cost_inr=round(m_cost, 2),
               projected_kwh=round(proj_kwh, 1) if proj_kwh else None,
               projected_bill_inr=round(proj_kwh * RATE_INR, 0) if proj_kwh else None)
    return res


@cached(30)
def traffic():
    d = energy_data()
    rows, lv = d["rows"], d["live"]
    today = date.today()
    yday = (today - timedelta(days=1)).isoformat()
    y = next((r for r in rows or [] if r["date"] == yday), None)
    prefix = today.strftime("%Y-%m")
    month = [r for r in rows or [] if r["date"].startswith(prefix) and r["date"] != today.isoformat()]
    t_rx = int(lv["rx_bytes"]) if lv and lv.get("rx_bytes") is not None else None
    t_tx = int(lv["tx_bytes"]) if lv and lv.get("tx_bytes") is not None else None
    res = {
        "today_rx": t_rx, "today_tx": t_tx,
        "today_total": (t_rx or 0) + (t_tx or 0) if t_rx is not None or t_tx is not None else None,
        "yesterday_rx": int(_f(y["rx_bytes"])) if y else None,
        "yesterday_tx": int(_f(y["tx_bytes"])) if y else None,
        "yesterday_total": int(_f(y["rx_bytes"]) + _f(y["tx_bytes"])) if y else None,
    }
    if rows is None and not lv:
        res.update(month_rx=None, month_tx=None, month_total=None)
    else:
        mrx = int(sum(_f(r["rx_bytes"]) for r in month)) + (t_rx or 0)
        mtx = int(sum(_f(r["tx_bytes"]) for r in month)) + (t_tx or 0)
        res.update(month_rx=mrx, month_tx=mtx, month_total=mrx + mtx)
    return res


# ---------------------------------------------------------------- docker (socket, read-only use)
class UnixConn(http.client.HTTPConnection):
    def __init__(self, path, timeout=5):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def docker_get(path, timeout=5):
    c = UnixConn(DOCKER_SOCK, timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        if r.status != 200:
            raise RuntimeError(f"docker {path}: HTTP {r.status}")
        return json.loads(body)
    finally:
        c.close()


@cached(300)
def docker_df():
    if MOCK:
        return {"images": 9.8e9, "containers": 1.2e8, "volumes": 3.1e9, "build_cache": 2.4e9, "reclaimable": 4.6e9}
    df = docker_get("/system/df", timeout=30)
    imgs = df.get("Images") or []
    img_size = sum(i.get("Size", 0) for i in imgs)
    img_recl = sum(i.get("Size", 0) for i in imgs if i.get("Containers", 0) == 0)
    cont = sum(c.get("SizeRw", 0) or 0 for c in df.get("Containers") or [])
    vols = df.get("Volumes") or []
    vol = sum((v.get("UsageData") or {}).get("Size", 0) or 0 for v in vols)
    vol_recl = sum((v.get("UsageData") or {}).get("Size", 0) or 0 for v in vols
                   if (v.get("UsageData") or {}).get("RefCount", 1) == 0)
    bc = df.get("BuildCache") or []
    bc_size = sum(b.get("Size", 0) for b in bc)
    bc_recl = sum(b.get("Size", 0) for b in bc if not b.get("InUse"))
    return {"images": img_size, "containers": cont, "volumes": vol, "build_cache": bc_size,
            "reclaimable": img_recl + vol_recl + bc_recl}


@cached(30)
def docker():
    if MOCK:
        names = [("pvt-discord-bot", 62e6, 0), ("mc-discord-bot", 58e6, 1), ("mcpanel", 41e6, 0),
                 ("beszel", 24e6, 0), ("beszel-agent", 12e6, 0), ("immich_server", 410e6, 0),
                 ("immich_postgres", 120e6, 0), ("immich_redis", 9e6, 0), ("extraction-service", 35e6, 2),
                 ("homelab-dashboard", 24e6, 0)]
        cs = []
        for n, m, r in names:
            m = int(jitter(m))
            cs.append({"name": n, "state": "running", "status": "Up 12 days", "uptime": "12d 4h", "restarts": r,
                       "mem_bytes": m, "mem": human_bytes(m), "summary": f"up 12d 4h · {human_bytes(m)} · {r} restarts"})
        cs[8].update(state="exited", status="Exited (0) 3 days ago", uptime=None, mem_bytes=0, mem="0 B",
                     summary="exited")
    else:
        cs = []
        for c in docker_get("/containers/json?all=1"):
            name = (c.get("Names") or ["?"])[0].lstrip("/")
            item = {"name": name, "state": c.get("State"), "status": c.get("Status"),
                    "uptime": None, "restarts": None, "mem_bytes": None, "mem": None}
            try:
                info = docker_get(f"/containers/{c['Id']}/json")
                item["restarts"] = info.get("RestartCount")
                started = (info.get("State") or {}).get("StartedAt")
                if item["state"] == "running" and started:
                    st = datetime.fromisoformat(started[:19] + "+00:00")
                    item["uptime"] = human_secs((datetime.now().astimezone() - st).total_seconds())
                if item["state"] == "running":
                    s = docker_get(f"/containers/{c['Id']}/stats?stream=false&one-shot=true")
                    ms = s.get("memory_stats") or {}
                    st = ms.get("stats") or {}
                    used = (ms.get("usage") or 0) - st.get("inactive_file", st.get("total_inactive_file", 0))
                    item["mem_bytes"] = max(used, 0)
                    item["mem"] = human_bytes(item["mem_bytes"])
            except Exception:
                pass
            if item["state"] == "running":
                item["summary"] = f"up {item['uptime']} · {item['mem']} · {item['restarts']} restarts"
            else:
                item["summary"] = item["status"]
            cs.append(item)
        cs.sort(key=lambda x: (x["state"] != "running", x["name"]))
    df = docker_df()
    running = sum(1 for c in cs if c["state"] == "running")
    return {"running": running, "total": len(cs), "containers": cs,
            "disk_images": df.get("images"), "disk_volumes": df.get("volumes"),
            "disk_build_cache": df.get("build_cache"), "disk_reclaimable": df.get("reclaimable"),
            "disk_total": sum(df.get(k) or 0 for k in ("images", "containers", "volumes", "build_cache"))
            if "error" not in df else None}


# ---------------------------------------------------------------- Immich
def http_json(url, headers, timeout=8):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def sum_keys(obj, keys):
    total = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, (int, float)):
                total += v
            else:
                total += sum_keys(v, keys)
    elif isinstance(obj, list):
        total += sum(sum_keys(x, keys) for x in obj)
    return total


@cached(30)
def immich():
    if MOCK:
        return {"photos": 18342, "videos": 611, "usage_bytes": int(96.4e9), "disk_used_percent": 60.7,
                "disk_free": "92.0 GB", "queue_active": 0, "queue_waiting": random.choice([0, 0, 12])}
    if not IMMICH_KEY:
        return {"photos": None, "videos": None, "usage_bytes": None, "disk_used_percent": None,
                "disk_free": None, "queue_active": None, "queue_waiting": None, "error": "IMMICH_API_KEY not set"}
    h = {"x-api-key": IMMICH_KEY}
    res = {}
    try:
        st = http_json(f"{IMMICH_URL}/api/server/statistics", h)
        res.update(photos=st.get("photos"), videos=st.get("videos"), usage_bytes=st.get("usage"))
    except Exception:
        res.update(photos=None, videos=None, usage_bytes=None)
    try:
        sto = http_json(f"{IMMICH_URL}/api/server/storage", h)
        res.update(disk_used_percent=sto.get("diskUsagePercentage"), disk_free=sto.get("diskAvailable"))
    except Exception:
        res.update(disk_used_percent=None, disk_free=None)
    q = None
    for path in ("/api/queues", "/api/jobs"):
        try:
            q = http_json(f"{IMMICH_URL}{path}", h)
            break
        except Exception:
            continue
    res["queue_active"] = sum_keys(q, {"active"}) if q is not None else None
    res["queue_waiting"] = sum_keys(q, {"waiting", "delayed", "paused"}) if q is not None else None
    return res


# ---------------------------------------------------------------- GitHub
_gh_repos = {"t": 0, "repos": []}


def gh(path):
    return http_json(f"https://api.github.com{path}",
                     {"Authorization": f"Bearer {GH_TOKEN}", "X-GitHub-Api-Version": "2022-11-28",
                      "User-Agent": "homelab-dashboard"}, timeout=10)


@cached(60)
def github():
    if MOCK:
        t = time.time() - 3 * 3600
        return {"runner": GH_RUNNER, "runner_status": random.choice(["idle", "idle", "busy"]),
                "last_run_repo": "homelab-dashboard", "last_run_workflow": "Deploy",
                "last_run_status": "success", "last_run_at": iso(t), "last_run_url": f"https://github.com/{GH_ORG}"}
    out = {"runner": GH_RUNNER, "runner_status": None, "last_run_repo": None, "last_run_workflow": None,
           "last_run_status": None, "last_run_at": None, "last_run_url": None}
    if not GH_TOKEN:
        out["error"] = "DASH_GITHUB_TOKEN not set"
        return out
    try:
        runners = gh(f"/orgs/{GH_ORG}/actions/runners?per_page=100").get("runners", [])
        r = next((x for x in runners if x.get("name") == GH_RUNNER), runners[0] if runners else None)
        if r:
            out["runner"] = r.get("name")
            out["runner_status"] = "offline" if r.get("status") != "online" else ("busy" if r.get("busy") else "idle")
    except Exception as e:
        out["error"] = f"runners: {str(e)[:100]}"
    try:
        if time.time() - _gh_repos["t"] > 600:
            _gh_repos.update(t=time.time(), repos=[x["name"] for x in gh(f"/orgs/{GH_ORG}/repos?per_page=100&sort=pushed")
                                                   if not x.get("archived")][:15])
        best = None
        for repo in _gh_repos["repos"]:
            try:
                runs = gh(f"/repos/{GH_ORG}/{repo}/actions/runs?per_page=1").get("workflow_runs", [])
            except Exception:
                continue
            if runs and (best is None or runs[0]["updated_at"] > best["updated_at"]):
                best = runs[0]
        if best:
            out.update(last_run_repo=best["repository"]["name"], last_run_workflow=best.get("name"),
                       last_run_status=best.get("conclusion") or best.get("status"),
                       last_run_at=best.get("updated_at"), last_run_url=best.get("html_url"))
    except Exception as e:
        out["error"] = f"runs: {str(e)[:100]}"
    return out


# ---------------------------------------------------------------- security
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
FAIL_RE = re.compile(r"sshd.*(Failed password|Invalid user|authentication failure|Failed publickey)")


def log_time(line, now):
    try:
        if line[:4].isdigit():  # RFC3339 (Ubuntu 23.10+ rsyslog default)
            return datetime.fromisoformat(line.split(" ", 1)[0]).timestamp()
        mon, day, hms = line[:15].split()
        dt = datetime(now.year, MONTHS[mon], int(day), *map(int, hms.split(":")))
        if dt > now + timedelta(days=1):
            dt = dt.replace(year=now.year - 1)
        return dt.timestamp()
    except Exception:
        return None


def tail(path, nbytes=4 * 1024 * 1024):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        return f.read().decode("utf-8", "replace").splitlines()


@cached(30)
def security():
    if MOCK:
        return {"failed_ssh_24h": random.choice([0, 3, 14]), "ufw": "enabled", "playit": "running"}
    now = datetime.now()
    cutoff = time.time() - 86400
    failed = None
    for p in (f"{HOSTFS}/var/log/auth.log.1", f"{HOSTFS}/var/log/auth.log"):
        try:
            lines = tail(p)
        except Exception:
            continue
        failed = failed or 0
        for line in lines:
            if FAIL_RE.search(line):
                t = log_time(line, now)
                if t and t >= cutoff:
                    failed += 1
    conf = read(f"{HOSTFS}/etc/ufw/ufw.conf")
    ufw = None
    if conf is not None:
        m = re.search(r"^\s*ENABLED\s*=\s*(\w+)", conf, re.M)
        ufw = "enabled" if m and m.group(1).lower() == "yes" else "disabled"
    procs = list_procs()
    playit = "running" if any(c.startswith("playit") for _, c, _ in procs) else ("stopped" if procs else None)
    return {"failed_ssh_24h": failed, "ufw": ufw, "playit": playit}


# ---------------------------------------------------------------- energy tracker service status
@cached(30)
def tracker():
    if MOCK:
        return {"status": "running", "last_update": iso(time.time() - 20)}
    lv = live()
    running = cmdline_match("energy_report")
    fresh = False
    if lv and lv.get("updated_at"):
        try:
            fresh = time.time() - datetime.fromisoformat(lv["updated_at"]).timestamp() < 180
        except Exception:
            pass
    status = "running" if running and (fresh or lv is None) else ("stale" if running else "stopped")
    if running is None:
        status = None
    return {"status": status, "last_update": lv.get("updated_at") if lv else None}


# ---------------------------------------------------------------- status helpers
OK, WARN, BAD, UNK = "🟢", "🟡", "🔴", "⚪"


def lvl(v, warn, bad, higher_is_worse=True):
    if v is None:
        return UNK
    if higher_is_worse:
        return BAD if v >= bad else WARN if v >= warn else OK
    return BAD if v <= bad else WARN if v <= warn else OK


def ok(d):
    return isinstance(d, dict) and "error" not in d


# ---------------------------------------------------------------- attention list (Home banner)
IGNORE_STOPPED = {x.strip() for x in os.environ.get("IGNORE_STOPPED_CONTAINERS", "extraction-service").split(",") if x.strip()}


def attention_items():
    """Everything that needs a look, worst first: [(level, what, detail)]."""
    items = []
    s = health_system()
    s = s if ok(s) else {}
    t = s.get("cpu_temp_c")
    if t is not None and t >= 75:
        items.append((lvl(t, 75, 90), "Processor is hot", f"{t:.0f} °C"))
    for key, name, warn, bad in (("ram_percent", "Memory almost full", 80, 92), ("disk_percent", "Disk almost full", 80, 90)):
        v = s.get(key)
        if v is not None and v >= warn:
            items.append((lvl(v, warn, bad), name, f"{v:.0f} % used"))
    ssd = health_ssd()
    t = ssd.get("nvme_temp_c") if ok(ssd) else None
    if t is not None and t >= 60:
        items.append((lvl(t, 60, 70), "SSD is hot", f"{t:.0f} °C"))
    b = health_battery()
    if ok(b) and (b.get("status") or "").lower() == "discharging":
        items.append((BAD, "Running on battery", f"{b.get('charge_percent') or '?'} % left, check the charger / power"))
    u = health_updates()
    if ok(u):
        if u.get("reboot_required") == "yes":
            items.append((WARN, "Restart needed", u.get("reboot_pkgs") or "after updates"))
        if u.get("security_updates"):
            items.append((WARN, "Security updates waiting", f"{u['security_updates']} of {u.get('updates')} updates"))
    sec = security()
    if ok(sec):
        f = sec.get("failed_ssh_24h")
        if f:
            items.append((lvl(f, 1, 20), "Failed SSH logins", f"{f} in the last 24 h"))
        if sec.get("ufw") == "disabled":
            items.append((BAD, "Firewall is off", "ufw disabled"))
        if sec.get("playit") == "stopped":
            items.append((BAD, "playit tunnel is down", "playitd not running"))
    d = docker()
    if ok(d):
        for c in d["containers"]:
            if c["state"] != "running" and c["name"] not in IGNORE_STOPPED:
                items.append((BAD, f"{c['name']} is down", c.get("status") or "stopped"))
    tr = tracker()
    if ok(tr) and tr.get("status") in ("stopped", "stale"):
        items.append((BAD if tr["status"] == "stopped" else WARN, "Energy tracker",
                      "stopped" if tr["status"] == "stopped" else "no new reading"))
    g = github()
    if isinstance(g, dict):
        if g.get("runner_status") == "offline":
            items.append((BAD, "GitHub runner offline", g.get("runner") or ""))
        if g.get("last_run_status") in ("failure", "timed_out"):
            items.append((BAD, "Last deploy failed", g.get("last_run_repo") or ""))
    order = {BAD: 0, WARN: 1}
    return [(lv, what, detail, tab_for(what)) for lv, what, detail in sorted(items, key=lambda x: order.get(x[0], 2))]


def tab_for(what):
    w = what.lower()
    if " is down" in w:
        return "docker"
    if "ssh" in w or "firewall" in w or "playit" in w:
        return "security"
    if "tracker" in w or "runner" in w or "deploy" in w:
        return "bots"
    return "health"



ROUTES = {
    "/health/system": health_system, "/health/battery": health_battery, "/health/ssd": health_ssd,
    "/health/updates": health_updates, "/energy": energy, "/traffic": traffic, "/docker": docker,
    "/immich": immich, "/github": github, "/security": security, "/tracker": tracker,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "server-api"

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path in STATIC:
                body, ctype = static_file(path)
            elif path.startswith("/panel/") and path[7:] in PANEL_RENDERERS:
                body, ctype = PANEL_RENDERERS[path[7:]]().encode(), "text/html; charset=utf-8"
            elif path == "/healthz":
                body, ctype = b'{"ok":true}', "application/json"
            elif path in ROUTES:
                body, ctype = json.dumps(ROUTES[path]()).encode(), "application/json"
            else:
                self.send_error(404)
                return
        except Exception as e:
            body, ctype = json.dumps({"error": str(e)[:200]}).encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


# ---------------------------------------------------------------- 24 h history (in memory) + panel files
HISTORY = deque(maxlen=1440)  # one sample per minute: (ts, cpu %, ram %, cpu temp)


def sample_history():
    s = health_system()
    if ok(s):
        HISTORY.append((time.time(), s.get("cpu_percent"), s.get("ram_percent"), s.get("cpu_temp_c")))


def mock_history():
    now = time.time()
    rnd = random.Random(7)
    out = []
    for i in range(288):  # 24 h at 5 min
        t = now - (287 - i) * 300
        hour = datetime.fromtimestamp(t).hour
        busy = 1.0 if 18 <= hour <= 23 else 0.4  # evenings: Minecraft
        cpu = max(2, 8 + 18 * busy + rnd.uniform(-5, 9))
        out.append((t, round(cpu, 1), round(50 + 6 * busy + rnd.uniform(-2, 2), 1), round(44 + cpu * 0.35 + rnd.uniform(-1, 1), 1)))
    return out


def history():
    if MOCK:
        return mock_history()
    h = list(HISTORY)
    return h[::max(1, len(h) // 240)]  # ~240 points is plenty for a 640 px chart


def _rows_with_live():
    d = energy_data()
    return d.get("rows") or [], d.get("live")


PANEL_RENDERERS = {
    "health": lambda: panels.panel_health(health_system(), health_battery(), health_ssd(), health_updates(), history()),
    "power": lambda: panels.panel_power(energy(), *_rows_with_live(), RATE_INR),
    "traffic": lambda: panels.panel_traffic(traffic(), *_rows_with_live()),
    "immich": lambda: panels.panel_immich(immich()),
    "bots": lambda: panels.panel_bots(docker(), tracker(), github()),
    "docker": lambda: panels.panel_docker(docker(), IGNORE_STOPPED),
    "security": lambda: panels.panel_security(security()),
    "home": lambda: panels.panel_home(health_system(), health_battery(), history(), energy(), *_rows_with_live(),
                                      traffic(), docker(), immich(), github(), attention_items(), IGNORE_STOPPED),
    "links": lambda: panels.panel_links(),
}


def sampler():
    """One history sample per minute for the Server health charts (kept in memory, 24 h)."""
    while True:
        try:
            sample_history()
        except Exception as e:
            print(f"history sample failed: {e}", flush=True)
        time.sleep(60)


WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml")}


def static_file(path):
    name, ctype = STATIC[path]  # fixed whitelist: no path traversal possible
    with open(os.path.join(WEB, name), "rb") as f:
        return f.read(), ctype


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    print(f"server-api on :{port} (MOCK={MOCK})", flush=True)
    threading.Thread(target=sampler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
