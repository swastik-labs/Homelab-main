"""Auto-discovered links: every container that publishes a host port becomes a link.

The browser builds each URL from its own hostname plus the host port (see web/app.js), so
the same links work on the LAN and over a VPN such as Tailscale. Nothing here knows an IP.

Docker labels on a container override everything (all optional):
  homelab.name, homelab.description, homelab.icon (Material Design Icons name),
  homelab.color, homelab.group, homelab.port (host port, if it publishes several),
  homelab.path (e.g. /admin), homelab.scheme (http|https), homelab.hide=true
"""
import os
import re
import time

GROUP_ORDER = ["Apps", "Minecraft", "Monitoring", "Projects", "External"]

# (match: substrings of the image or container name, name, description, icon, colour, group, web port inside the container)
CATALOG = [
    (("immich-server", "immich_server"), "Immich", "Photos and videos", "image-multiple", "#f472b6", "Apps", 2283),
    (("beszel",), "Beszel", "System monitoring", "chart-areaspline", "#2dd4bf", "Monitoring", 8090),
    (("mcpanel", "mc-panel"), "MC Panel", "Minecraft server panel", "minecraft", "#4ade80", "Minecraft", 8000),
    (("jellyfin",), "Jellyfin", "Movies, shows and music", "filmstrip", "#a78bfa", "Apps", 8096),
    (("adguardhome", "adguard"), "AdGuard Home", "Network-wide ad blocking", "shield-check", "#34d399", "Apps", 80),
    (("vaultwarden",), "Vaultwarden", "Password manager", "shield-key", "#60a5fa", "Apps", 80),
    (("dozzle",), "Dozzle", "Live container logs", "text-box-search-outline", "#fbbf24", "Monitoring", 8080),
    (("dockge",), "Dockge", "Manage compose stacks", "docker", "#60a5fa", "Monitoring", 5001),
    (("uptime-kuma",), "Uptime Kuma", "Website and service uptime", "heart-pulse", "#4ade80", "Monitoring", 3001),
    (("navidrome",), "Navidrome", "Music streaming", "music-circle", "#38bdf8", "Apps", 4533),
    (("forgejo",), "Forgejo", "Git hosting", "source-branch", "#fb923c", "Apps", 3000),
    (("gitea",), "Gitea", "Git hosting", "source-branch", "#4ade80", "Apps", 3000),
    (("code-server",), "code-server", "VS Code in the browser", "microsoft-visual-studio-code", "#38bdf8", "Apps", None),
    (("n8n",), "n8n", "Workflow automation", "robot-outline", "#f87171", "Apps", 5678),
    (("pocketbase",), "PocketBase", "Backend in one file", "database", "#e2e8f0", "Apps", 8090),
    (("umami",), "Umami", "Website analytics", "chart-bar", "#a78bfa", "Monitoring", 3000),
    (("excalidraw",), "Excalidraw", "Whiteboard and sketches", "draw", "#a78bfa", "Apps", 80),
    (("it-tools",), "IT-Tools", "Handy developer tools", "tools", "#38bdf8", "Apps", 80),
]
# Databases and caches never get a link, even if they publish a port.
DB_WORDS = ("postgres", "pgvecto", "redis", "valkey", "mysql", "mariadb", "mongo", "memcached")
MINECRAFT_IMAGE = "itzg/minecraft-server"
GENERIC = ("application-outline", "#94a3b8", "Projects")
NEW_FOR = 24 * 3600


def image_repo(image):
    """'ghcr.io/immich-app/immich-server:v2.3.1@sha256:..' -> 'ghcr.io/immich-app/immich-server'"""
    image = (image or "").lower().split("@", 1)[0]
    slash = image.rfind("/")
    colon = image.rfind(":")
    return image[:colon] if colon > slash else image


def is_db(repo):
    last = repo.rsplit("/", 1)[-1]
    return any(w in last for w in DB_WORDS)


def catalog_entry(repo, name):
    hay = f"{repo} {name.lower()}"
    for match, *rest in CATALOG:
        if any(m in hay for m in match):
            return rest
    return None


def _label(labels, key):
    v = labels.get(f"homelab.{key}")
    return v.strip() if isinstance(v, str) and v.strip() else None


def clean_icon(v):
    if not v:
        return None
    v = re.sub(r"^mdi[-:]", "", v.lower())
    return v if re.fullmatch(r"[a-z0-9-]{2,60}", v) else None


def clean_color(v):
    return v if v and re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}", v) else None


