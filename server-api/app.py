"""Read-only JSON API for Homepage customapi widgets. Python stdlib only."""
import calendar
import hashlib
import hmac
import http.client
import json
import os
import random
import re
import secrets
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import incidents
import links
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
DATA_DIR = os.environ.get("DATA_DIR", "/data")
ADMIN_PIN = (os.environ.get("DASHBOARD_ADMIN_PIN") or "").strip()  # unset: dashboard stays read-only
NTFY = incidents.Notifier(os.environ.get("NTFY_URL"), os.environ.get("NTFY_TOKEN"),
                          os.environ.get("NTFY_TOPIC") or "server-alerts",
                          os.environ.get("DASHBOARD_PUBLIC_URL") or "http://192.168.31.125:3000")

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
            "top_processes": [{"name": "java", "cpu": 6.2}, {"name": "immich", "cpu": 3.1}, {"name": "python3", "cpu": 1.4},
                              {"name": "dockerd", "cpu": 0.9}, {"name": "beszel-agent", "cpu": 0.4}],
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
    top = sorted(usage.items(), key=lambda kv: -kv[1])[:5]
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
def parse_updates(txt):
    """update-notifier's motd text -> (regular updates, of which security, extra ESM fixes).

    "0 updates can be applied immediately."
    "2 of these updates are standard security updates."
    "4 additional security updates can be applied with ESM Apps."   <- needs Ubuntu Pro, not pending
    Older releases: "5 packages can be updated." / "2 updates are security updates."
    """
    updates = sec = esm = 0
    for line in txt.splitlines():
        m = re.match(r"\s*(\d+)\s", line)
        if not m:
            continue
        n, low = int(m.group(1)), line.lower()
        if "esm" in low or "ubuntu pro" in low:
            esm += n
        elif "can be applied immediately" in low or "packages can be updated" in low or "package can be updated" in low:
            updates = n
        elif "security" in low:
            sec = n
    return updates, sec, esm


@cached(30)
def health_updates():
    if MOCK:
        return {"updates": 7, "security_updates": 2, "esm_updates": 4, "reboot_required": "no", "reboot_pkgs": None}
    txt = read(f"{HOSTFS}/var/lib/update-notifier/updates-available")
    updates = sec = esm = None
    if txt is not None:
        updates, sec, esm = parse_updates(txt)
    rr = None
    for p in (f"{HOSTFS}/run/reboot-required", f"{HOSTFS}/var/run/reboot-required"):
        if os.path.isdir(os.path.dirname(p)):
            rr = os.path.exists(p)
            if rr:
                break
    pkgs = read(f"{HOSTFS}/run/reboot-required.pkgs")
    return {"updates": updates, "security_updates": sec, "esm_updates": esm,
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
            rx, tx = rnd.uniform(0.4e9, 1.6e9), rnd.uniform(0.15e9, 0.6e9)  # internet-only
            rows.append({"date": d.isoformat(), "cpu_kwh": cpu, "total_kwh": round(cpu + 0.216, 3),
                         "cost_inr": round((cpu + 0.216) * RATE_INR, 2),
                         "rx_bytes": int(rx), "tx_bytes": int(tx), "hours": 24})
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
                "rx_bytes": int(4.2e7 * h), "tx_bytes": int(1.4e7 * h), "hours": round(h, 2),
                "traffic_scope": "internet", "updated_at": now.astimezone().isoformat(timespec="seconds")}
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
    dim = calendar.monthrange(today.year, today.month)[1]
    full = [r for r in rows or [] if r["date"] < today.isoformat()]
    if month_rows:   # month so far + average of this month's full days for the rest
        avg = sum(_f(r["total_kwh"]) for r in month_rows) / len(month_rows)
        proj_kwh = sum(_f(r["total_kwh"]) for r in month_rows) + avg * (dim - len(month_rows))
    elif full:       # 1st of the month: use the latest full days
        last = full[-7:]
        proj_kwh = sum(_f(r["total_kwh"]) for r in last) / len(last) * dim
    else:            # no full day recorded yet
        proj_kwh = None
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


