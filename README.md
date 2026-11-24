# swastikserver dashboard

A lightweight, from-scratch homelab dashboard for **swastikserver**, an old Lenovo laptop
(i3-1115G4, 7 GB RAM, Ubuntu 26.04) that runs Docker apps, Discord bots, Immich and a
Minecraft panel at home.

It answers "is everything OK?" at a glance, and explains each number in plain English:
what it means, whether it is good or bad (green / amber / red), and how it compares with
yesterday or last month.

**Open it:** <http://192.168.31.125:3000> (home network only, no login, read-only)

> Screenshots can be added here later.

---

## Pages

| Tab | What you see |
|---|---|
| **Home** | Status banner ("All good" or "N things need attention", with each problem listed). Vital tiles with gauges: CPU temperature, CPU load, memory, disk, battery, uptime. Service tiles: power today vs yesterday, internet used today, apps running, Immich, GitHub runner and last deploy. Sparklines and quick links to every discovered app. Every tile opens its tab. |
| **Server health** | Processor load, temperature, memory and disk with gauges and sparklines. A 24-hour history chart (load, memory, temperature). Battery charge and health, SSD temperature, pending updates and whether a restart is needed, last restart, busiest programs. |
| **Power and cost** | Energy used today (and the pace vs yesterday), yesterday, expected bill this month (vs last month), average power draw in watts. 7-day chart with ₹ per day and a 30-day chart with an average line. Priced at ₹5/kWh. |
| **Internet usage** | Only data to and from the internet, the part your ISP counts: today, yesterday and this month, with trends and the month's pace. 7-day and 30-day download/upload charts. Home transfers like Immich uploads aren't included. |
| **Immich** | Photo and video counts, library size, background processing queue, free space on the photo disk. |
| **Bots and automation** | pvt-discord-bot and mc-discord-bot (running, uptime, memory, restarts), the energy tracker service, the GitHub runner and the latest deploy, and the Minecraft worlds created in MC Panel. |
| **Docker** | Every container with state, uptime, memory and restart count. Docker disk use (images, volumes, build cache) and how much `docker system prune` could free. |
| **Security** | Failed SSH logins in the last 24 h, firewall (ufw) on/off, playit tunnel running. |
| **Incidents** | What broke, when, and why. Stat row (open now, last 7 days, the app with most incidents, 7-day uptime). Open incidents are pinned at the top as full cards: the error line from the logs, a plain-English explanation, exit code, restarts, out-of-memory and phone-alert facts, a timeline, and the logs around the error (Copy, Show all). Resolved ones are one-line cards that expand on click. Filters: All / Open / Resolved / Apps / System and 24 h / 7 d / 30 d, remembered. `#incidents/<id>` opens one incident; phone alerts link there. Home shows the 3 most recent. |
| **Links** | Found automatically from Docker and grouped (Apps, Minecraft, Monitoring, Projects, External), with a running/stopped dot and a NEW badge for apps first seen in the last 24 h. Minecraft worlds appear as status tiles. Home shows the same apps as quick-link buttons. |

Pages refresh themselves every 15 to 30 seconds without reloading, and the header shows when
the data was last updated.

## Stack

- **Frontend:** plain HTML, CSS and vanilla JavaScript (`server-api/web/`). No framework,
  no Node.js, no build step. Charts are inline SVG; icons come from the Material Design
  Icons CDN.
- **Backend:** one small Python 3.13 server using only the standard library
  (`server-api/app.py`, `server-api/panels.py`, `server-api/links.py`).
- **One container**, about **18 MB RAM** and near-zero CPU when idle (measured with
  `docker stats`).

## How it works

```
browser ──► :3000  homelab-dashboard container (Python)
                    ├─ serves the page (web/) and one HTML panel per tab (/panel/<tab>)
                    └─ reads host data, read-only:
                         /proc, /sys ............ CPU, memory, temperatures, battery, SSD, processes
                         / (mounted at /hostfs) . disk usage, apt updates, reboot-required,
                                                  /var/log/auth.log, /etc/ufw/ufw.conf,
                                                  energy-tracker SQLite DB + live.json
                         /var/run/docker.sock ... containers, memory, disk usage, app links
                         Immich API ............. photo counts, storage, queue
                         GitHub API ............. org runner status, latest workflow run
                    ├─ writes only to /data (volume dashboard-data): incidents.db
                    └─ sends phone alerts to your ntfy (optional)
```

- Everything is **read-only** except the `dashboard-data` volume at `/data`, where incidents
  are kept: the container has a read-only filesystem, all Linux capabilities dropped except
  `DAC_READ_SEARCH` (needed to read `auth.log`), and `no-new-privileges`.
- Every source is cached for 10 to 60 seconds. Missing files show as "Unknown" and never
  crash the page.
- CPU/memory/temperature history is kept in memory for 24 hours, so that chart starts
  empty after a restart.