def clean_path(v):
    if not v:
        return "/"
    v = v if v.startswith("/") else "/" + v
    return v if re.fullmatch(r"[A-Za-z0-9/_\-.~%?=&#+]{1,200}", v) else "/"


def published_ports(c, inspect):
    """[(host_port, container_port)] for TCP ports reachable from the network (not 127.0.0.1 only)."""
    out = set()
    for p in c.get("Ports") or []:
        if p.get("PublicPort") and (p.get("Type") or "tcp") == "tcp" and not str(p.get("IP") or "").startswith(("127.", "::1")):
            out.add((int(p["PublicPort"]), int(p.get("PrivatePort") or 0)))
    if not out and c.get("State") != "running":
        # stopped containers don't list ports; read them from the container's config
        try:
            bindings = ((inspect(c["Id"]) or {}).get("HostConfig") or {}).get("PortBindings") or {}
        except Exception:
            bindings = {}
        for spec, binds in bindings.items():
            cport, _, proto = spec.partition("/")
            if (proto or "tcp") != "tcp":
                continue
            for b in binds or []:
                hp, ip = str(b.get("HostPort") or ""), str(b.get("HostIp") or "")
                if hp.isdigit() and not ip.startswith(("127.", "::1")):
                    out.add((int(hp), int(cport) if cport.isdigit() else 0))
    return sorted(out)


def choose_port(ports, wanted_label, web_port):
    if wanted_label and wanted_label.isdigit():
        w = int(wanted_label)
        for hp, cp in ports:
            if w in (hp, cp):
                return hp
        if 0 < w < 65536:
            return w
    if web_port:
        for hp, cp in ports:
            if cp == web_port:
                return hp
    return ports[0][0] if ports else None


def discover(containers, inspect, self_id, first_seen, now=None):
    """-> {"links": [...], "worlds": [...]} from the Docker /containers/json list."""
    now = now or time.time()
    links, worlds = [], []
    for c in containers:
        name = ((c.get("Names") or ["?"])[0]).lstrip("/")
        labels = c.get("Labels") or {}
        repo = image_repo(c.get("Image"))
        state = c.get("State") or "unknown"
        first_seen.setdefault(name, now)
        if (_label(labels, "hide") or "").lower() in ("true", "1", "yes"):
            continue
        if name == "homelab-dashboard" or (self_id and len(self_id) >= 12 and c.get("Id", "").startswith(self_id)):
            continue  # the dashboard itself
        if labels.get("mcpanel") == "1" or MINECRAFT_IMAGE in repo:
            worlds.append({"name": _label(labels, "name") or name, "state": state, "status": c.get("Status") or "",
                           "managed": labels.get("mcpanel") == "1"})
            continue
        if is_db(repo) and not _label(labels, "port"):
            continue
        ports = published_ports(c, inspect)
        if not ports and not _label(labels, "port"):
            continue  # bots, agents, internal services
        known = catalog_entry(repo, name)
        nice, desc, ic, colour, group, web_port = known if known else (name, "Your project", *GENERIC, None)
        port = choose_port(ports, _label(labels, "port"), web_port)
        if not port:
            continue
        scheme = (_label(labels, "scheme") or "http").lower()
        seen = first_seen.get(name, now)
        links.append({
            "name": _label(labels, "name") or nice,
            "description": _label(labels, "description") or desc,
            "icon": clean_icon(_label(labels, "icon")) or ic,
            "color": clean_color(_label(labels, "color")) or colour,
            "group": (_label(labels, "group") or group)[:24],
            "port": port,
            "path": clean_path(_label(labels, "path")),
            "scheme": scheme if scheme in ("http", "https") else "http",
            "state": state,
            "container": name,
            "known": bool(known),
            "new": bool(seen) and now - seen < NEW_FOR,
        })
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    links.sort(key=lambda l: (order.get(l["group"], 50), l["group"], l["name"].lower()))
    worlds.sort(key=lambda w: w["name"].lower())
    return {"links": links, "worlds": worlds}


_ORG = os.environ.get("GITHUB_ORG", "")
EXTERNAL = ([("GitHub", f"{_ORG} organisation", f"https://github.com/{_ORG}", "github", "#e2e8f0")] if _ORG else []) + [
    ("playit.gg", "Tunnel for Minecraft friends", "https://playit.gg", "tunnel", "#34d399"),
    ("Discord", "Bot servers", "https://discord.com/app", "message-text", "#818cf8"),
]
