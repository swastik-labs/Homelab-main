"""Dashboard panels: one HTML fragment per tab, rendered server-side with inline SVG charts.

web/app.js fetches /panel/<tab> every 15-20 s and swaps the fragment in, so the page stays
live without reloading. Styling lives in web/app.css.
"""
import calendar
import html
import time
from datetime import date, datetime, timedelta

OKC, WARNC, BADC, MUTED = "#4ade80", "#fbbf24", "#f87171", "#94a3b8"
LEVEL_COLOUR = {"ok": OKC, "warn": WARNC, "bad": BADC, None: MUTED}
LEVEL_WORD = {"ok": "OK", "warn": "Check", "bad": "Problem", None: "Unknown"}
ACCENT = {"home": "#38bdf8", "links": "#94a3b8", "health": "#2dd4bf", "power": "#f59e0b", "traffic": "#38bdf8", "immich": "#f472b6",
          "bots": "#a78bfa", "docker": "#60a5fa", "security": "#34d399"}
MDI = "https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg/{}.svg"
esc = html.escape


# ------------------------------------------------------------------ small formatters
def level(v, warn, bad, higher_is_worse=True):
    if v is None:
        return None
    if higher_is_worse:
        return "bad" if v >= bad else "warn" if v >= warn else "ok"
    return "bad" if v <= bad else "warn" if v <= warn else "ok"


def gb(b, digits=None):
    if b is None:
        return "—"
    g = b / 1e9
    if g >= 1000:
        return f"{g / 1000:.1f} TB"
    if g >= 1:
        return f"{g:.{digits if digits is not None else (1 if g < 10 else 0)}f} GB"
    return f"{b / 1e6:.0f} MB"


def num(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}"


def ago(ts_iso):
    if not ts_iso:
        return None
    try:
        t = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
    s = max(0, time.time() - t)
    if s < 10:
        return "just now"
    if s < 90:
        return f"{int(s)} s ago"
    if s < 5400:
        return f"{int(s // 60)} min ago"
    if s < 172800:
        return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} days ago"


def duration(secs):
    if not secs:
        return "—"
    d, h = int(secs // 86400), int(secs % 86400 // 3600)
    return f"{d} day{'s' if d != 1 else ''}, {h} h" if d else f"{h} h {int(secs % 3600 // 60)} min"


def when(ts_iso):
    try:
        t = datetime.fromisoformat(ts_iso)
        return f"{t.day} {t:%b}, {t.hour % 12 or 12}:{t:%M} {'am' if t.hour < 12 else 'pm'}"
    except Exception:
        return "—"


# ------------------------------------------------------------------ building blocks
def icon(name, colour="var(--accent)", size=22):
    return (f'<span class="ic" style="width:{size}px;height:{size}px;background:{colour};'
            f'-webkit-mask-image:url({MDI.format(name)});mask-image:url({MDI.format(name)})"></span>')


def pill(lv, text=None):
    return f'<span class="pill" style="--c:{LEVEL_COLOUR[lv]}">{esc(text or LEVEL_WORD[lv])}</span>'


def card(title, ic, body, lv="none", explain=None, span=1, extra_cls=""):
    p = pill(lv) if lv != "none" else ""
    ex = f'<p class="ex">{explain}</p>' if explain else ""
    return (f'<section class="card s{span} {extra_cls}"><div class="hd">{icon(ic)}<h3>{esc(title)}</h3>{p}</div>'
            f'{body}{ex}</section>')


def big(value, unit="", lv=None, sub=None):
    colour = f' style="color:{LEVEL_COLOUR[lv]}"' if lv else ""
    u = f'<small>{esc(unit)}</small>' if unit else ""
    s = f'<div class="sub">{sub}</div>' if sub else ""
    return f'<div class="big"{colour}>{esc(str(value))}{u}</div>{s}'


def bar(pct, lv, left="", right="", scale=None):
    pct = max(0, min(100, pct or 0))
    sc = f'<div class="scale">{scale}</div>' if scale else ""
    return (f'<div class="barwrap"><div class="barlbl"><span>{left}</span><span>{right}</span></div>'
            f'<div class="bar"><i style="width:{pct:.1f}%;background:{LEVEL_COLOUR[lv]}"></i></div>{sc}</div>')


def trend(cur, prev, label, up_is_bad=True, neutral=False):
    if cur is None or not prev:
        return f'<span class="trend" style="--c:{MUTED}">no comparison yet</span>'
    ch = (cur - prev) / prev * 100
    if abs(ch) < 3:
        return f'<span class="trend" style="--c:{MUTED}">≈ same as {esc(label)}</span>'
    arrow = "↑" if ch > 0 else "↓"
    c = MUTED if neutral else (WARNC if (ch > 0) == up_is_bad else OKC)
    return f'<span class="trend" style="--c:{c}">{arrow} {abs(ch):.0f}% vs {esc(label)}</span>'


def smooth(values, buckets=48):
    """Average long series into ~48 buckets so sparklines show the trend, not jitter."""
    if len(values) <= buckets:
        return values
    out, size = [], len(values) / buckets
    for k in range(buckets):
        chunk = [v for v in values[int(k * size):int((k + 1) * size)] if v is not None]
        out.append(sum(chunk) / len(chunk) if chunk else None)
    return out


def spark(values, colour="var(--accent)", h=38):
    values = smooth(list(values))
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        if v is None:
            continue
        x = i / (n - 1) * 100
        y = h - 3 - (v - lo) / rng * (h - 6)
        pts.append(f"{x:.2f},{y:.2f}")
    line = " ".join(pts)
    return (f'<svg class="spark" viewBox="0 0 100 {h}" preserveAspectRatio="none">'
            f'<polygon points="0,{h} {line} 100,{h}" fill="{colour}" opacity=".15"/>'
            f'<polyline points="{line}" fill="none" stroke="{colour}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>')


def nice_max(v):
    if v <= 0:
        return 1
    import math
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def bar_chart(labels, series, fmt, value_labels=None, avg=None, height=170, stacked=True, legend=None):
    """series: list of (name, colour, values). Stacked bars, y grid, x labels, optional average line."""
    n = len(labels)
    if n == 0:
        return '<p class="empty">No data yet. Charts fill in as days are recorded.</p>'
    W, H, L, B, T = 640, height, 46, 22, 14
    totals = [sum((s[2][i] or 0) for s in series) for i in range(n)] if stacked else \
        [max((s[2][i] or 0) for s in series) for i in range(n)]
    vmax = nice_max(max(totals) * 1.05 if totals else 1)
    plot_w, plot_h = W - L - 6, H - B - T
    slot = plot_w / n
    bw = slot * (0.62 if n <= 10 else 0.72)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img">']
    for k in range(5):
        v = vmax * k / 4
        y = T + plot_h - plot_h * k / 4
        out.append(f'<line x1="{L}" x2="{W - 6}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y + 3.5:.1f}" text-anchor="end" class="ax">{fmt(v)}</text>')
    step = 1 if n <= 10 else (5 if n <= 31 else 7)
    for i, lab in enumerate(labels):
        x = L + i * slot + (slot - bw) / 2
        base = T + plot_h
        tip = [f"{lab}"]
        for name, colour, vals in series:
            v = vals[i] or 0
            h = plot_h * v / vmax
            y = base - h if stacked else T + plot_h - h
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(h, 0.8):.1f}" rx="2" fill="{colour}">'
                       f'<title>{esc(lab)} · {esc(name)}: {esc(fmt(v))}</title></rect>')
            if stacked:
                base -= h
        if value_labels and n <= 10:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{T + plot_h - plot_h * totals[i] / vmax - 5:.1f}" '
                       f'text-anchor="middle" class="vl">{esc(value_labels[i])}</text>')
        if i % step == 0 or i == n - 1:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{H - 6}" text-anchor="middle" class="ax">{esc(lab)}</text>')
    if avg:
        y = T + plot_h - plot_h * avg / vmax
        out.append(f'<line x1="{L}" x2="{W - 6}" y1="{y:.1f}" y2="{y:.1f}" class="avg"/>'
                   f'<text x="{W - 8}" y="{y - 4:.1f}" text-anchor="end" class="avgt">average {esc(fmt(avg))}</text>')
    out.append("</svg>")
    leg = ""
    if legend:
        leg = '<div class="legend">' + "".join(
            f'<span><i style="background:{c}"></i>{esc(nm)}</span>' for nm, c in legend) + "</div>"
    return leg + "".join(out)