- Stopped containers raise an alert, except:
  - Minecraft worlds created by MC Panel (Docker label `mcpanel=1`). They show as
    "Stopped (world, managed by MC Panel)" and are listed under Bots and automation.
  - Names in `IGNORE_STOPPED_CONTAINERS` in `docker-compose.yml` (currently
    `extraction-service`).
- Updates only alert for regular pending updates or a needed restart. Extra Ubuntu Pro
  (ESM) security fixes are shown as optional information.

## Links are found automatically

Every ~30 s the dashboard lists the Docker containers. Each container that **publishes a
host port** gets a link, built in the browser from the address you opened the dashboard
with plus that host port. So the same links work at home (`192.168.31.125`) and over
Tailscale, and no IP is hard-coded. For example, MC Panel publishes `8081 -> 8000`, so its
link is `http://<this host>:8081`.

- **Hidden automatically:** the dashboard itself, containers without published ports
  (bots, `beszel-agent`, Immich's database and cache), ports bound to `127.0.0.1` only, and
  database/cache images (postgres, redis, valkey, mysql, mariadb, mongo) even if they
  publish a port.
- **Known apps** get a proper name, description, icon, colour and group: Immich, Beszel,
  MC Panel, Jellyfin, AdGuard Home, Vaultwarden, Dozzle, Dockge, Uptime Kuma, Navidrome,
  Gitea, Forgejo, code-server, n8n, PocketBase, Umami, Excalidraw, IT-Tools. Anything else
  (like `bunkers`) shows under **Projects** with its container name and a generic icon.
- **Minecraft worlds** (label `mcpanel=1`, or the `itzg/minecraft-server` image) have no web
  page, so they show as status tiles in the Minecraft group.
- **NEW** marks apps first seen in the last 24 h. This is kept in memory, so right after
  the dashboard restarts nothing is NEW.
- **External** links (GitHub, playit.gg, Discord) are a short fixed list in
  `server-api/links.py`.

Override anything with Docker labels on the app's container (all optional):

```yaml
labels:
  homelab.name: "Bunkers"
  homelab.description: "Bunker planner"
  homelab.icon: "shield-home"        # any Material Design Icons name
  homelab.color: "#38bdf8"
  homelab.group: "Projects"          # Apps, Minecraft, Monitoring, Projects, or your own
  homelab.port: "3001"               # which host port, if it publishes several
  homelab.path: "/admin"
  homelab.scheme: "https"
  homelab.hide: "true"               # leave it out entirely
```

## Internet usage

The energy tracker counts **internet-only** traffic: nftables counters on the Wi-Fi
interface that leave out home/LAN, Docker, Tailscale and the home IPv6 /64. Its history
started fresh on 14 Nov 2026, so there is no older home-network data mixed in. Until the
first full day is recorded, the daily charts, month pace and bill estimate say
"Collecting data, the first full day appears tomorrow".

## Phone alerts (ntfy) and incidents

The same check that drives Home's "N things need attention" banner runs every 30 s. When a
problem lasts **60 s** (crash loops and out-of-memory kills count at once), it becomes an
**incident**: the dashboard saves the evidence right then and sends a phone alert through your
private ntfy. When it has been gone for 60 s, the incident is resolved and you get
"✅ resolved after X min".

| Priority | Problems |
|---|---|
| **Urgent** | an app/bot is down, crash-looping (3+ restarts in 15 min) or killed for running out of memory |
| **High** | app unhealthy, CPU ≥ 80 °C, SSD ≥ 65 °C, RAM ≥ 90 %, disk ≥ 85 %, running on battery, playit down, energy tracker stopped, runner offline, deploy failed, 20+ failed SSH logins |
| **Low** | updates waiting, restart needed, firewall off, a few failed SSH logins, energy tracker stuck. At most one phone alert per problem per day. |

- One message per problem, a reminder every 3 h while it lasts (low priority: once a day),
  and tapping the alert opens the incident on the dashboard (`/#incidents/<id>`).
- Stopped MC Panel worlds and `IGNORE_STOPPED_CONTAINERS` never alert.
- **Evidence for apps:** exit code and what it means, out-of-memory kill, restart count,
  health status and the last health-check output, Docker events (die/restart/oom), and the
  last 150 log lines. The line that explains the failure (the final exception of a Python
  traceback, or the last error line) becomes the incident's headline, for example
  `TypeError: Unknown option 'send_default_pii'`.
- **Evidence for server problems:** the value over the last hour and the busiest programs and
  apps at that moment.
- **Secrets are hidden** before anything is stored or shown: Discord tokens and webhooks,
  `tk_…` ntfy tokens, `Bearer …`, GitHub tokens, `password=…`, API keys and passwords in URLs.
- Incidents are stored in SQLite on the `dashboard-data` volume, so they survive redeploys.
  Logs are capped at 200 KB per incident and resolved incidents are deleted after 30 days.
- Alerts are **off** until `NTFY_URL` and `NTFY_TOKEN` are set. The token is never logged.

## Repository layout

```
docker-compose.yml          # the dashboard container (port 3000)
docker-compose.mock.yml     # override for testing on a PC with fake data
server-api/
  Dockerfile
  app.py                    # data collection, caching, HTTP server
  panels.py                 # renders each tab (HTML + inline SVG charts)
  links.py                  # finds app links from Docker; known-app catalog
  incidents.py              # incidents (SQLite), evidence, secret redaction, ntfy alerts
  web/                      # index.html, app.css, app.js, favicon.svg
.github/workflows/deploy.yml
.env.example
```

## Try it on Windows with mock data

Needs Docker Desktop. Mock mode uses realistic fake data (including example incidents such
as the `send_default_pii` crash loop) and mounts nothing from the host except its own
`dashboard-data` volume.

```bash
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build --remove-orphans
```

Open <http://localhost:3000>. Stop it with:

```bash
docker compose -f docker-compose.yml -f docker-compose.mock.yml down
```

## Deployment

Push to `main` (or run the workflow by hand from the Actions tab). The workflow runs on
the existing **self-hosted org runner `homelab-laptop`** on the server (user `swastik`, in
the `docker` group, no sudo). It:

1. checks out the repo,
2. writes `.env` from the repo secrets (never printed),
3. runs `docker compose up -d --build --remove-orphans`,
4. waits until the dashboard answers its health check,
5. deletes `.env`.

### Required repository secrets

Add them under **Settings → Secrets and variables → Actions**.

| Secret | What it is | Permissions (read-only) |
|---|---|---|
| `IMMICH_API_KEY` | Immich API key, created by an **admin** user in Immich → Account settings → API keys | `server.statistics`, `server.storage`, and `job.read` (`queue.read` on newer Immich versions). |
| `DASH_GITHUB_TOKEN` | Fine-grained personal access token, **resource owner: swastik-labs** | Organization: **Self-hosted runners: Read-only**. Repository access: all repositories, with **Actions: Read-only** and **Metadata: Read-only**. |
| `NTFY_URL` | Your ntfy server as the dashboard container reaches it, e.g. `http://192.168.31.125:8085` | — |
| `NTFY_TOKEN` | ntfy access token (`tk_…`) for a user that may **publish** to `server-alerts` | write access to the `server-alerts` topic only |

If a secret is missing, its cards say "not set" and the rest of the dashboard still works.
For a manual run on the server, copy `.env.example` to `.env` and fill it in. `.env` is
git-ignored.

## Energy tracker patch (`live.json`)

The Power and Internet usage pages read finished days from
`/home/swastik/energy-tracker/energy_tracker.db` (table `daily_reports`). The tracker keeps
today's running totals only in memory, so add this small patch to
`/home/swastik/energy-tracker/energy_report.py`. It writes `live.json` atomically on every
30-second poll and changes nothing else.

1. Add the imports (skip any that are already there):

```python
import json, os, tempfile
from datetime import datetime
```

2. Add this function at module level:

```python
LIVE_PATH = "/home/swastik/energy-tracker/live.json"

def write_live(day, cpu_kwh, total_kwh, cost, rx_bytes, tx_bytes, hours):
    """Atomically write today's running totals for the dashboard. Never raises."""
    try:
        data = {
            "date": str(day),
            "cpu_kwh_so_far": round(cpu_kwh, 5),
            "total_kwh_so_far": round(total_kwh, 5),   # includes SYSTEM_BASE_WATTS (9 W)
            "cost_so_far": round(cost, 3),
            "rx_bytes": int(rx_bytes),
            "tx_bytes": int(tx_bytes),
            "hours": round(hours, 3),
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LIVE_PATH), prefix=".live.")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, LIVE_PATH)
    except Exception as e:
        print(f"live.json write failed: {e}")
```

3. Call it at the end of each poll, after today's totals are updated. Use your script's
   variable names. If it already computes the day's total kWh and cost for the daily
   report, pass those so `live.json` and `daily_reports` always agree:

```python
write_live(
    day=today,
    cpu_kwh=today_cpu_kwh,
    total_kwh=today_cpu_kwh + SYSTEM_BASE_WATTS * hours_today / 1000,
    cost=(today_cpu_kwh + SYSTEM_BASE_WATTS * hours_today / 1000) * RATE_PER_KWH,
    rx_bytes=today_rx_bytes,
    tx_bytes=today_tx_bytes,
    hours=hours_today,
)
```

4. Restart the service and check the file appears within about 30 seconds:

```bash
sudo systemctl restart energy-tracker
```

```bash
cat /home/swastik/energy-tracker/live.json
```

(Use your actual unit name if it differs.) Until the patch is applied, "today" values show
"No live reading yet", and past days still work from the database.
