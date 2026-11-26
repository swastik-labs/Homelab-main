# Homelab dashboard

A lightweight dashboard for one Linux home server: system health, Docker apps, power and
internet use, Immich, bots, security, and **incidents with phone alerts** (ntfy) that tell you
what broke and why.

- **One container**, about 18 MB RAM, near-zero CPU when idle.
- **No framework, no build step:** a Python 3.13 standard-library server plus plain HTML, CSS and
  JavaScript.
- **Read-only by design:** a read-only container that sees the host through read-only mounts.
  The only writable place is its own data volume.
- Every integration is optional: anything you don't configure just shows "not set".

Version **1.0.0**.

---

## Contents

1. [What you get](#what-you-get)
2. [Requirements](#requirements)
3. [Install (step by step)](#install-step-by-step)
4. [Configuration reference](#configuration-reference)
5. [Optional integrations](#optional-integrations)
6. [Deploy with GitHub Actions (optional)](#deploy-with-github-actions-optional)
7. [Update, back up, uninstall](#update-back-up-uninstall)
8. [Incidents and phone alerts](#incidents-and-phone-alerts)
9. [Links are found automatically](#links-are-found-automatically)
10. [Try it with fake data](#try-it-with-fake-data)
11. [Troubleshooting](#troubleshooting)
12. [How it works and security](#how-it-works-and-security)

---

## What you get

| Tab | What you see |
|---|---|
| **Home** | "All good" or "N things need attention" with each problem listed. Gauges for CPU temperature and load, memory, disk, battery and uptime. Tiles for power, internet, apps, Immich and GitHub. Quick links to every app. |
| **Server health** | Load, temperature, memory and disk, with a 24-hour chart. Battery health, SSD temperature, pending updates, whether a restart is needed, and the busiest programs. |
| **Power and cost** | Energy used today, yesterday and this month, the expected bill, and 7-day and 30-day charts. Needs the [energy tracker](#energy-tracker). |
| **Internet usage** | Internet-only traffic for today, yesterday and this month, with charts. Needs the [energy tracker](#energy-tracker). |
| **Immich** | Photo and video counts, library size, the processing queue and free space. |
| **Bots and automation** | Bot containers, the energy tracker, the GitHub self-hosted runner and the last deploy, and Minecraft worlds. |
| **Docker** | Every container with its state, uptime, memory and restarts. Docker disk use and how much a prune could free. |
| **Security** | Failed SSH logins in the last 24 h, whether the ufw firewall is on, and whether the playit tunnel is running. |
| **Incidents** | What broke, when and why: the error line from the logs, the exit code explained, a timeline and the logs. Ignore and mute controls, with an optional PIN. |
| **Links** | Every app that publishes a port, found automatically. |

Pages refresh themselves every 15 to 30 seconds.

---

## Requirements

**Required**

| Tool | Version | Why | Check with |
|---|---|---|---|
| Linux server | any modern distro; tested on Ubuntu 24.04 to 26.04 | reads `/proc`, `/sys`, apt and ufw files | `uname -a` |
| Docker Engine | 24 or newer | runs the dashboard and is what it watches | `docker --version` |
| Docker Compose plugin | **v2.24.4 or newer** | `docker compose` (the mock file uses `!reset` and `!override`) | `docker compose version` |
| git | any | to download and update | `git --version` |
| A web browser | any current one | to open the dashboard | — |

The server needs internet access to build the image the first time (it pulls
`python:3.13-alpine`). Your browser loads icons from `cdn.jsdelivr.net`.

**Optional**

| Tool | Gives you |
|---|---|
| [ntfy](https://ntfy.sh) server, self-hosted, and the ntfy phone app | phone alerts for incidents |
| [Immich](https://immich.app) | the Immich tab |
| A GitHub organisation with a self-hosted Actions runner on the server | runner status, last deploy, and automatic deploys on push |
| An energy tracker that writes `energy_tracker.db` and `live.json` ([format](#energy-tracker)) | the Power and Internet usage tabs |
| ufw, playit.gg, MC Panel | their rows on the Security, Bots and Links tabs |
| Python 3.13 | only to run the tests or the server without Docker |

Nothing else is installed on the host. No Node.js, no database server.

---

## Install (step by step)

All commands run **on the server**, as a user that may use Docker (a member of the
`docker` group, or with `sudo`).

**1. Install Docker and the Compose plugin** (skip if `docker compose version` already
prints v2.24.4 or newer). Follow the official guide for your distro:
<https://docs.docker.com/engine/install/>. On Ubuntu that is the "Install using the apt
repository" section. Then let your user run Docker without sudo, and log out and back in:

```bash
sudo usermod -aG docker $USER
```

**2. Download the dashboard**

```bash
git clone https://github.com/<owner>/<repo>.git homelab-dashboard
```

```bash
cd homelab-dashboard
```

**3. Create your settings file**

```bash
cp .env.example .env
```

```bash
chmod 600 .env
```

Open `.env` in an editor (`nano .env`) and set at least these:

- `DASHBOARD_NAME`
- `TZ`
- `DASHBOARD_PUBLIC_URL`, which is `http://<server-ip>:3000`. Find the IP with
  `hostname -I`.

Leave everything else empty for now. [Configuration reference](#configuration-reference)
explains each line.

**4. Start it**

```bash
docker compose up -d --build
```

**5. Check it is healthy.** After about 20 seconds this should print `"ok": true`:

```bash
docker exec homelab-dashboard wget -qO- http://127.0.0.1:3000/healthz
```

**6. Open it** at `http://<server-ip>:3000` from any device on your network. If it doesn't
load from another device, allow the port in the firewall:

```bash
sudo ufw allow 3000/tcp
```

**7. Add the optional parts** you want: [phone alerts](#phone-alerts-ntfy),
[Immich](#immich), [GitHub](#github-runner-and-deploys), [energy tracker](#energy-tracker), and
an [admin PIN](#admin-pin-optional). After any change to `.env`, run `docker compose up -d` again.

> **Keep it on your home network.** The dashboard has no login for viewing. Don't forward
> port 3000 on your router. For access from outside, use a VPN such as Tailscale or
> WireGuard.

---

## Configuration reference

Everything lives in `.env` next to `docker-compose.yml`. Empty means off. Keep comments on
their own lines: text after a value becomes part of the value.

| Variable | Default | What it does |
|---|---|---|
| `DASHBOARD_NAME` | `homelab` | Name in the header and the browser tab. |
| `DASHBOARD_PORT` | `3000` | Port on the host. |
| `TZ` | `UTC` | Time zone for times and "today", e.g. `Asia/Kolkata`. |
| `DASHBOARD_PUBLIC_URL` | — | Address your phone uses to reach the dashboard. Tapping an alert opens `<this>/#incidents/<id>`. |
| `DASHBOARD_ADMIN_PIN` | — | **Secret, optional.** If set, the Ignore/Mute buttons ask for this PIN. Empty means anyone who can open the dashboard can use them. |
| `IGNORE_STOPPED_CONTAINERS` | — | Comma-separated container names that are stopped on purpose, so they never alert. |
| `NTFY_URL` | — | Your ntfy server, as the container reaches it, e.g. `http://192.168.1.10:8085`. |
| `NTFY_TOKEN` | — | **Secret.** ntfy access token (`tk_…`) that may publish to the topic. |
| `NTFY_TOPIC` | `server-alerts` | ntfy topic for alerts. |
| `IMMICH_URL` | — | Immich address, e.g. `http://192.168.1.10:2283`. |
| `IMMICH_API_KEY` | — | **Secret.** Immich API key. |
| `DASH_GITHUB_ORG` | — | GitHub organisation that owns your self-hosted runner. |
| `DASH_GITHUB_RUNNER` | first runner | Runner name to show. |
| `DASH_GITHUB_TOKEN` | — | **Secret.** Fine-grained, read-only GitHub token. |
| `ENERGY_DIR` | `/opt/energy-tracker` | Host folder with `energy_tracker.db` and `live.json`. |
| `RATE_INR_PER_KWH` | `5` | Electricity price per kWh, used for the cost. |

Never commit `.env`; it is already in `.gitignore`.

---

## Optional integrations

### Phone alerts (ntfy)

1. Run an ntfy server; see the [self-hosting docs](https://docs.ntfy.sh/install/). Turn on
   access control (`auth-default-access: deny-all`) so only your users can read and
   publish.
2. Create a user for the phone, a user for the dashboard, and the dashboard's token:

   ```bash
   ntfy user add phone
   ```

   ```bash
   ntfy user add dashboard
   ```

   ```bash
   ntfy access phone server-alerts read-only
   ```

   ```bash
   ntfy access dashboard server-alerts write-only
   ```

   ```bash
   ntfy token add dashboard
   ```

3. Put the server address in `NTFY_URL` and the `tk_…` token in `NTFY_TOKEN`, then run
   `docker compose up -d`.
4. On your phone, install the ntfy app. Add your server under Settings → Users (user
   `phone`), then subscribe to the `server-alerts` topic on your server.
5. Make urgent alerts ring: in the app, open the topic's settings and allow
   priority 5 and 4 notifications to make sound and break through Do Not Disturb if you want.
6. Test it: start a container that keeps crashing. Within about a minute you should get an
   alert and see an incident.

   ```bash
   docker run -d --name crashtest --restart always alpine sh -c "echo boom; exit 1"
   ```

   Clean up afterwards; the incident resolves a minute later:

   ```bash
   docker rm -f crashtest
   ```

### Admin PIN (optional)

Without a PIN, anyone who can open the dashboard can use the Ignore and Mute buttons. That is
fine on a home network you trust. If other people share your network, set
`DASHBOARD_ADMIN_PIN` in `.env` to a PIN of at least 6 digits, then run
`docker compose up -d`. The **Ignore…** menu then asks for the PIN once per browser and
remembers it for 30 days. See [Ignore and mute](#ignore-and-mute).

### Immich

1. In Immich, sign in as an **admin**. Go to Account settings → API keys → New API key.
2. Give it read-only permissions:
   - `server.statistics`
   - `server.storage`
   - `job.read` (called `queue.read` on newer versions)
3. Set `IMMICH_URL` and `IMMICH_API_KEY`, then run `docker compose up -d`.

### GitHub runner and deploys

1. Create a fine-grained personal access token at GitHub → Settings → Developer settings →
   Fine-grained tokens. Set **Resource owner** to your organisation, and give it:
   - Organization permissions: **Self-hosted runners: Read-only**.
   - Repository access: **All repositories**, with **Actions: Read-only** and
     **Metadata: Read-only**.
2. Set `DASH_GITHUB_ORG`, `DASH_GITHUB_RUNNER` (optional) and `DASH_GITHUB_TOKEN`, then run
   `docker compose up -d`.

### Energy tracker

The Power and Internet usage tabs read files from `ENERGY_DIR`. Any script can produce them:

- **`energy_tracker.db`** (SQLite), table `daily_reports`, one row per finished day:
  - `date` (`YYYY-MM-DD`)
  - `cpu_kwh`, `total_kwh`, `cost_inr`
  - `rx_bytes`, `tx_bytes` (internet traffic)
  - `hours`
- **`live.json`**, today's running totals, rewritten every 30 seconds or so:

```json
{"date": "2026-11-24", "cpu_kwh_so_far": 0.031, "total_kwh_so_far": 0.13, "cost_so_far": 0.65,
 "rx_bytes": 104857600, "tx_bytes": 20971520, "hours": 11.2,
 "updated_at": "2026-11-24T11:12:00+05:30"}
```

Write `live.json` atomically: write a temp file in the same folder, then `os.replace()` it.
The dashboard calls the tracker "running" when a process whose command line contains
`energy_report` exists and `live.json` is under 3 minutes old. Without these files the tabs
say "not set" and everything else still works.

### Docker labels

Optional labels on your own containers:

```yaml
labels:
  homelab.ignore: "true"        # never alert or open incidents for this container
  homelab.name: "Bunkers"       # link name on the Links tab
  homelab.description: "Bunker planner"
  homelab.icon: "shield-home"   # any Material Design Icons name
  homelab.color: "#38bdf8"
  homelab.group: "Projects"     # Apps, Minecraft, Monitoring, Projects, or your own
  homelab.port: "3001"          # which host port, if it publishes several
  homelab.path: "/admin"
  homelab.scheme: "https"
  homelab.hide: "true"          # leave it off the Links tab
```

Minecraft worlds made by MC Panel carry `mcpanel=1`. They show as worlds and are allowed to be
stopped.

---

## Deploy with GitHub Actions (optional)

Instead of running `docker compose up -d --build` by hand, you can deploy on every push to
`main`. `.github/workflows/deploy.yml` is included. It runs on a **self-hosted runner on the
server itself** and does these steps:

1. Runs the tests in mock mode; a failing test stops the deploy.
2. Writes `.env` from your repository secrets and variables (never printed).
3. Runs `docker compose up -d --build --remove-orphans`.
4. Waits for `/healthz`.
5. Deletes `.env`.

Set it up:

1. **Add a self-hosted runner** on the server under the user that runs Docker: repository
   (or organisation) → Settings → Actions → Runners → New self-hosted runner. Follow the
   commands GitHub shows, then install it as a service with `sudo ./svc.sh install` and
   `sudo ./svc.sh start`.
2. **Add secrets** under Settings → Secrets and variables → Actions → **Secrets**. Add only the
   ones you use:
   - `DASHBOARD_ADMIN_PIN`
   - `NTFY_TOKEN`
   - `IMMICH_API_KEY`
   - `DASH_GITHUB_TOKEN`
3. **Add variables** on the **Variables** tab of the same page. Use the same names as in
   [Configuration reference](#configuration-reference):
   - `DASHBOARD_NAME`, `DASHBOARD_PORT`, `TZ`, `DASHBOARD_PUBLIC_URL`
   - `IGNORE_STOPPED_CONTAINERS`
   - `NTFY_URL`, `NTFY_TOPIC`
   - `IMMICH_URL`
   - `DASH_GITHUB_ORG`, `DASH_GITHUB_RUNNER`
   - `ENERGY_DIR`, `RATE_INR_PER_KWH`
4. Push to `main`, or run the workflow by hand from the Actions tab.

> **Public repository?** GitHub warns against self-hosted runners on public repositories,
> because a pull request from a fork could run code on your server. Either keep the deploy
> repository private, or set Settings → Actions → General → "Fork pull request workflows" to
> require approval for all outside contributors. The workflow itself only runs on `push` to
> `main` and by hand.

---

## Update, back up, uninstall

**Update**

```bash
git pull
```

```bash
docker compose up -d --build
```

**Back up incidents and ignore rules.** They live in the Docker volume
`homelab-dashboard_dashboard-data`:

```bash
docker run --rm -v homelab-dashboard_dashboard-data:/data -v "$PWD":/backup alpine tar czf /backup/dashboard-data.tgz -C /data .
```

**Uninstall.** Removes the container. Add `-v` to delete the incidents volume too.

```bash
docker compose down
```

---

## Incidents and phone alerts

The check behind Home's "N things need attention" runs every 30 seconds. A problem becomes an
**incident** after it lasts 60 seconds; crash loops and out-of-memory kills count at once. The
dashboard saves the evidence right then and sends a phone alert. When the problem has been gone
for 60 seconds, the incident is resolved and you get "✅ resolved after X min".

| Priority | Problems | Phone |
|---|---|---|
| **Urgent** | an app is down, crash-looping (3+ restarts in 15 min), or killed for running out of memory | priority 5, rings |
| **High** | app unhealthy, CPU ≥ 80 °C, SSD ≥ 65 °C, RAM ≥ 90 %, disk ≥ 85 %, running on battery, playit down, energy tracker stopped, runner offline, deploy failed, 20+ failed SSH logins | priority 4 |
| **Home only** | updates waiting, restart needed, firewall off, a few failed SSH logins, energy tracker stuck | never: amber on Home only, no incident |

- One alert per problem, then a reminder every 3 hours while it lasts. Tapping an alert opens
  the incident.
- **Not alerted:**
  - containers you stopped yourself (`docker stop`, `docker kill`, `docker compose stop`)
  - stopped MC Panel worlds
  - names in `IGNORE_STOPPED_CONTAINERS`
  - one-off containers: Docker's random names like `nice_mcnulty`, no compose project, and
    stopped after under 2 minutes

  The Docker tab shows these in grey.
- **Evidence for apps:**
  - the exit code and what it means
  - out-of-memory kill
  - restarts
  - the health check
  - Docker events
  - the last 150 log lines

  The headline is the line that explains the failure, e.g. the final exception of a Python
  traceback. Info lines like `✅ Sentry Error Tracking: ENABLED` never count. If nothing real is
  found, the card says "No error found in the logs; it exited with code N".
- **Evidence for server problems:** the last hour of the value, and the busiest programs and
  apps at that moment.
- **Secrets are hidden** before anything is stored or shown: Discord tokens and webhooks, ntfy
  and GitHub tokens, `Bearer …`, `password=…`, API keys and passwords in URLs.
- Incidents are stored in SQLite on the data volume, so they survive restarts and updates. Logs
  are capped at 200 KB per incident, and resolved incidents are deleted after 30 days. If the
  database file is ever damaged, it is moved aside as `incidents.db.corrupt-<time>` and a fresh
  one is started.

### Ignore and mute

Every incident has an **Ignore…** menu. If you set an [admin PIN](#admin-pin-optional), it asks
for it first.

- **Ignore this container:** no more incidents or alerts for it. It still shows its real state,
  in grey with an "ignored" tag.
- **Ignore this error everywhere:** matches the error line. Numbers, ids and timestamps don't
  matter.
- **Mute for 1 h / 8 h / 24 h / 7 days:** the same, but temporary.

**Manage ignored**, under the filters, lists every rule with an **Un-ignore** button. Ignored
problems never alert, not even "resolved". The `homelab.ignore: "true"` label does the same as
config.

With a PIN, it is remembered per browser for 30 days in a signed, HttpOnly cookie. Five wrong
tries lock it for 15 minutes, and changing the PIN signs every browser out.

---

## Links are found automatically

Every container that **publishes a host port** gets a link. The browser builds it from the
address you opened the dashboard with plus that port, so the same links work on the LAN and
over a VPN, and no IP is stored anywhere.

- **Hidden:**
  - the dashboard itself
  - containers without published ports
  - ports bound to `127.0.0.1`
  - databases and caches (postgres, redis, valkey, mysql, mariadb, mongo)
- **Known apps** get a proper name and icon: Immich, Beszel, MC Panel, Jellyfin, AdGuard Home,
  Vaultwarden, Dozzle, Dockge, Uptime Kuma, Navidrome, Gitea, Forgejo, code-server, n8n,
  PocketBase, Umami, Excalidraw and IT-Tools. Anything else goes under **Projects**. Use
  [labels](#docker-labels) to change any of it.
- **NEW** marks apps first seen in the last 24 hours.

---

## Try it with fake data

Mock mode shows realistic fake data, including example incidents, and reads nothing from the
host. It works on Windows and macOS with Docker Desktop:

```bash
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
```

Open <http://localhost:3000>. Stop it with:

```bash
docker compose -f docker-compose.yml -f docker-compose.mock.yml down
```

Or without Docker, with Python 3.13. On Windows PowerShell, use `$env:MOCK=1` instead:

```bash
cd server-api && MOCK=1 DATA_DIR=./.data python app.py
```

**Tests** (mock mode, standard library only):

```bash
cd server-api && MOCK=1 DATA_DIR=/tmp/dash-test python -m unittest -v test_dashboard
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose` says `unknown tag !reset` | Compose is older than v2.24.4. Update the Docker Compose plugin. |
| Page doesn't load from another device | Check `docker compose ps`. Allow the port with `sudo ufw allow 3000/tcp`. Use the server's LAN IP, not `localhost`. |
| A card says "not set" | That integration's variables are empty in `.env`. Fill them in and run `docker compose up -d`. |
| Docker tab says "Unavailable" | `/var/run/docker.sock` isn't mounted, or rootless Docker uses another socket path. Mount yours at `/var/run/docker.sock`. |
| No phone alerts | Check `docker compose logs dashboard`. `ntfy: send failed (HTTP 403)` means the token can't publish to the topic; `URLError` means `NTFY_URL` isn't reachable from the container. The token is never logged. |
| Temperatures or battery show "Unknown" | The machine doesn't expose those sensors in `/sys`. That's normal on VMs. |
| Failed SSH logins show "Unknown" | The host has no `/var/log/auth.log`, which happens on journald-only systems. |
| Container is marked unhealthy or restarted itself | `/healthz` returns 503 when the incident monitor stops ticking, and after 5 minutes the process exits so Docker's restart policy starts it fresh. `docker compose logs dashboard` shows why (`watchdog: …` or `incident monitor failed: …`). |
| Everything else | `docker compose logs --tail 100 dashboard` |

---

## How it works and security

```
browser ──► :3000  homelab-dashboard container (Python, stdlib only)
                    ├─ serves the page (web/) and one HTML panel per tab (/panel/<tab>)
                    ├─ reads, read-only:  /proc, /sys, the host's / at /hostfs, the Docker socket,
                    │                     the Immich API, the GitHub API
                    ├─ writes only to /data (named volume): incidents.db
                    └─ sends phone alerts to your ntfy (optional)
```

- The container has a read-only filesystem, drops every Linux capability except
  `DAC_READ_SEARCH` (needed to read `auth.log`), and sets `no-new-privileges`. Memory is capped
  at 64 MB and Docker's log file at 3 MB.
- The Docker socket is mounted read-only, and the dashboard only ever sends `GET` requests.
  Note that any process with the Docker socket is powerful, so keep the dashboard on a trusted
  network.
- The only write actions (ignore and mute rules) need a custom request header and a JSON body,
  so other websites can't trigger them. If a PIN is set, they also need it, and PIN tries are
  rate-limited.
- Pages are sent with `Content-Security-Policy`, `X-Frame-Options: DENY` and `nosniff`.
- Each data source is cached for 15 to 60 seconds. A source that fails shows "Unknown" or
  "not set" and never breaks the page.
- `GET /healthz` reports the version, whether the incident monitor is alive and its last error. A
  watchdog restarts the process if the monitor hangs for 5 minutes. Read-only JSON
  is also available at:
  - `/health/system`, `/health/battery`, `/health/ssd`, `/health/updates`
  - `/energy`, `/traffic`
  - `/docker`, `/immich`, `/github`, `/security`, `/tracker`
  - `/links`, `/problems`, `/incidents`

### Repository layout

```
docker-compose.yml          # the dashboard container; settings come from .env
docker-compose.mock.yml     # override for trying it with fake data
.env.example                # every setting, documented
server-api/
  Dockerfile
  app.py                    # data collection, caching, incident monitor, HTTP server
  panels.py                 # renders each tab (HTML + inline SVG charts)
  links.py                  # finds app links from Docker; known-app catalog
  incidents.py              # incidents (SQLite), evidence, secret redaction, ntfy
  test_dashboard.py         # smoke tests (mock mode)
  web/                      # index.html, app.css, app.js, favicon.svg
.github/workflows/deploy.yml
```

## License

[MIT](LICENSE): free to use, change and share, with no warranty.