def docker_raw(path, timeout=10):
    c = UnixConn(DOCKER_SOCK, timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        if r.status != 200:
            raise RuntimeError(f"docker {path.split('?')[0]}: HTTP {r.status}")
        return body
    finally:
        c.close()


def docker_events(since, until, container=None):
    """Container lifecycle events (die/oom/restart/health) between two unix times."""
    flt = {"type": ["container"], "event": ["die", "oom", "restart", "start", "stop", "kill", "health_status"]}
    if container:
        flt["container"] = [container]
    q = urllib.parse.urlencode({"since": int(since), "until": int(until), "filters": json.dumps(flt)})
    out = []
    for line in docker_raw(f"/events?{q}", timeout=10).splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        attrs = (e.get("Actor") or {}).get("Attributes") or {}
        out.append({"ts": e.get("timeNano", 0) / 1e9 or e.get("time", 0), "action": e.get("Action") or e.get("status") or "",
                    "name": attrs.get("name", ""), "exit": attrs.get("exitCode")})
    return out


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
                 ("homelab-dashboard", 18e6, 0), ("mc-2", 1.4e9, 0), ("mc-1", 0, 0), ("mc-server", 0, 0),
                 ("defender-staging", 0, 40), ("scratch-runner", 0, 0), ("nice_mcnulty", 0, 0)]
        cs = []
        for n, m, r in names:
            m = int(jitter(m))
            cs.append({"name": n, "state": "running", "status": "Up 12 days", "uptime": "12d 4h", "restarts": r,
                       "mem_bytes": m, "mem": human_bytes(m), "summary": f"up 12d 4h · {human_bytes(m)} · {r} restarts",
                       "world": n in ("mc-1", "mc-2"), "image": "itzg/minecraft-server" if n.startswith("mc-") else ""})
        for c in cs:
            c.update(exit_code=None, oom=False, health=None, health_output=None)
        for i, status, code in ((8, "Exited (0) 3 days ago", 0), (11, "Exited (0) 2 days ago", 0), (12, "Exited (143) 5 days ago", 143)):
            cs[i].update(state="exited", status=status, uptime=None, mem_bytes=0, mem="0 B", summary="exited", exit_code=code)
        cs[5].update(health="healthy")
        cs[0].update(state="restarting", status="Restarting (1) 40 seconds ago", uptime=None, restarts=7, exit_code=1,
                     mem_bytes=0, mem="0 B", summary="restarting")
        RESTART_SEEN.setdefault("pvt-discord-bot", deque([(time.time() - 600, 0)], maxlen=50))
        # defender-staging restarts all the time on purpose; Docker's state shows exit 0, its last stop was exit 1
        cs[13].update(state="restarting", status="Restarting (1) 12 seconds ago", uptime=None, exit_code=0, summary="restarting")
        RESTART_SEEN.setdefault("defender-staging", deque([(time.time() - 600, 30)], maxlen=50))
        if not any(e["name"] == "defender-staging" and time.time() - e["ts"] < 90 for e in RECENT_EVENTS):
            RECENT_EVENTS.append({"ts": time.time(), "action": "die", "name": "defender-staging", "exit": "1"})
        cs[14].update(state="exited", status="Exited (1) 1 hour ago", uptime=None, exit_code=1, summary="exited",
                      labels={"homelab.ignore": "true"})
        cs[15].update(state="exited", status="Exited (1) 20 seconds ago", uptime=None, exit_code=1, summary="exited",
                      image="python:3.13-alpine", ran_secs=3)
        for c in cs[:15]:
            c.setdefault("labels", {"com.docker.compose.project": "homelab"})
        cs[10]["uptime"] = "3h 12m"
    else:
        cs = []
        for c in docker_get("/containers/json?all=1"):
            name = (c.get("Names") or ["?"])[0].lstrip("/")
            item = {"name": name, "state": c.get("State"), "status": c.get("Status"),
                    "uptime": None, "restarts": None, "mem_bytes": None, "mem": None,
                    "world": (c.get("Labels") or {}).get("mcpanel") == "1", "image": c.get("Image") or "",
                    "labels": c.get("Labels") or {},
                    "exit_code": None, "oom": False, "health": None, "health_output": None}
            try:
                info = docker_get(f"/containers/{c['Id']}/json")
                item["restarts"] = info.get("RestartCount")
                stt = info.get("State") or {}
                item.update(exit_code=stt.get("ExitCode"), oom=bool(stt.get("OOMKilled")))
                try:
                    a, b = stt.get("StartedAt") or "", stt.get("FinishedAt") or ""
                    if a[:4] > "0001" and b[:4] > "0001":
                        item["ran_secs"] = (datetime.fromisoformat(b[:19] + "+00:00")
                                            - datetime.fromisoformat(a[:19] + "+00:00")).total_seconds()
                except ValueError:
                    pass
                hl = stt.get("Health") or {}
                if hl:
                    item["health"] = hl.get("Status")
                    last = (hl.get("Log") or [{}])[-1]
                    item["health_output"] = incidents.redact((last.get("Output") or "").strip())[-500:] or None
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
    for c in cs:  # stopped Minecraft worlds (MC Panel) and the manual ignore list are off on purpose
        c["ignored"] = ignored_container(c)  # "label" / "rule" / "mute" / None
        c["oneoff"] = is_oneoff(c)
        c["off_ok"] = c["state"] != "running" and (c["world"] or c["name"] in IGNORE_STOPPED or bool(c["ignored"])
                                                    or c["oneoff"])
    df = docker_df()
    running = sum(1 for c in cs if c["state"] == "running")
    down = [c["name"] for c in cs if c["state"] != "running" and not c["off_ok"]]
    return {"running": running, "total": len(cs), "containers": cs, "down": down,
            "expected": len(cs) - sum(1 for c in cs if c["off_ok"]),
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


RECENT_EVENTS = deque(maxlen=500)   # docker events seen by the monitor: {ts, action, name, exit}
RESTART_SEEN = {}                    # container -> deque[(ts, restart count)]
CRASH_WINDOW = 15 * 60


def problems():
    """Everything that needs a look, worst first. Drives the Home banner, incidents and phone alerts.

    priority: urgent (app down, crash loop, OOM) / high / low (at most one phone alert a day)."""
    out = []
    now = time.time()

    def add(key, kind, type_, target, level, priority, title, detail, tab, immediate=False, fingerprint=None):
        out.append({"key": key, "kind": kind, "type": type_, "target": target, "level": level, "priority": priority,
                    "title": title, "detail": detail, "tab": tab, "immediate": immediate,
                    "fingerprint": fingerprint or detail})

    d = docker()
    if ok(d):
        for c in d["containers"]:
            name = c["name"]
            hist = RESTART_SEEN.setdefault(name, deque(maxlen=50))
            if c.get("restarts") is not None and (not hist or hist[-1][1] != c["restarts"]):
                hist.append((now, c["restarts"]))
            older = [r for t, r in hist if now - t > CRASH_WINDOW]
            base = older[-1] if older else (hist[0][1] if hist else 0)
            restarts_recent = (c.get("restarts") or 0) - base
            deaths = [e for e in RECENT_EVENTS if e["name"] == name and e["action"] == "die" and now - e["ts"] < CRASH_WINDOW]
            ooms = [e for e in RECENT_EVENTS if e["name"] == name and e["action"] == "oom" and now - e["ts"] < 600]
            if c.get("oneoff") or (c.get("off_ok") and not c.get("ignored")):
                continue  # one-off `docker run`, stopped MC Panel world or IGNORE_STOPPED_CONTAINERS
            code = last_exit(name, c.get("exit_code"))
            mark = len(out)
            if ooms or (c["state"] != "running" and c.get("oom")):
                add(f"oom:{name}", "container", "oom", name, BAD, "urgent", f"{name} ran out of memory",
                    "killed by the kernel (OOM)", "docker", immediate=True, fingerprint=f"oom:{len(ooms)}")
            elif c["state"] == "restarting" or len(deaths) >= 3 or restarts_recent >= 3:
                n = max(len(deaths), restarts_recent)
                what = f"{n} restarts in 15 min" if n >= 3 else f"restarting, {c.get('restarts') or 0} restarts so far"
                add(f"crash_loop:{name}", "container", "crash_loop", name, BAD, "urgent", f"{name} is crash-looping",
                    what + (f", exit code {code}" if code is not None else ""), "docker",
                    immediate=True, fingerprint=f"restarts:{c.get('restarts')}")
            elif c["state"] != "running":
                add(f"container_down:{name}", "container", "container_down", name, BAD, "urgent", f"{name} is down",
                    c.get("status") or "stopped", "docker", fingerprint=f"exit:{code}")
            elif c.get("health") == "unhealthy":
                add(f"unhealthy:{name}", "container", "unhealthy", name, BAD, "high", f"{name} is unhealthy",
                    (c.get("health_output") or "health check failing")[:160], "docker", fingerprint="unhealthy")
            for p in out[mark:]:
                if c.get("ignored"):
                    p["ignored"] = c["ignored"]  # shown grey on Home, never an incident or alert
    s = health_system()
    s = s if ok(s) else {}
    t = s.get("cpu_temp_c")
    if t is not None and t >= 80:
        add("cpu_hot", "metric", "cpu_hot", "CPU temperature", lvl(t, 80, 90), "high", "Processor is hot", f"{t:.0f} °C",
            "health", fingerprint=f"{t // 5}")
    for key, name, title, warn, bad in (("ram_percent", "ram_high", "Memory almost full", 90, 95),
                                         ("disk_percent", "disk_high", "Disk almost full", 85, 95)):
        v = s.get(key)
        if v is not None and v >= warn:
            add(name, "metric", name, "RAM" if name == "ram_high" else "Disk", lvl(v, warn, bad), "high", title,
                f"{v:.0f} % used", "health", fingerprint=f"{v // 5}")
    ssd = health_ssd()
    t = ssd.get("nvme_temp_c") if ok(ssd) else None
    if t is not None and t >= 65:
        add("ssd_hot", "metric", "ssd_hot", "SSD temperature", lvl(t, 65, 75), "high", "SSD is hot", f"{t:.0f} °C", "health",
            fingerprint=f"{t // 5}")
    b = health_battery()
    if ok(b) and (b.get("status") or "").lower() == "discharging":
        add("on_battery", "system", "on_battery", "Battery", BAD, "high", "Running on battery",
            f"{b.get('charge_percent') or '?'} % left, check the charger / power", "health", fingerprint="batt")
    u = health_updates()
    if ok(u):
        if u.get("reboot_required") == "yes":
            add("reboot", "system", "reboot", "Ubuntu", WARN, "low", "Restart needed", u.get("reboot_pkgs") or "after updates", "health")
        n, sec_n = u.get("updates") or 0, u.get("security_updates") or 0
        if n:
            add("updates", "system", "updates", "Ubuntu", WARN, "low", "Updates waiting",
                f"{n} update{'s' if n != 1 else ''}" + (f", {sec_n} security" if sec_n else ""), "health", fingerprint="updates")
    sec = security()
    if ok(sec):
        f = sec.get("failed_ssh_24h")
        if f:
            add("ssh_failed", "system", "ssh_failed", "SSH", lvl(f, 1, 20), "high" if f >= 20 else "low", "Failed SSH logins",
                f"{f} in the last 24 h", "security", fingerprint="many" if f >= 20 else "few")
        if sec.get("ufw") == "disabled":  # maintenance item: Home banner only, never an incident or alert
            add("firewall_off", "system", "firewall_off", "ufw", WARN, "low", "Firewall is off", "ufw disabled", "security")
        if sec.get("playit") == "stopped":
            add("playit_down", "system", "playit_down", "playitd", BAD, "high", "playit tunnel is down", "playitd not running", "security")
    tr = tracker()
    if ok(tr) and tr.get("status") in ("stopped", "stale"):
        stopped = tr["status"] == "stopped"
        add("tracker", "system", "tracker", "energy tracker", BAD if stopped else WARN, "high" if stopped else "low",
            "Energy tracker " + ("stopped" if stopped else "is stuck"), "stopped" if stopped else "no new reading", "bots",
            fingerprint=tr["status"])
    g = github()
    if isinstance(g, dict):
        if g.get("runner_status") == "offline":
            add("runner_offline", "system", "runner_offline", g.get("runner") or "runner", BAD, "high", "GitHub runner offline",
                g.get("runner") or "", "bots")
        if g.get("last_run_status") in ("failure", "timed_out"):
            add("deploy_failed", "system", "deploy_failed", g.get("last_run_repo") or "deploy", BAD, "high", "Last deploy failed",
                g.get("last_run_repo") or "", "bots", fingerprint=g.get("last_run_at"))
    order = {BAD: 0, WARN: 1}
    for p in out:
        if not p.get("ignored") and muted(p, now):
            p["ignored"] = "mute"
    return sorted(out, key=lambda p: order.get(p["level"], 2))


def last_exit(name, fallback=None):
    """Exit code of the container's most recent stop (Docker's current state can say 0 while it crash-loops)."""
    for e in reversed(RECENT_EVENTS):
        if e["name"] == name and e["action"] == "die" and e.get("exit") not in (None, ""):
            try:
                return int(e["exit"])
            except ValueError:
                break
    return fallback


def important(problems_):
    """Only problems worth interrupting for. Low-priority ones (updates waiting, restart needed, a few failed
    SSH logins, a stuck tracker) stay visible on their own tabs, but never alert, pin or open an incident."""
    return [p for p in problems_ if p["priority"] != "low"]


def incident_problems():
    """What the incident monitor sees: important, and not ignored or muted."""
    return [p for p in important(problems()) if not p.get("ignored")]


def attention_items():
    """Home banner: (level, what, detail, tab). Everything stays visible here, including maintenance items
    (updates, restart needed, firewall); ignored/muted ones are grey (level UNK) with a tag."""
    quiet = {i["key"] for i in STORE.list(status="open", ignored=True)}
    out = []
    for p in problems():
        if p.get("ignored") or p["key"] in quiet:
            why = {"label": "ignored by label", "mute": "muted"}.get(p.get("ignored"), "ignored")
            out.append((UNK, p["title"], f"{p['detail']} · {why}", p["tab"]))
        else:
            out.append((p["level"], p["title"], p["detail"], p["tab"]))
    return out


ONEOFF_NAME = re.compile(r"^[a-z]+_[a-z]+$")  # Docker's random "adjective_surname" names


def is_oneoff(c):
    """A one-off `docker run` / `--rm`: random name, no compose project, stopped after running under 2 min."""
    if (c.get("labels") or {}).get("com.docker.compose.project"):
        return False
    if not ONEOFF_NAME.match(c["name"]) or c["state"] == "running" or c["state"] == "restarting":
        return False
    ran = c.get("ran_secs")
    return ran is not None and ran < 120


# ---------------------------------------------------------------- ignore / mute rules
def _rules_now():
    try:
        return STORE.rules()
    except NameError:  # during import, before STORE exists
        return []


def ignored_container(c):
    """'label' (homelab.ignore=true), 'rule' (Ignore this container), 'mute' (muted for a while) or None."""
    if str((c.get("labels") or {}).get("homelab.ignore", "")).lower() in ("true", "1", "yes"):
        return "label"
    for r in _rules_now():
        if r["kind"] == "container" and r["value"] == c["name"]:
            return "rule"
        if r["kind"] == "mute" and r["value"] == f"container:{c['name']}":
            return "mute"
    return None


def muted(p, now=None):
    for r in _rules_now():
        if r["kind"] == "mute" and r["value"] == f"key:{p['key']}":
            return True
        if r["kind"] in ("container", "mute") and p["kind"] == "container" and r["value"] in (p["target"], f"container:{p['target']}"):
            return True
    return False


def error_rule(headline):
    """Rule id that ignores this error line everywhere, or 0."""
    norm = incidents.normalize_error(headline) if headline else None
    for r in _rules_now():
        if r["kind"] == "error" and norm and r["value"] == norm:
            return r["id"]
    return 0


MUTE_HOURS = {1: "1 h", 8: "8 h", 24: "24 h", 168: "7 days"}


def add_rule_from_incident(iid, kind, hours=None):
    """Create an ignore/mute rule from an incident (the server decides what it matches, not the browser)."""
    inc = STORE.get(iid)
    if not inc:
        raise ValueError("incident not found")
    now = time.time()
    if kind == "container":
        if inc["kind"] != "container":
            raise ValueError("not a container incident")
        name = inc["target"]
        rid = STORE.add_rule("container", name, f"Ignore container {name}")
        STORE.mark_ignored(rid, "kind='container' AND target=?", (name,))
    elif kind == "error":
        if not inc.get("headline"):
            raise ValueError("this incident has no error line")
        norm = incidents.normalize_error(inc["headline"])
        rid = STORE.add_rule("error", norm, f"Ignore error: {inc['headline'][:160]}")
        ids = [i for i, h in STORE.headlines() if incidents.normalize_error(h) == norm]
        if ids:
            STORE.mark_ignored(rid, f"id IN ({','.join('?' * len(ids))})", ids)
    elif kind == "mute":
        if hours not in MUTE_HOURS:
            raise ValueError("unsupported mute length")
        scope = f"container:{inc['target']}" if inc["kind"] == "container" else f"key:{inc['key']}"
        what = inc["target"] if inc["kind"] == "container" else inc["title"]
        rid = STORE.add_rule("mute", scope, f"Muted {what} for {MUTE_HOURS[hours]}", until=now + hours * 3600)
        if inc["kind"] == "container":
            STORE.mark_ignored(rid, "status='open' AND kind='container' AND target=?", (inc["target"],))
        else:
            STORE.mark_ignored(rid, "status='open' AND key=?", (inc["key"],))
    else:
        raise ValueError("unknown rule type")
    _cache.pop("docker", None)
    return rid


# ---------------------------------------------------------------- admin PIN (protects the ignore controls)
ADMIN_COOKIE = "dash_admin"
ADMIN_DAYS = 30
_fails = {}            # client ip -> [failure times]
_fails_all = deque(maxlen=200)
_fail_lock = threading.Lock()


def _auth_key():
    k = STORE.kv_get("auth_secret")
    if not k:
        k = secrets.token_hex(32)
        STORE.kv_set("auth_secret", k)
    # the PIN is part of the key, so changing DASHBOARD_ADMIN_PIN signs everyone out
    return hmac.new(k.encode(), hashlib.sha256(ADMIN_PIN.encode()).digest(), hashlib.sha256).digest()


def issue_token(now=None):
    exp = int((now or time.time()) + ADMIN_DAYS * 86400)
    sig = hmac.new(_auth_key(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(token, now=None):
    if not ADMIN_PIN or not token or "." not in token:
        return False
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or int(exp) < (now or time.time()):
        return False
    good = hmac.new(_auth_key(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def admin_state(headers):
    """'off' (no PIN configured), 'locked' or 'on' for this browser."""
    if not ADMIN_PIN:
        return "off"
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == ADMIN_COOKIE and token_ok(v):
            return "on"
    return "locked"


def pin_attempt_allowed(ip, now=None):
    now = now or time.time()
    with _fail_lock:
        mine = [t for t in _fails.get(ip, []) if now - t < 900]
        _fails[ip] = mine
        recent_all = [t for t in _fails_all if now - t < 900]
        return len(mine) < 5 and len(recent_all) < 30


def pin_failed(ip, now=None):
    now = now or time.time()
    with _fail_lock:
        _fails.setdefault(ip, []).append(now)
        _fails_all.append(now)


def check_pin(pin):
    return bool(ADMIN_PIN) and hmac.compare_digest(hashlib.sha256(str(pin).encode()).digest(),
                                                   hashlib.sha256(ADMIN_PIN.encode()).digest())


# ---------------------------------------------------------------- incidents: evidence + monitor
MOCK_TRACEBACK = [
    "INFO discord.client logging in using static token",
    "INFO bot Loading cogs: music, moderation, error_handler",
    "Traceback (most recent call last):",
    '  File "/app/bot.py", line 58, in setup_hook',
    '    await self.load_extension("cogs.error_handler")',
    '  File "/usr/local/lib/python3.12/site-packages/discord/ext/commands/bot.py", line 1013, in load_extension',
    "    await self._load_from_module_spec(spec, name)",
    '  File "/app/cogs/error_handler.py", line 14, in setup',
    "    sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=True, traces_sample_rate=0.2)",
    '  File "/usr/local/lib/python3.12/site-packages/sentry_sdk/client.py", line 112, in _get_options',
    "    raise TypeError(\"Unknown option '%s'\" % key)",
    "TypeError: Unknown option 'send_default_pii'",
    "ERROR bot Fatal error during startup, exiting with code 1"]


def mock_logs(name, kind):
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z ", time.gmtime(t))
    if name == "pvt-discord-bot":
        t0 = time.time() - 25 * 60
        lines = [stamp(t0) + "DEBUG config token=MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GhIjKl.abcdefghijklmnopqrstuvwxyz0123456789AB"]
        for i in range(7):
            lines += [stamp(t0 + i * 200 + 2) + l for l in MOCK_TRACEBACK]
        return "\n".join(lines)
    if name == "defender-staging":
        t0 = time.time() - 300
        return "\n".join(stamp(t0 + i) + l for i, l in enumerate([
            "INFO [SYSTEM] Booting defender (staging)",
            "✅ [SYSTEM] Sentry Error Tracking: ENABLED",
            "✅ [SYSTEM] Database: connected",
            "INFO [SYSTEM] Running staging self-test",
            "INFO [SYSTEM] Self-test finished, restarting staging environment"]))
    if name == "mc-server":
        return "\n".join([
            "2026-11-19T08:01:12.120Z [init] Running as uid=1000 gid=1000",
            "2026-11-19T08:01:20.501Z [Server thread/INFO]: Starting minecraft server version 1.21.1",
            "2026-11-19T08:01:44.930Z [Server thread/INFO]: Done (24.4s)! For help, type \"help\"",
            "2026-11-19T21:15:02.004Z [mc-server-runner] Stopping with rcon-cli",
            "2026-11-19T21:15:02.880Z [Server thread/INFO]: Stopping the server",
            "2026-11-19T21:15:03.412Z [Server thread/INFO]: Saving worlds",
            "2026-11-19T21:15:05.002Z [Server thread/ERROR]: Failed to save chunk [12, -4]: java.io.IOException: No space left on device",
            "2026-11-19T21:15:05.300Z [mc-server-runner] Done"])
    return "2026-11-24T02:10:00.000Z INFO started\n2026-11-24T02:10:01.000Z INFO ready"


def capture_container(p):
    name, typ = p["target"], p["type"]
    ev, logs, events = {"container": name}, "", []
    try:
        if MOCK:
            c = next((x for x in docker()["containers"] if x["name"] == name), {})
            ev.update(image=c.get("image") or "", state=c.get("state"), exit_code=c.get("exit_code"), oom=c.get("oom"),
                      restarts=c.get("restarts"), health=c.get("health"), health_output=c.get("health_output"))
            logs = mock_logs(name, typ)
        else:
            info = docker_get(f"/containers/{urllib.parse.quote(name)}/json")
            st = info.get("State") or {}
            hl = st.get("Health") or {}
            ev.update(image=(info.get("Config") or {}).get("Image") or "", state=st.get("Status"), exit_code=st.get("ExitCode"),
                      oom=bool(st.get("OOMKilled")), restarts=info.get("RestartCount"), error=st.get("Error") or None,
                      started_at=st.get("StartedAt"), finished_at=st.get("FinishedAt"), health=hl.get("Status"),
                      health_output=incidents.redact(((hl.get("Log") or [{}])[-1].get("Output") or "").strip())[-1000:] or None)
            raw = docker_raw(f"/containers/{urllib.parse.quote(name)}/logs?stdout=1&stderr=1&tail=150&timestamps=1")
            logs = incidents.demux_docker_logs(raw)
            now = time.time()
            evs = docker_events(now - 3600, now, name)[-40:]
            dies = [e for e in evs if e["action"] == "die" and e["exit"] not in (None, "")]
            if dies:
                RECENT_EVENTS.extend(e for e in dies if e not in RECENT_EVENTS)
            for e in evs:
                what = {"die": f"Container stopped (exit {e['exit']})" if e["exit"] is not None else "Container stopped",
                        "oom": "Killed: out of memory", "restart": "Docker restarted it", "start": "Container started",
                        "stop": "Container was stopped", "kill": "Container was killed"}.get(e["action"], e["action"])
                events.append({"ts": e["ts"], "text": what})
    except Exception as e:
        ev["capture_error"] = str(e)[:200]
    logs = incidents.redact(logs)
    idx, headline = incidents.find_error(logs.splitlines())
    ev["error_line"] = idx
    state_code = ev.get("exit_code")
    last = last_exit(name, state_code)
    if last is not None and last != state_code:
        ev.update(exit_code=last, state_exit_code=state_code)  # "last exit: 1; Docker's current state says 0"
    m = re.match(r"(\d+) restarts in 15 min", p.get("detail") or "")
    if p["type"] == "crash_loop" and m:
        ev["restarts_recent"] = int(m.group(1))
    code = ev.get("exit_code")
    meaning = incidents.exit_meaning(code)
    if p["type"] == "crash_loop" and code in (0, None):
        meaning = "it keeps exiting by itself and Docker restarts it"  # never "exited normally" for a crash loop
    summary = {
        "crash_loop": f"{name} keeps crashing and Docker keeps restarting it ({p['detail']}). Exit code {code}: {meaning}.",
        "container_down": f"{name} stopped and didn't come back. Exit code {code}: {meaning}.",
        "oom": f"{name} used more memory than it was allowed, so the kernel killed it.",
        "unhealthy": f"{name} is running, but its own health check says it isn't working.",
    }.get(p["type"], p["detail"])
    if headline:
        summary += " The logs point to the line shown above."
    return {"summary": summary, "headline": headline or (ev.get("health_output") if p["type"] == "unhealthy" else None),
            "evidence": ev, "logs": logs, "events": events}


def capture_metric(p):
    s = health_system()
    s = s if ok(s) else {}
    col = {"cpu_hot": 3, "ram_high": 2}.get(p["type"])
    hist = history()
    series = [(h[0], h[col]) for h in hist if h[0] > time.time() - 3600 and h[col] is not None] if col else []
    d = docker()
    tops = sorted([c for c in (d.get("containers") or []) if c.get("mem_bytes")], key=lambda c: -c["mem_bytes"])[:5] if ok(d) else []
    ev = {"metric": p["type"], "value": p["detail"], "series": series,
          "top_processes": (s.get("top_processes") or [])[:5],
          "top_containers": [{"name": c["name"], "mem_bytes": c["mem_bytes"]} for c in tops]}
    if p["type"] == "disk_high" and ok(d):
        ev["docker_disk"] = {"images": d.get("disk_images"), "volumes": d.get("disk_volumes"),
                             "build_cache": d.get("disk_build_cache"), "reclaimable": d.get("disk_reclaimable")}
    limit = {"cpu_hot": "80 °C", "ram_high": "90 %", "disk_high": "85 %", "ssd_hot": "65 °C"}.get(p["type"])
    summary = f"{p['title']}: {p['detail']}" + (f" (alert from {limit})." if limit else ".")
    if p["kind"] == "system":
        summary = f"{p['title']}: {p['detail']}."
    ev.update(peak=p["detail"], limit=limit)
    return {"summary": summary, "headline": None, "evidence": ev, "logs": ""}


def capture(p):
    return capture_container(p) if p["kind"] == "container" else capture_metric(p)


STORE = incidents.Store(DATA_DIR)
MONITOR = incidents.Monitor(STORE, NTFY, capture, error_rule)


def seed_mock_incidents():
    """Mock mode: the open send_default_pii crash loop, a resolved CPU-hot, a resolved restart after reboot."""
    if STORE.count():
        return
    now = time.time()
    # 1. open: pvt-discord-bot crash loop (mock docker() keeps it restarting, so it stays open)
    start = now - 25 * 60 + 600
    cap = capture_container({"target": "pvt-discord-bot", "type": "crash_loop", "detail": "7 restarts in 15 min, exit code 1"})
    tl = []
    for i in range(7):
        tl.append({"ts": now - 25 * 60 + i * 200 + 30, "text": "Exited with code 1"})
        tl.append({"ts": now - 25 * 60 + i * 200 + 40, "text": "Docker restarted it"})
    tl += [{"ts": start, "text": "Crash loop detected: 3 restarts in 15 min, exit code 1"},
           {"ts": start + 2, "text": "Alert sent to phone"}]
    ev = dict(cap["evidence"], fingerprint="restarts:7", restarts_recent=7, timeline=sorted(tl, key=lambda x: x["ts"]))
    STORE.create({"key": "crash_loop:pvt-discord-bot", "kind": "container", "type": "crash_loop", "target": "pvt-discord-bot",
                  "severity": BAD, "priority": "urgent", "title": "pvt-discord-bot is crash-looping", "summary": cap["summary"],
                  "headline": cap["headline"], "status": "open", "started": start, "last_notified": start + 2,
                  "evidence": ev, "logs": cap["logs"]})
    # 2. resolved: processor hot during an Immich video job
    t0 = now - 2 * 86400 - 5 * 3600
    ser = [(t0 - 1800 + i * 120, round(66 + i * 0.95 + random.uniform(-1, 1), 1)) for i in range(31)]
    STORE.create({"key": "cpu_hot", "kind": "metric", "type": "cpu_hot", "target": "CPU temperature", "severity": BAD,
                  "priority": "high", "title": "Processor is hot",
                  "summary": "Processor reached 91 °C (alert from 80 °C) while Immich was converting videos.",
                  "headline": None, "status": "resolved", "started": t0, "ended": t0 + 18 * 60, "last_notified": t0 + 60,
                  "evidence": {"metric": "cpu_hot", "value": "91 °C", "peak": "91 °C", "limit": "80 °C", "series": ser,
                               "top_container_cpu": {"name": "immich_server", "cpu": 332, "process": "ffmpeg"},
                               "top_processes": [{"name": "ffmpeg", "cpu": 332.0}, {"name": "java", "cpu": 41.5},
                                                 {"name": "immich", "cpu": 22.0}, {"name": "python3", "cpu": 3.1},
                                                 {"name": "dockerd", "cpu": 1.2}],
                               "top_containers": [{"name": "immich_server", "mem_bytes": 1.1e9}, {"name": "mc-2", "mem_bytes": 1.4e9}],
                               "timeline": [{"ts": t0, "text": "Problem detected: 84 °C"},
                                            {"ts": t0 + 60, "text": "Alert sent to phone"},
                                            {"ts": t0 + 18 * 60, "text": "Back to normal"},
                                            {"ts": t0 + 18 * 60 + 5, "text": "Resolved message sent to phone"}]}})
    # 3. resolved, minor: restarted after the server rebooted
    t1 = now - 4 * 86400 - 2 * 3600
    STORE.create({"key": "container_down:immich_server", "kind": "container", "type": "container_down", "target": "immich_server",
                  "severity": WARN, "priority": "high", "title": "immich_server restarted",
                  "summary": "Restarted after server reboot. No errors in logs.", "headline": None, "status": "resolved",
                  "started": t1, "ended": t1 + 140,
                  "evidence": {"container": "immich_server", "image": "ghcr.io/immich-app/immich-server:v2.3.1", "state": "exited",
                               "exit_code": 0, "oom": False, "restarts": 0, "minor": True, "note": "Restarted after server reboot. No errors in logs.",
                               "timeline": [{"ts": t1 - 5, "text": "Server rebooted (kernel update)"},
                                            {"ts": t1, "text": "Down detected: Exited (0)"},
                                            {"ts": t1 + 140, "text": "Back to normal"}]},
                  "logs": "2026-11-20T03:12:01.000Z [Nest] LOG Shutting down\n2026-11-20T03:14:20.000Z [Nest] LOG Immich Server is listening"})
    # 4. older, outside the default 7 days: disk almost full
    STORE.create({"key": "disk_high", "kind": "metric", "type": "disk_high", "target": "Disk", "severity": WARN, "priority": "high",
                  "title": "Disk almost full", "summary": "Disk reached 87 % used (alert from 85 %).", "headline": None,
                  "status": "resolved", "started": now - 12 * 86400, "ended": now - 12 * 86400 + 5400,
                  "evidence": {"metric": "disk_high", "value": "87 % used", "limit": "85 %", "series": [],
                               "docker_disk": {"images": 14.2e9, "volumes": 3.1e9, "build_cache": 6.8e9, "reclaimable": 9.4e9},
                               "timeline": [{"ts": now - 12 * 86400, "text": "Problem detected: 87 % used"},
                                            {"ts": now - 12 * 86400 + 5400, "text": "Back to normal"}]}})


def monitor_loop():
    """Every 30 s: read new Docker events, compare problems with open incidents, alert by ntfy."""
    if MOCK:
        seed_mock_incidents()
    removed = STORE.delete_low()  # low-priority incidents from older versions: remove quietly, no "resolved" ping
    if removed:
        print(f"incidents: removed {removed} low-priority incident(s)", flush=True)
    last = time.time() - 60
    print(f"incidents: {'saved in ' + DATA_DIR if STORE.persistent else 'memory only'}; "
          f"phone alerts {'on (topic ' + NTFY.topic + ')' if NTFY.enabled else 'off (NTFY_URL/NTFY_TOKEN not set)'}", flush=True)
    while True:
        now = time.time()
        if not MOCK:
            try:
                RECENT_EVENTS.extend(docker_events(last, now))
                last = now
            except Exception as e:
                print(f"docker events failed: {str(e)[:120]}", flush=True)
        try:
            STORE.expire_rules(now)  # finished mutes: their incidents are visible again
            for f in (docker, health_system):  # fresh state for detection
                _cache.pop(f.__name__, None)
            for c in (docker().get("containers") or []):  # homelab.ignore=true containers: hide their incidents
                if c.get("ignored") == "label":
                    STORE.mark_ignored(-1, "kind='container' AND target=?", (c["name"],))
            MONITOR.tick(incident_problems(), now)
        except Exception as e:
            print(f"incident monitor failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
        time.sleep(30)


DOWN_TYPES = ("container_down", "crash_loop", "oom")


def incident_stats(now=None):
    """Stat row: open now, last 7 days, most incidents, uptime (7 d) from app/server-down incidents."""
    now = now or time.time()
    week = now - 7 * 86400
    items = STORE.list(since=week, limit=1000)
    in_week = [i for i in items if i["started"] >= week or i["status"] == "open"]
    counts = {}
    for i in in_week:
        counts[i["target"]] = counts.get(i["target"], 0) + 1
    top = max(counts.items(), key=lambda kv: kv[1]) if counts else None
    spans = sorted((max(i["started"], week), i["ended"] or now) for i in in_week
                   if i["type"] in DOWN_TYPES and not (i.get("evidence") or {}).get("minor"))
    down, cur_s, cur_e = 0, None, None
    for a, b in spans:  # union of overlapping down periods
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                down += cur_e - cur_s
            cur_s, cur_e = a, b
        else:
            cur_e = max(cur_e, b)
    if cur_e is not None:
        down += cur_e - cur_s
    return {"open": sum(1 for i in in_week if i["status"] == "open"), "week": len(in_week),
            "top": top, "uptime": 100 * (1 - down / (7 * 86400)), "down_secs": down}


def incident_list_page(query, admin="off"):
    q = urllib.parse.parse_qs(query)
    show = (q.get("show") or ["all"])[0]
    rng = (q.get("range") or ["7d"])[0]
    try:
        open_id = int((q.get("open") or ["0"])[0])
    except ValueError:
        open_id = 0
    secs = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}.get(rng, 7 * 86400)
    items = STORE.list(since=time.time() - secs, status=show if show in ("open", "resolved") else None,
                       kind="container" if show == "apps" else None, ignored=show == "ignored")
    if show == "system":
        items = [i for i in items if i["kind"] in ("metric", "system")]
    if open_id and not any(i["id"] == open_id for i in items):  # a deep link always shows its incident
        extra = STORE.get(open_id)
        if extra:
            items.append(extra)
    full = {i["id"]: STORE.get(i["id"]) for i in items if i["status"] == "open" or i["id"] == open_id}
    d = docker()
    label_ignored = [c["name"] for c in (d.get("containers") or []) if c.get("ignored") == "label"] if ok(d) else []
    return panels.panel_incidents(items, {"show": show, "range": rng}, incident_stats(), full, open_id,
                                  NTFY.enabled, STORE.persistent, admin=admin, rules=STORE.rules(),
                                  label_ignored=label_ignored)


def container_logs_page(name):
    d = docker()
    if not (ok(d) and any(c["name"] == name for c in d["containers"])):
        return panels.page("incidents", '<p class="empty">No container with that name.</p>')
    if MOCK:
        logs = mock_logs(name, "")
    else:
        logs = incidents.demux_docker_logs(
            docker_raw(f"/containers/{urllib.parse.quote(name)}/logs?stdout=1&stderr=1&tail=200&timestamps=1"))
    logs = incidents.redact(logs)
    idx, head = incidents.find_error(logs.splitlines())
    return panels.panel_logs(name, logs, idx, head)


# ---------------------------------------------------------------- auto-discovered links (Links tab, Home buttons)
FIRST_SEEN = {}          # container name -> first time seen; 0 = already there when the dashboard started
_links_baseline = [False]


def mock_containers():
    """Docker /containers/json-shaped mock data, plus PortBindings for the stopped ones."""
    def c(name, image, state, ports=(), labels=None, status=None):
        return {"Id": (name.replace("_", "").replace("-", "") + "0" * 12)[:12] + "mock", "Names": ["/" + name],
                "Image": image, "State": state, "Labels": labels or {},
                "Status": status or ("Up 12 days" if state == "running" else "Exited (0) 2 days ago"),
                "Ports": [{"IP": ip, "PrivatePort": cp, "PublicPort": hp, "Type": "tcp"}
                          for hp, cp in ports for ip in ("0.0.0.0", "::")] if state == "running" else [],
                "_bindings": {f"{cp}/tcp": [{"HostIp": "", "HostPort": str(hp)}] for hp, cp in ports}}
    world = {"mcpanel": "1"}
    return [
        c("immich_server", "ghcr.io/immich-app/immich-server:v2.3.1", "running", [(2283, 2283)]),
        c("immich_postgres", "ghcr.io/immich-app/postgres:14-vectorchord0.4.3", "running", [(5432, 5432)]),
        c("immich_redis", "docker.io/valkey/valkey:8-bookworm", "running"),
        c("beszel", "henrygd/beszel:latest", "running", [(8090, 8090)]),
        c("beszel-agent", "henrygd/beszel-agent:latest", "running"),
        c("mcpanel", "mcpanel-mcpanel:latest", "running", [(8081, 8000)]),
        c("bunkers", "bunkers-web:latest", "running", [(3001, 3000)]),
        c("dozzle", "amir20/dozzle:latest", "exited", [(8888, 8080)]),
        c("homelab-dashboard", "homelab-dashboard:latest", "running", [(3000, 3000)]),
        c("pvt-discord-bot", "pvt-discord-bot:latest", "running"),
        c("mc-discord-bot", "mc-discord-bot:latest", "running"),
        c("mc-2", "itzg/minecraft-server:latest", "running", [(25566, 25565)], world, "Up 3 hours"),
        c("mc-1", "itzg/minecraft-server:latest", "exited", [(25565, 25565)], world),
        c("mc-server", "itzg/minecraft-server:latest", "exited", [], None, "Exited (143) 5 days ago"),
        c("extraction-service", "extraction-service:latest", "exited"),
    ]


@cached(30)
def links_data():
    if MOCK:
        containers = mock_containers()
        by_id = {c["Id"]: c for c in containers}
        inspect = lambda cid: {"HostConfig": {"PortBindings": by_id[cid]["_bindings"]}}
    else:
        containers = docker_get("/containers/json?all=1")
        inspect = lambda cid: docker_get(f"/containers/{cid}/json")
    if not _links_baseline[0]:  # whatever exists when the dashboard starts is not "new"
        for c in containers:
            FIRST_SEEN.setdefault(((c.get("Names") or ["?"])[0]).lstrip("/"), 0)
        _links_baseline[0] = True
        if MOCK:
            FIRST_SEEN["bunkers"] = time.time() - 2 * 3600  # show off the NEW badge
    return links.discover(containers, inspect, os.environ.get("HOSTNAME", ""), FIRST_SEEN)


ROUTES = {
    "/health/system": health_system, "/health/battery": health_battery, "/health/ssd": health_ssd,
    "/health/updates": health_updates, "/energy": energy, "/traffic": traffic, "/docker": docker,
    "/immich": immich, "/github": github, "/security": security, "/tracker": tracker,
    "/links": links_data,
    "/problems": problems,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "server-api"

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path in STATIC:
                body, ctype = static_file(path)
            elif path == "/panel/incidents":
                body = incident_list_page(self.path.partition("?")[2], admin_state(self.headers)).encode()
                ctype = "text/html; charset=utf-8"
            elif re.fullmatch(r"/panel/incident/\d+", path):
                body = incident_list_page(f"open={path.rsplit('/', 1)[1]}", admin_state(self.headers)).encode()
                ctype = "text/html; charset=utf-8"
            elif re.fullmatch(r"/panel/logs/[A-Za-z0-9_.-]{1,128}", path):
                body, ctype = container_logs_page(path.rsplit("/", 1)[1]).encode(), "text/html; charset=utf-8"
            elif path == "/incidents":
                body, ctype = json.dumps(STORE.list(since=time.time() - 30 * 86400)).encode(), "application/json"
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

    def _json(self, code, obj, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """Write actions (ignore/mute rules). Need the admin PIN cookie plus a custom header, so other
        websites can't trigger them (a cross-site request can't add the header without CORS)."""
        path = self.path.split("?", 1)[0].rstrip("/")
        if not ADMIN_PIN:
            return self._json(403, {"error": "read-only: DASHBOARD_ADMIN_PIN is not set"})
        if self.headers.get("X-Dashboard") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json(400, {"error": "bad request"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) if 0 < n <= 4096 else b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        ip = self.client_address[0]
        if path == "/api/login":
            if not pin_attempt_allowed(ip):
                return self._json(429, {"error": "Too many tries. Wait 15 minutes."})
            if not check_pin(data.get("pin", "")):
                pin_failed(ip)
                time.sleep(0.5)
                return self._json(401, {"error": "Wrong PIN"})
            cookie = f"{ADMIN_COOKIE}={issue_token()}; Max-Age={ADMIN_DAYS * 86400}; Path=/; HttpOnly; SameSite=Strict"
            return self._json(200, {"ok": True}, cookie)
        if path == "/api/logout":
            return self._json(200, {"ok": True}, f"{ADMIN_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict")
        if admin_state(self.headers) != "on":
            return self._json(401, {"error": "PIN required"})
        try:
            if path == "/api/rules":
                rid = add_rule_from_incident(int(data.get("incident")), data.get("kind"),
                                             int(data["hours"]) if data.get("hours") else None)
                return self._json(200, {"ok": True, "rule": rid})
            if path == "/api/rules/delete":
                return self._json(200, {"ok": True, "removed": STORE.delete_rule(int(data.get("rule")))})
        except (ValueError, TypeError) as e:
            return self._json(400, {"error": str(e)[:120]})
        finally:
            _cache.pop("docker", None)
        return self._json(404, {"error": "not found"})

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
    "docker": lambda: panels.panel_docker(docker()),
    "security": lambda: panels.panel_security(security()),
    "home": lambda: panels.panel_home(health_system(), health_battery(), history(), energy(), *_rows_with_live(),
                                      traffic(), docker(), immich(), github(), attention_items(), links_data(),
                                      {"items": STORE.list(since=time.time() - 7 * 86400, limit=20),
                                       "open": len(STORE.list(status="open"))}),
    "links": lambda: panels.panel_links(links_data()),
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
    threading.Thread(target=monitor_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