def line_chart(times, series, height=220, ymax=None, unit="", W=1280):
    """series: list of (name, colour, values). times: unix timestamps. W ~ rendered width in px."""
    times = smooth(list(times), 160)
    series = [(n, c, smooth(list(v), 160)) for n, c, v in series]
    pts = [i for i, t in enumerate(times)]
    if len(pts) < 2:
        return '<p class="empty">Collecting history. The chart appears after a few minutes.</p>'
    H, L, B, T = height, 40, 22, 12
    allv = [v for _, _, vs in series for v in vs if v is not None]
    vmax = ymax or nice_max(max(allv) * 1.1 if allv else 1)
    plot_w, plot_h = W - L - 6, H - B - T
    t0, t1 = times[0], times[-1]
    span = (t1 - t0) or 1
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img">']
    for k in range(5):
        v = vmax * k / 4
        y = T + plot_h - plot_h * k / 4
        out.append(f'<line x1="{L}" x2="{W - 6}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y + 3.5:.1f}" text-anchor="end" class="ax">{v:.0f}{unit}</text>')
    for name, colour, vals in series:
        p = []
        for t, v in zip(times, vals):
            if v is None:
                continue
            x = L + (t - t0) / span * plot_w
            y = T + plot_h - plot_h * min(v, vmax) / vmax
            p.append(f"{x:.1f},{y:.1f}")
        out.append(f'<polyline points="{" ".join(p)}" fill="none" stroke="{colour}" stroke-width="2" stroke-linejoin="round"/>')
    for k in range(5):
        t = t0 + span * k / 4
        x = L + plot_w * k / 4
        lab = datetime.fromtimestamp(t).strftime("%H:%M")
        out.append(f'<text x="{x:.1f}" y="{H - 6}" text-anchor="{"start" if k == 0 else "end" if k == 4 else "middle"}" class="ax">{lab}</text>')
    out.append("</svg>")
    leg = '<div class="legend">' + "".join(
        f'<span><i style="background:{c}"></i>{esc(nm)}</span>' for nm, c, _ in series) + "</div>"
    return leg + "".join(out)





def page(section, body):
    """Panels are HTML fragments; the frontend (web/app.js) swaps them into the page."""
    return f'<div class="panel" style="--accent:{ACCENT[section]}">{body}</div>'


def row(k, v, lv=None):
    dot = f'<span class="dot" style="--c:{LEVEL_COLOUR[lv]}"></span>' if lv else ""
    return f'<div class="row"><span class="k">{k}</span><span class="v">{dot}{v}</span></div>'


def ok_dict(d):
    return isinstance(d, dict) and "error" not in d


# ------------------------------------------------------------------ Server health
def panel_health(sysd, bat, ssd, upd, history):
    s = sysd if ok_dict(sysd) else {}
    cards = []
    hist_t = [h[0] for h in history]
    cpu = s.get("cpu_percent")
    lv = level(cpu, 70, 90)
    cards.append(card("Processor load", "chip", big(f"{cpu:.0f}" if cpu is not None else "—", "% busy", lv)
                      + bar(cpu, lv, "", "", "<span>idle</span><span>busy</span>")
                      + spark([h[1] for h in history]),
                      lv if cpu is not None else None,
                      "How hard the processor is working. Short spikes are normal; staying above 70% for long means something is busy."))
    t = s.get("cpu_temp_c")
    lv = level(t, 75, 90)
    cards.append(card("Processor temperature", "thermometer", big(f"{t:.0f}" if t is not None else "—", "°C", lv)
                      + bar(((t or 30) - 30) / 70 * 100, lv, "", "", "<span>30 °C</span><span>75 °C warm</span><span>100 °C</span>")
                      + spark([h[3] for h in history], LEVEL_COLOUR[lv] if lv else MUTED),
                      lv if t is not None else None,
                      "Laptop processors are fine up to about 80 °C. Higher for a long time means dust or poor airflow."))
    rp = s.get("ram_percent")
    lv = level(rp, 80, 92)
    cards.append(card("Memory (RAM)", "memory", big(gb(s.get("ram_used_bytes")), f"of {gb(s.get('ram_total_bytes'))}", None)
                      + bar(rp, lv, f"{rp:.0f}% in use" if rp is not None else "", f"{gb((s.get('ram_total_bytes') or 0) - (s.get('ram_used_bytes') or 0))} free")
                      + spark([h[2] for h in history]),
                      lv if rp is not None else None,
                      "Working memory shared by all apps. Above 90% the server slows down and may stop apps."))
    dp = s.get("disk_percent")
    lv = level(dp, 80, 90)
    free = (s.get("disk_total_bytes") or 0) - (s.get("disk_used_bytes") or 0)
    cards.append(card("Disk space", "harddisk", big(gb(free) if dp is not None else "—", "free", None)
                      + bar(dp, lv, f"{dp:.0f}% used" if dp is not None else "", f"{gb(s.get('disk_used_bytes'))} of {gb(s.get('disk_total_bytes'))}"),
                      lv if dp is not None else None,
                      "Space on the main drive (photos, apps, logs). Keep it under 85% used."))
    # history chart
    cards.append(card("Last 24 hours", "chart-line",
                      line_chart(hist_t, [("Processor load %", "#2dd4bf", [h[1] for h in history]),
                                          ("Memory used %", "#a78bfa", [h[2] for h in history]),
                                          ("Temperature °C", "#fb923c", [h[3] for h in history])], ymax=100),
                      "none", "Sampled every minute and kept in memory for 24 h, so the chart restarts empty after the dashboard restarts.", span=4))
    # battery
    b = bat if ok_dict(bat) else {}
    ch, hl, st = b.get("charge_percent"), b.get("health_percent"), (b.get("status") or "").lower()
    on_batt = st == "discharging"
    lv_c = "bad" if on_batt else ("ok" if ch is not None else None)
    lv_h = level(hl, 60, 40, False)
    plug = {"full": "Plugged in, full", "charging": "Plugged in, charging", "discharging": "Running on battery!",
            "not charging": "Plugged in"}.get(st, "Unknown")
    cards.append(card("Battery", "battery-heart-variant",
                      big(f"{ch:.0f}" if ch is not None else "—", "% charged", lv_c, esc(plug))
                      + bar(ch, lv_c or "ok", "Charge", f"{ch:.0f}%" if ch is not None else "—")
                      + bar(hl, lv_h, "Health vs new", f"{hl:.0f}%" if hl is not None else "—")
                      + (f'<div class="sub" style="margin-top:8px">Charged {b["cycle_count"]} times so far</div>' if b.get("cycle_count") is not None else ""),
                      "bad" if on_batt else (lv_h or None) if hl is not None else None,
                      "The battery is the server's backup during power cuts. Health is how much it holds compared to new; below 60% backup time gets short."))
    t2 = ssd.get("nvme_temp_c") if ok_dict(ssd) else None
    lv = level(t2, 60, 70)
    cards.append(card("SSD temperature", "expansion-card", big(f"{t2:.0f}" if t2 is not None else "—", "°C", lv)
                      + bar(((t2 or 20) - 20) / 60 * 100, lv, "", "", "<span>20 °C</span><span>60 °C warm</span><span>80 °C</span>"),
                      lv if t2 is not None else None,
                      "The storage drive. Under 60 °C is healthy."))
    u = upd if ok_dict(upd) else {}
    n, sec, rr, esm = u.get("updates"), u.get("security_updates"), u.get("reboot_required"), u.get("esm_updates")
    lv_u = None if n is None else ("warn" if n else "ok")
    lv_r = None if rr is None else ("warn" if rr == "yes" else "ok")
    worst = "warn" if "warn" in (lv_u, lv_r) else ("ok" if lv_u or lv_r else None)
    cards.append(card("Updates and restarts", "package-up",
                      big("Up to date" if n == 0 else (n if n is not None else "—"), "" if n == 0 else "updates waiting", lv_u)
                      + '<div class="rows">'
                      + row("Of these, security", "none" if not sec else f"{sec}", "warn" if sec else ("ok" if sec == 0 else None))
                      + (row("With Ubuntu Pro", f"{esm} extra security fixes (optional)") if esm else "")
                      + row("Restart needed", "Yes" + (f" ({esc(u['reboot_pkgs'])})" if u.get("reboot_pkgs") else "") if rr == "yes" else ("No" if rr == "no" else "—"), lv_r)
                      + row("Last restart", f"{when(s.get('last_reboot'))} · {ago(s.get('last_reboot')) or ''}")
                      + row("Running for", duration(s.get("uptime_seconds")))
                      + "</div>",
                      worst, "Install updates with <code>sudo apt update && sudo apt upgrade</code>. "
                      "Ubuntu Pro (ESM) fixes are extra and optional; they are not counted as waiting updates.",
                      ))
    top = s.get("top_processes") or []
    mx = max([p["cpu"] for p in top] + [1])
    rows = "".join(f'<div class="row"><span class="k">{esc(p["name"])}</span><span class="v">{p["cpu"]:.0f}%'
                   f'<span class="mini"><i style="width:{p["cpu"] / mx * 100:.0f}%"></i></span></span></div>' for p in top)
    cards.append(card("Busiest programs", "format-list-numbered", f'<div class="rows">{rows or "<p class=empty>—</p>"}</div>',
                      "none", "Programs using the most processor time right now (java is usually the Minecraft server)."))
    return page("health", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Power and cost
def _day_label(d):
    return datetime.fromisoformat(d).strftime("%a %d") if d else ""


def month_split(rows, today):
    m0 = today.replace(day=1)
    pm_end = m0 - timedelta(days=1)
    pm0 = pm_end.replace(day=1)
    this = [r for r in rows if r["date"] >= m0.isoformat() and r["date"] < today.isoformat()]
    prev = [r for r in rows if pm0.isoformat() <= r["date"] <= pm_end.isoformat()]
    return this, prev, pm_end


def panel_power(e, rows, live, rate, base_watts=9):
    e = e if ok_dict(e) else {}
    rows = rows or []
    today = date.today()
    this_m, prev_m, pm_end = month_split(rows, today)
    ydays = [r for r in rows if r["date"] < today.isoformat()]
    hours = (live or {}).get("hours") or 0
    tk = e.get("today_kwh")
    pace = tk / hours * 24 if tk is not None and hours > 1 else None
    yk = e.get("yesterday_kwh")
    cards = []
    sub = f"₹{e['today_cost_inr']:.2f} so far" if e.get("today_cost_inr") is not None else "No live reading yet"
    live_note = (f"Last reading {ago(live.get('updated_at'))}." if live and live.get("updated_at")
                 else "Apply the energy-tracker patch (see the README) to see today's live numbers.")
    cards.append(card("Today so far", "flash", big(f"{tk:.2f}" if tk is not None else "—", "kWh", None, sub)
                      + (trend(pace, yk, "yesterday") if pace else "")
                      + (f'<div class="sub" style="margin-top:6px;font-size:12.5px">On pace for about {pace:.2f} kWh today</div>' if pace else "")
                      + spark([r["total_kwh"] for r in ydays[-14:]] + ([pace] if pace else [])),
                      "none", f"Electricity the server used today, estimated at ₹{rate:g}/kWh. {live_note}"))
    cards.append(card("Yesterday", "calendar-arrow-left", big(f"{yk:.2f}" if yk is not None else "—", "kWh", None,
                                                              f"₹{e['yesterday_cost_inr']:.2f}" if e.get("yesterday_cost_inr") is not None else "")
                      + (trend(yk, ydays[-2]["total_kwh"] if len(ydays) >= 2 else None, "the day before") if yk is not None else ""),
                      "none", "A full day of use, from the energy tracker's daily report."))
    proj, pk = e.get("projected_bill_inr"), e.get("projected_kwh")
    prev_kwh = sum(r["total_kwh"] or 0 for r in prev_m) or None
    dim = calendar.monthrange(today.year, today.month)[1]
    mk = e.get("month_kwh")
    cards.append(card("Expected bill this month", "cash", big(f"₹{proj:.0f}" if proj is not None else "—", "", None,
                                                              f"about {pk:.1f} kWh in {today:%B}" if pk else "Not enough data yet")
                      + (trend(pk, prev_kwh, pm_end.strftime("%B")) if pk else "")
                      + bar((mk or 0) / pk * 100 if pk else 0, "ok", f"₹{e.get('month_cost_inr') or 0:.0f} used so far", f"day {today.day} of {dim}"),
                      "none", f"Guess for the full month, based on use so far at ₹{rate:g}/kWh."))
    avg_kwh = (sum(r["total_kwh"] or 0 for r in this_m) / len(this_m)) if this_m else (yk or None)
    watts = avg_kwh * 1000 / 24 if avg_kwh else None
    cards.append(card("Average power draw", "lightbulb-on-outline",
                      big(f"{watts:.0f}" if watts else "—", "watts", None,
                          f"{watts / 9:.1f}× a 9 W LED bulb, all day" if watts else "")
                      + (f'<div class="sub" style="margin-top:6px;font-size:12.5px">≈ ₹{avg_kwh * rate * 30:.0f} per month · ₹{avg_kwh * rate * 365:.0f} per year</div>' if avg_kwh else ""),
                      "none", f"Average over this month's full days. Includes a fixed {base_watts} W for the rest of the laptop."))
    last7 = ydays[-7:]
    cards.append(card("Last 7 days", "chart-bar",
                      bar_chart([_day_label(r["date"]) for r in last7], [("kWh", "#f59e0b", [r["total_kwh"] for r in last7])],
                                lambda v: f"{v:.2f}", value_labels=[f"₹{(r['cost_inr'] or 0):.2f}" for r in last7]),
                      "none", "Electricity used per day (kWh). The label on each bar is the cost that day.", span=2))
    last30 = ydays[-30:]
    a30 = sum(r["total_kwh"] or 0 for r in last30) / len(last30) if last30 else None
    cards.append(card("Last 30 days", "chart-bar",
                      bar_chart([datetime.fromisoformat(r["date"]).strftime("%d %b") for r in last30],
                                [("kWh", "#f59e0b", [r["total_kwh"] for r in last30])], lambda v: f"{v:.2f}", avg=a30),
                      "none", f"Daily use over the last month. Total ₹{sum(r['cost_inr'] or 0 for r in last30):.0f} for these {len(last30)} days.", span=2))
    return page("power", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Traffic
def nice_day(d):
    return f"{d.day} {d:%b}"


def is_internet(day_iso, since):
    """Daily rows after the switch day are internet-only; the switch day itself was mixed."""
    return day_iso > since.isoformat()


def internet_days(rows, since):
    return [r for r in rows if is_internet(r["date"], since)]


def day_total(r):
    return (r["rx_bytes"] or 0) + (r["tx_bytes"] or 0)


def panel_traffic(t, rows, live, since):
    t = t if ok_dict(t) else {}
    rows = rows or []
    today = date.today()
    ydays = [r for r in rows if r["date"] < today.isoformat()]
    this_m, prev_m, pm_end = month_split(rows, today)
    hours = (live or {}).get("hours") or 0
    live_inet = (live or {}).get("traffic_scope") == "internet"
    tot = t.get("today_total")
    pace = tot / hours * 24 if tot and hours > 1 else None
    yrow = next((r for r in ydays if r["date"] == (today - timedelta(days=1)).isoformat()), None)
    y_inet = bool(yrow) and is_internet(yrow["date"], since)
    ytot = t.get("yesterday_total")
    D, U, GD, GU = "#38bdf8", "#818cf8", "#475569", "#64748b"
    first_full = since + timedelta(days=1)
    lan_note = f"includes home network (internet-only from {nice_day(first_full)})"

    def split_bar(rx, tx):
        if rx is None:
            return ""
        s_ = (rx or 0) + (tx or 0) or 1
        return (f'<div class="barwrap"><div class="barlbl"><span><span class="dot" style="--c:{D}"></span>↓ {gb(rx)} downloaded</span>'
                f'<span><span class="dot" style="--c:{U}"></span>↑ {gb(tx)} uploaded</span></div>'
                f'<div class="bar" style="display:flex"><i style="width:{rx / s_ * 100:.1f}%;background:{D};border-radius:0"></i>'
                f'<i style="width:{tx / s_ * 100:.1f}%;background:{U};border-radius:0"></i></div></div>')

    def scope_tag(text):
        return f'<span class="trend" style="--c:{MUTED}">{esc(text)}</span>'

    inet_hist = [day_total(r) for r in internet_days(ydays, since)][-14:]
    cards = [
        card("Today so far", "earth", big(gb(tot) if tot is not None else "—", "", None)
             + split_bar(t.get("today_rx"), t.get("today_tx"))
             + (trend(pace, ytot, "yesterday", neutral=True) if pace and live_inet and y_inet else "")
             + ("" if live_inet or tot is None else scope_tag("includes home network (tracker not switched yet)"))
             + spark(inet_hist + ([pace] if pace and live_inet else [])),
             "none", "Data to and from the internet today." if live_inet or tot is None
             else "The energy tracker hasn't switched to internet-only counting, so this still includes home transfers."),
        card("Yesterday", "calendar-arrow-left", big(gb(ytot) if ytot is not None else "—", "", None)
             + split_bar(t.get("yesterday_rx"), t.get("yesterday_tx"))
             + ("" if y_inet or ytot is None else scope_tag(lan_note)),
             "none", "A full day, from the energy tracker's daily report."),
    ]
    # ---- this month: only internet-only days (+ today) count towards the headline and pace
    m_inet = internet_days(this_m, since)
    m_lan = [r for r in this_m if not is_internet(r["date"], since)]
    m_tot = sum(day_total(r) for r in m_inet) + (tot or 0 if live_inet else 0)
    m_rx = sum(r["rx_bytes"] or 0 for r in m_inet) + ((t.get("today_rx") or 0) if live_inet else 0)
    m_tx = sum(r["tx_bytes"] or 0 for r in m_inet) + ((t.get("today_tx") or 0) if live_inet else 0)
    days_done = len(m_inet) + (hours / 24 if live_inet else 0)
    dim = calendar.monthrange(today.year, today.month)[1]
    mpace = m_tot / days_done * dim if m_tot and days_done >= 1 else None
    prev_inet = bool(prev_m) and all(is_internet(r["date"], since) for r in prev_m)
    prev_tot = sum(day_total(r) for r in prev_m) if prev_inet else None
    if not m_lan:
        since_txt = "since the 1st"
    elif m_inet:
        since_txt = f"since {nice_day(max(first_full, today.replace(day=1)))}"
    else:
        since_txt = "today so far"  # the switch was today or yesterday: no full internet-only day yet
    lan_rows = ""
    if m_lan:
        last_lan = max(int(r["date"][8:]) for r in m_lan)
        lan_label = f"1–{last_lan} {today:%b} (incl. home network)"
        lan_rows = f'<div class="rows" style="margin-top:8px">{row(lan_label, gb(sum(day_total(r) for r in m_lan)))}</div>'
    cards.append(card(f"This month ({today:%B})", "calendar-month",
                      big(gb(m_tot) if days_done else "—", since_txt, None,
                          f"on pace for about {gb(mpace)} for the month" if mpace else "Pace shows after the first full day")
                      + split_bar(m_rx, m_tx)
                      + (trend(mpace, prev_tot, pm_end.strftime("%B"), neutral=True) if mpace and prev_tot else "")
                      + lan_rows,
                      "none", "Internet data this month. Older days that also counted home transfers are listed separately."
                      if m_lan else "Internet data since the 1st."))
    cards.append(card("What counts?", "information-outline",
                      '<div class="rows">'
                      + row("Counted", "Discord bots, Minecraft players via playit, updates, Docker pulls, GitHub deploys", "ok")
                      + row("Not counted", "Immich uploads at home, dashboard, MC Panel and SSH from home", None)
                      + "</div>",
                      "none", "Only traffic that leaves your home network, the part your ISP counts. "
                      "Docker-internal and Tailscale traffic is left out too."))
    legend = [("Downloaded", D), ("Uploaded", U), (f"Up to {nice_day(since)}: includes home network", GD)]

    def series(days):
        inet = [is_internet(r["date"], since) for r in days]
        return [("Downloaded", D, [(r["rx_bytes"] or 0) / 1e9 if i else 0 for r, i in zip(days, inet)]),
                ("Uploaded", U, [(r["tx_bytes"] or 0) / 1e9 if i else 0 for r, i in zip(days, inet)]),
                ("Downloaded (incl. home)", GD, [0 if i else (r["rx_bytes"] or 0) / 1e9 for r, i in zip(days, inet)]),
                ("Uploaded (incl. home)", GU, [0 if i else (r["tx_bytes"] or 0) / 1e9 for r, i in zip(days, inet)])]

    last7 = ydays[-7:]
    any_lan7 = any(not is_internet(r["date"], since) for r in last7)
    cards.append(card("Last 7 days", "chart-bar",
                      bar_chart([_day_label(r["date"]) for r in last7], series(last7),
                                lambda v: f"{v:.1f} GB" if v < 10 else f"{v:.0f} GB",
                                value_labels=[gb(day_total(r)) for r in last7],
                                legend=legend if any_lan7 else legend[:2]),
                      "none", "Internet data per day, split into downloaded and uploaded."
                      + (f" Grey bars are from before the switch on {nice_day(since)} and also include home transfers." if any_lan7 else ""),
                      span=2))
    last30 = ydays[-30:]
    inet30 = [day_total(r) / 1e9 for r in internet_days(last30, since)]
    any_lan30 = len(inet30) < len(last30)
    cards.append(card("Last 30 days", "chart-bar",
                      bar_chart([datetime.fromisoformat(r["date"]).strftime("%d %b") for r in last30], series(last30),
                                lambda v: f"{v:.0f} GB" if v >= 1 else f"{v:.1f} GB",
                                avg=sum(inet30) / len(inet30) if len(inet30) >= 3 else None,
                                legend=legend if any_lan30 else legend[:2]),
                      "none", ((f"Internet-only since {nice_day(since)}: {gb(sum(inet30) * 1e9)} over {len(inet30)} full "
                                f"day{'s' if len(inet30) != 1 else ''}, about {gb(sum(inet30) / len(inet30) * 1e9)} a day."
                                if inet30 else
                                f"Internet-only counting started on {nice_day(since)}; full days show here in colour from {nice_day(first_full)}.")
                               + (" Grey bars also include home transfers." if any_lan30 else "")),
                      span=2))
    return page("traffic", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Immich
def panel_immich(i):
    i = i if isinstance(i, dict) else {}
    qa, qw = i.get("queue_active"), i.get("queue_waiting")
    lv_q = None if qa is None else ("ok" if qa + qw == 0 else "warn")
    du = i.get("disk_used_percent")
    lv_d = level(du, 80, 90)
    err = i.get("error")
    cards = [
        card("Photos", "image-multiple", big(num(i.get("photos")), "photos", None, f"{num(i.get('videos'))} videos"),
             "none", "Everything backed up from your phones to Immich."),
        card("Library size", "folder-image", big(gb(i.get("usage_bytes")), "", None),
             "none", "Space your photos and videos take, including thumbnails."),
        card("Background work", "progress-clock",
             big("Idle" if lv_q == "ok" else (f"{qw}" if qa is not None else "—"), "" if lv_q == "ok" else "jobs waiting", lv_q,
                 "Nothing to process" if lv_q == "ok" else (f"{qa} running now" if qa is not None else "")),
             lv_q, "After uploads Immich makes thumbnails and reads photo details. A queue for a while after uploading is normal."),
        card("Photo disk", "harddisk", big(f"{du:.0f}" if du is not None else "—", "% used", lv_d, f"{i.get('disk_free') or '—'} free")
             + bar(du, lv_d, "", ""), lv_d if du is not None else None,
             "Space left on the drive Immich stores photos on."),
    ]
    if err:
        cards.append(card("Connection", "alert-circle-outline", f'<div class="sub">{esc(err)}</div>', "warn",
                          "Add IMMICH_API_KEY to the repo secrets so the dashboard can read Immich.", span=4))
    return page("immich", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Bots and automation
def container_card(c, title, ic, what):
    if not c:
        return card(title, ic, big("Not found", "", "bad"), "bad", what)
    if c["state"] != "running":
        return card(title, ic, big("Stopped", "", "bad", esc(c.get("status") or "")), "bad", what)
    lv = "warn" if c.get("restarts") else "ok"
    return card(title, ic, big("Running", "", "ok", f"up {esc(c.get('uptime') or '?')}")
                + '<div class="rows">' + row("Memory", gb(c.get("mem_bytes")))
                + row("Restarts", "none" if not c.get("restarts") else f"{c['restarts']}× (it crashed and came back)", lv) + "</div>",
                lv, what)


def panel_bots(d, tr, g):
    cs = {c["name"]: c for c in (d.get("containers") or [])} if ok_dict(d) else {}
    cards = [
        container_card(cs.get("pvt-discord-bot"), "pvt-discord-bot", "robot-happy-outline", "Your private Discord bot, running in Docker."),
        container_card(cs.get("mc-discord-bot"), "mc-discord-bot", "minecraft", "Discord bot for the Minecraft server, running in Docker."),
    ]
    st = tr.get("status") if ok_dict(tr) else None
    lv = {"running": "ok", "stale": "warn", "stopped": "bad"}.get(st)
    cards.append(card("Energy tracker", "lightning-bolt-circle",
                      big({"running": "Running", "stale": "Stuck", "stopped": "Stopped"}.get(st, "—"), "", lv,
                          f"last reading {ago(tr.get('last_update')) or 'unknown'}" if ok_dict(tr) else ""),
                      lv, "Measures power and traffic every 30 s and posts a daily report to Discord. Feeds the Power and Traffic tabs."))
    g = g if isinstance(g, dict) else {}
    rs = g.get("runner_status")
    lv = {"idle": "ok", "busy": "ok", "offline": "bad"}.get(rs)
    res = g.get("last_run_status")
    lv_d = "ok" if res == "success" else "bad" if res in ("failure", "cancelled", "timed_out") else ("warn" if res else None)
    cards.append(card("Auto-deploy", "rocket-launch-outline",
                      big({"idle": "Ready", "busy": "Deploying", "offline": "Offline"}.get(rs, "—"), "", lv, f"runner {esc(g.get('runner') or '')}")
                      + '<div class="rows">'
                      + row("Last deploy", f"{esc(g.get('last_run_repo') or '—')}", None)
                      + row("Result", esc(res or "—"), lv_d)
                      + row("When", ago(g.get("last_run_at")) or "—") + "</div>",
                      lv_d if lv == "ok" else lv,
                      "Pushing to main on GitHub makes this laptop rebuild that app automatically." +
                      (f' <span style="color:{WARNC}">{esc(g["error"])}</span>' if g.get("error") else "")))
    worlds = [c for c in (d.get("containers") or []) if c.get("world")] if ok_dict(d) else []
    up = sum(1 for w in worlds if w["state"] == "running")
    rows_ = "".join(row(esc(w["name"]), (f"Running · up {esc(w.get('uptime') or '?')} · {gb(w.get('mem_bytes'))} RAM"
                                         if w["state"] == "running" else "Stopped (managed by MC Panel)"),
                        "ok" if w["state"] == "running" else None) for w in worlds)
    cards.append(card("Minecraft worlds", "minecraft",
                      big(f"{up} of {len(worlds)}", "running", None, "worlds created in MC Panel") + f'<div class="rows">{rows_}</div>'
                      if worlds else big("None", "", None, "no MC Panel worlds found"),
                      "none", "Each world is its own container, started and stopped from MC Panel. "
                      "A stopped world is normal and never raises an alert.", span=4))
    return page("bots", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Docker
def panel_docker(d):
    if not ok_dict(d):
        return page("docker", card("Docker", "docker", big("Unavailable", "", "bad"), "bad",
                                   esc(str((d or {}).get("error", ""))), span=4))
    cs = d["containers"]
    unexpected = d.get("down") or []
    lv = "bad" if unexpected else "ok"
    total_mem = sum(c.get("mem_bytes") or 0 for c in cs)
    mx = max([c.get("mem_bytes") or 0 for c in cs] + [1])
    trs = []
    for c in cs:
        if c["state"] == "running":
            clv = "warn" if c.get("restarts") else "ok"
            st = "Running" + (" (world)" if c.get("world") else "")
        elif c.get("world"):
            clv, st = None, "Stopped (world, managed by MC Panel)"
        elif c.get("off_ok"):
            clv, st = None, "Stopped (on purpose)"
        else:
            clv, st = "bad", "Stopped"
        mem = c.get("mem_bytes")
        trs.append(f'<tr><td><span class="dot" style="--c:{LEVEL_COLOUR[clv]}"></span>{esc(c["name"])}</td>'
                   f'<td>{st}</td><td>{esc(c.get("uptime") or "—")}</td>'
                   f'<td class="r">{gb(mem) if mem else "—"}<span class="mini"><i style="width:{(mem or 0) / mx * 100:.0f}%"></i></span></td>'
                   f'<td class="r">{c.get("restarts") or 0}</td></tr>')
    table = ('<table><thead><tr><th>App</th><th>State</th><th>Up for</th><th class="r">Memory</th><th class="r">Restarts</th></tr></thead>'
             f'<tbody>{"".join(trs)}</tbody></table>')
    parts = [("Images", d.get("disk_images"), "#60a5fa"), ("Volumes (data)", d.get("disk_volumes"), "#a78bfa"),
             ("Build cache", d.get("disk_build_cache"), "#94a3b8")]
    tot = sum(p[1] or 0 for p in parts) or 1
    seg = "".join(f'<i style="width:{(v or 0) / tot * 100:.1f}%;background:{c};border-radius:0"></i>' for _, v, c in parts)
    leg = "".join(f'<div class="row"><span class="k"><span class="dot" style="--c:{c}"></span>{n}</span><span class="v">{gb(v)}</span></div>' for n, v, c in parts)
    cards = [
        card("Apps running", "docker", big(f"{d['running']} of {d['expected']}", "running", lv,
                                           ("Down: " + ", ".join(esc(n) for n in unexpected)) if unexpected else "Everything that should run is running")
             + bar(d["running"] / max(d["expected"], 1) * 100, lv), lv,
             "Each app runs in its own Docker container. Stopped Minecraft worlds and apps switched off on purpose are not counted."),
        card("Memory used by apps", "memory", big(gb(total_mem), "", None, "all containers together"),
             "none", "The server has about 7.6 GB. Immich is usually the biggest user."),
        card("Docker disk use", "harddisk", big(gb(d.get("disk_total")), "", None)
             + f'<div class="bar" style="display:flex;margin-top:10px">{seg}</div><div class="rows" style="margin-top:8px">{leg}</div>',
             "none", f"<b>{gb(d.get('disk_reclaimable'))}</b> could be freed with <code>docker system prune</code> (unused images and cache).", span=2),
        card("All containers", "format-list-bulleted", table, "none",
             "Green = running fine · amber = restarted at least once · grey = stopped on purpose (incl. MC Panel worlds) · red = stopped unexpectedly.", span=4),
    ]
    return page("docker", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Security
def panel_security(s):
    s = s if ok_dict(s) else {}
    f = s.get("failed_ssh_24h")
    lv = None if f is None else ("ok" if f == 0 else level(f, 1, 20))
    ufw = s.get("ufw")
    lv_f = {"enabled": "ok", "disabled": "warn"}.get(ufw)
    pl = s.get("playit")
    lv_p = {"running": "ok", "stopped": "bad"}.get(pl)
    cards = [
        card("Failed SSH logins", "shield-key", big(f if f is not None else "—", "in the last 24 h", lv,
                                                   "No one tried a wrong password" if f == 0 else ("someone typed a wrong password or user name" if f else "auth.log not readable")),
             lv, "Each one is a login to the server that failed. A few could be you mistyping; many in a short time means someone is guessing passwords.",
             span=2),
        card("Firewall", "wall-fire", big({"enabled": "On", "disabled": "Off"}.get(ufw, "—"), "", lv_f, "ufw"),
             lv_f, "Blocks connections to ports you have not opened. " + (
                 "Off is low risk on a home network behind your router, and ports published by Docker skip ufw anyway. "
                 "To turn it on: <code>sudo ufw allow OpenSSH</code> then <code>sudo ufw enable</code>."
                 if ufw == "disabled" else "Note: ports published by Docker skip ufw rules.")),
        card("playit tunnel", "tunnel", big({"running": "Running", "stopped": "Not running"}.get(pl, "—"), "", lv_p, "playitd"),
             lv_p, "Lets friends outside your home join the Minecraft server. If it is down, they cannot connect."),
    ]
    return page("security", f'<div class="grid">{"".join(cards)}</div>')


# ------------------------------------------------------------------ Home overview
EMOJI_LEVEL = {"🔴": "bad", "🟡": "warn", "🟢": "ok"}


def tile(section, title, ic, value, unit="", lv=None, gauge=None, meaning="", extra="", sparkline=""):
    colour = f' style="color:{LEVEL_COLOUR[lv]}"' if lv else ""
    u = f"<small>{esc(unit)}</small>" if unit else ""
    g = ""
    if gauge is not None:
        g = (f'<div class="bar"><i style="width:{max(0, min(100, gauge)):.1f}%;'
             f'background:{LEVEL_COLOUR[lv] if lv else "var(--accent)"}"></i></div>')
    return (f'<a class="tile" style="--accent:{ACCENT[section]}" href="#{section}">'
            f'<div class="th">{icon(ic, size=20)}<span>{esc(title)}</span>'
            f'{pill(lv) if lv else ""}</div>'
            f'<div class="tv"{colour}>{esc(str(value))}{u}</div>{extra}{g}{sparkline}'
            f'<div class="tm">{meaning}</div></a>')



def panel_home(sysd, bat, hist, e, rows, live, t, d, imm, g, attention, ld=None, since=None):
    s = sysd if ok_dict(sysd) else {}
    today = date.today().isoformat()
    ydays = [r for r in (rows or []) if r["date"] < today]

    # ---- banner
    if not attention:
        c, title, sub, ic = OKC, "All good", "Nothing needs your attention right now.", "check-bold"
        issues = ""
    else:
        worst = "bad" if any(a[0] == "🔴" for a in attention) else "warn"
        c = LEVEL_COLOUR[worst]
        n = len(attention)
        title = f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} attention"
        sub, ic = "Click one to open its tab.", "alert" if worst == "bad" else "alert-outline"
        issues = '<div class="issues">' + "".join(
            f'<a class="issue" href="#{tab}" style="--c:{LEVEL_COLOUR[EMOJI_LEVEL.get(lv, "warn")]}">'
            f'{esc(what)}: {esc(detail)}</a>' for lv, what, detail, tab in attention) + "</div>"
    banner = (f'<div class="banner" style="--c:{c}"><div class="bi">{icon(ic, c, 26)}</div>'
              f'<div><h2>{title}</h2><p>{sub}</p>{issues}</div></div>')

    # ---- vitals
    temps = [h[3] for h in hist]
    loads = [h[1] for h in hist]
    rams = [h[2] for h in hist]
    tc, cpu, rp, dp = s.get("cpu_temp_c"), s.get("cpu_percent"), s.get("ram_percent"), s.get("disk_percent")
    b = bat if ok_dict(bat) else {}
    ch, hl, st = b.get("charge_percent"), b.get("health_percent"), (b.get("status") or "").lower()
    lv_b = "bad" if st == "discharging" else level(hl, 60, 40, False)
    vit = [
        tile("health", "CPU temperature", "thermometer", f"{tc:.0f}" if tc is not None else "—", "°C", level(tc, 75, 90),
             ((tc or 30) - 30) / 70 * 100, "Fine up to about 80 °C", sparkline=spark(temps, LEVEL_COLOUR[level(tc, 75, 90)] if tc else MUTED)),
        tile("health", "CPU load", "chip", f"{cpu:.0f}" if cpu is not None else "—", "%", level(cpu, 70, 90), cpu,
             "How busy the processor is", sparkline=spark(loads)),
        tile("health", "Memory", "memory", f"{rp:.0f}" if rp is not None else "—", "% used", level(rp, 80, 92), rp,
             f"{gb(s.get('ram_used_bytes'))} of {gb(s.get('ram_total_bytes'))}", sparkline=spark(rams)),
        tile("health", "Disk", "harddisk", f"{dp:.0f}" if dp is not None else "—", "% used", level(dp, 80, 90), dp,
             f"{gb((s.get('disk_total_bytes') or 0) - (s.get('disk_used_bytes') or 0))} free"),
        tile("health", "Battery", "battery-heart-variant", f"{ch:.0f}" if ch is not None else "—", "% charged", lv_b, ch,
             ("On battery! Check power" if st == "discharging" else
              f"Health {hl:.0f}% of new" if hl is not None else "Backup during power cuts")),
        tile("health", "Uptime", "timer-outline", duration(s.get("uptime_seconds")).split(",")[0], "", "ok" if s.get("uptime_seconds") else None,
             None, f"Since {when(s.get('last_reboot'))}"),
    ]

    # ---- services row
    e = e if ok_dict(e) else {}
    tk, yk = e.get("today_kwh"), e.get("yesterday_kwh")
    hours = (live or {}).get("hours") or 0
    pace = tk / hours * 24 if tk is not None and hours > 1 else None
    tt = t if ok_dict(t) else {}
    tot = tt.get("today_total")
    tpace = tot / hours * 24 if tot and hours > 1 else None
    live_inet = (live or {}).get("traffic_scope") == "internet"
    since = since or date.today()
    y_inet = bool(ydays) and ydays[-1]["date"] == (date.today() - timedelta(days=1)).isoformat() and is_internet(ydays[-1]["date"], since)
    power = tile("power", "Power today", "flash", f"{tk:.2f}" if tk is not None else "—", "kWh", None, None,
                 (f"₹{e.get('today_cost_inr') or 0:.2f} so far · bill ≈ ₹{e.get('projected_bill_inr') or 0:.0f}/month"
                  if tk is not None else "No live reading: apply the energy-tracker patch"),
                 extra=trend(pace, yk, "yesterday") if pace else "",
                 sparkline=spark([r["total_kwh"] for r in ydays[-14:]] + ([pace] if pace else [])))
    traffic = tile("traffic", "Internet today", "earth", gb(tot) if tot is not None else "—", "", None, None,
                   f"↓ {gb(tt.get('today_rx'))} down · ↑ {gb(tt.get('today_tx'))} up"
                   + ("" if live_inet or tot is None else " · incl. home network"),
                   extra=trend(tpace, tt.get("yesterday_total"), "yesterday", neutral=True) if tpace and live_inet and y_inet else "",
                   sparkline=spark([day_total(r) for r in internet_days(ydays, since)][-14:]))
    if ok_dict(d):
        down, expected = d.get("down") or [], d.get("expected") or d["total"]
        lv_c = "bad" if down else "ok"
        cont = tile("docker", "Apps running", "docker", f"{d['running']}/{expected}", "", lv_c, d["running"] / max(expected, 1) * 100,
                    ("Down: " + ", ".join(down)) if down else "Every app is up")
    else:
        cont = tile("docker", "Apps running", "docker", "—", "", None, None, "Docker not reachable")
    i = imm if isinstance(imm, dict) else {}
    qa, qw = i.get("queue_active"), i.get("queue_waiting")
    busy = qa is not None and (qa + qw) > 0
    immich = tile("immich", "Immich", "image-multiple", num(i.get("photos")), "photos",
                  None if qa is None else ("warn" if busy else "ok"), None,
                  (f"Processing: {qw} waiting" if busy else "Idle, nothing to process") if qa is not None else (i.get("error") or "Not connected"),
                  extra=f'<div class="tx">{num(i.get("videos"))} videos · {gb(i.get("usage_bytes"))}</div>')
    g = g if isinstance(g, dict) else {}
    rs, res = g.get("runner_status"), g.get("last_run_status")
    lv_r = {"idle": "ok", "busy": "ok", "offline": "bad"}.get(rs)
    mark = "✅" if res == "success" else "❌" if res in ("failure", "cancelled", "timed_out") else "⏳" if res else ""
    runner = tile("bots", "GitHub runner", "rocket-launch-outline", {"idle": "Idle", "busy": "Busy", "offline": "Offline"}.get(rs, "—"), "",
                  lv_r, None, f"Last deploy {ago(g.get('last_run_at')) or '—'}",
                  extra=f'<div class="tx">{mark} {esc(g.get("last_run_repo") or "no deploys yet")}</div>')

    quick = [l for l in (ld or {}).get("links", []) if l["group"] != "External"]
    links = ('<div class="links">' + "".join(app_link(l) for l in quick) + "</div>" if quick else
             '<p class="empty">No apps with a web page found yet. Any container that publishes a port shows up here.</p>')

    body = (banner + '<div class="sec">Server vitals</div><div class="g6">' + "".join(vit) + "</div>"
            + '<div class="sec">Services</div><div class="g5">' + power + traffic + cont + immich + runner + "</div>"
            + '<div class="sec">Quick links</div>' + links)
    return page("home", body)


# ------------------------------------------------------------------ Links (auto-discovered)
GROUP_INFO = {
    "Apps": ("apps", "Web apps running on the server."),
    "Minecraft": ("minecraft", "MC Panel and every Minecraft world. Worlds have no web page; start and stop them in MC Panel."),
    "Monitoring": ("chart-areaspline", "Tools that watch the server."),
    "Projects": ("folder-wrench-outline", "Your own apps. Anything new with a published port appears here automatically."),
    "External": ("web", "Useful sites outside the server."),
}
STATE_LEVEL = {"running": "ok", "restarting": "warn", "paused": "warn"}


def state_dot(state):
    lv = STATE_LEVEL.get(state, "bad")
    word = {"ok": "Running", "warn": state.capitalize()}.get(lv, "Stopped")
    return f'<span class="sdot" style="--c:{LEVEL_COLOUR[lv]}" title="{word}"></span>'


def app_link(l, big=False):
    """A discovered app. The browser fills in href from its own hostname + port (web/app.js)."""
    c = l["color"]
    new = '<span class="newb">NEW</span>' if l.get("new") else ""
    stopped = "" if l["state"] == "running" else '<span class="stopped">Stopped</span>'
    url = (f'<span class="url"><span data-hostport>…</span>{esc(l["path"]) if l["path"] != "/" else ""}</span>' if big else "")
    return (f'<a class="lnk applink{" big" if big else ""}" href="#links" target="_blank" rel="noopener" '
            f'data-port="{int(l["port"])}" data-path="{esc(l["path"])}" data-scheme="{esc(l["scheme"])}">'
            f'<span class="li" style="background:color-mix(in srgb,{c} 18%,transparent)">{icon(l["icon"], c, 26 if big else 22)}</span>'
            f'<span class="lt"><b>{esc(l["name"])}{new}</b><span>{esc(l["description"])}</span>{url}</span>'
            f'{stopped}{state_dot(l["state"])}</a>')


def ext_link(name, desc, url, ic, c):
    return (f'<a class="lnk big" href="{esc(url)}" target="_blank" rel="noopener">'
            f'<span class="li" style="background:color-mix(in srgb,{c} 18%,transparent)">{icon(ic, c, 26)}</span>'
            f'<span class="lt"><b>{esc(name)}</b><span>{esc(desc)}</span>'
            f'<span class="url">{esc(url.split("://", 1)[-1])}</span></span></a>')


def world_tile(w):
    up = w["state"] == "running"
    lv = "ok" if up else (None if w["managed"] else "bad")  # stopped MC Panel worlds are normal
    note = ("Running" if up else "Stopped") + (" · managed by MC Panel" if w["managed"] else " · standalone server")
    return (f'<div class="lnk big world"><span class="li" style="background:color-mix(in srgb,#4ade80 14%,transparent)">'
            f'{icon("minecraft", "#4ade80" if up else MUTED, 26)}</span>'
            f'<span class="lt"><b>{esc(w["name"])}</b><span>{note}</span><span class="url">{esc(w.get("status") or "")}</span></span>'
            f'<span class="sdot" style="--c:{LEVEL_COLOUR[lv]}"></span></div>')


def panel_links(ld):
    from links import EXTERNAL, GROUP_ORDER
    ld = ld if isinstance(ld, dict) else {}
    groups = {}
    for l in ld.get("links", []):
        groups.setdefault(l["group"], []).append(app_link(l, big=True))
    for w in ld.get("worlds", []):
        groups.setdefault("Minecraft", []).append(world_tile(w))
    groups["External"] = [ext_link(*x) for x in EXTERNAL]
    names = [g for g in GROUP_ORDER if g in groups] + sorted(g for g in groups if g not in GROUP_ORDER)
    out = []
    if "error" in ld:
        out.append(f'<p class="empty">Couldn\'t read Docker to find apps ({esc(str(ld["error"]))}).</p>')
    for g in names:
        ic, desc = GROUP_INFO.get(g, ("folder-outline", "Grouped with the homelab.group label."))
        out.append(f'<div class="lgroup"><h2>{icon(ic, "#94a3b8", 18)}{esc(g)} <small>{len(groups[g])}</small></h2>'
                   f'<p>{desc}</p><div class="links l3">{"".join(groups[g])}</div></div>')
    n = len(ld.get("links", []))
    out.append(f'<p class="ex" style="margin-top:18px">Found {n} app{"s" if n != 1 else ""} automatically from Docker: '
               'every container that publishes a port gets a link, except databases and this dashboard. '
               'Green = running, red = stopped, NEW = first seen in the last 24 h. '
               'Customise one with Docker labels like <code>homelab.name</code>, <code>homelab.icon</code>, '
               '<code>homelab.group</code> or hide it with <code>homelab.hide=true</code>.</p>')
    return page("links", "".join(out))
