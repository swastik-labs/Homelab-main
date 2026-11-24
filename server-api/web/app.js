// swastikserver dashboard: hash-routed tabs; each tab is an HTML fragment from /panel/<id>,
// re-fetched every few seconds and swapped in place (no page reloads, no framework).
// Extra routes: #incidents?status=open&range=7d, #incident-<id>, #logs-<container>.
(() => {
  const MDI = (n) => `https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg/${n}.svg`;
  const TABS = [
    { id: "home", name: "Home", icon: "home-variant", accent: "#38bdf8", every: 15,
      sub: "Everything important at a glance. Click any tile for details." },
    { id: "health", name: "Server health", icon: "laptop", accent: "#2dd4bf", every: 15,
      sub: "Is the laptop itself OK? Load, memory, disk, heat, battery and updates." },
    { id: "power", name: "Power and cost", icon: "flash", accent: "#f59e0b", every: 30,
      sub: "Electricity the server uses, what it costs, and how that compares with before." },
    { id: "traffic", name: "Internet usage", icon: "earth", accent: "#38bdf8", every: 30,
      sub: "Only data to and from the internet, the part your ISP counts. Home transfers like Immich uploads aren't included." },
    { id: "immich", name: "Immich", icon: "image-multiple", accent: "#f472b6", every: 30,
      sub: "Your photo library: how big it is and whether it is busy." },
    { id: "bots", name: "Bots and automation", icon: "robot-happy-outline", accent: "#a78bfa", every: 20,
      sub: "Discord bots, the energy tracker and automatic deploys from GitHub." },
    { id: "docker", name: "Docker", icon: "docker", accent: "#60a5fa", every: 20,
      sub: "Every app container on the server and the disk space Docker uses." },
    { id: "security", name: "Security", icon: "shield-check", accent: "#34d399", every: 30,
      sub: "Login attempts, firewall and the playit tunnel." },
    { id: "incidents", name: "Incidents", icon: "alert-octagon-outline", accent: "#f87171", every: 20,
      sub: "What broke, when, and why: the logs and details saved at that moment. Kept for 30 days." },
    { id: "links", name: "Links", icon: "link-variant", accent: "#94a3b8", every: 0,
      sub: "The other apps running on the server." },
  ];
  const byId = Object.fromEntries(TABS.map((t) => [t.id, t]));
  const nav = document.getElementById("tabs");
  const view = document.getElementById("view");
  const head = document.getElementById("sechead");
  const stamp = document.getElementById("stamp");
  let current = null, route = null, timer = null, lastOk = 0, seq = 0;

  nav.innerHTML = TABS.map((t) =>
    `<a href="#${t.id}" data-id="${t.id}" style="--accent:${t.accent}">` +
    `<span class="ic" style="-webkit-mask-image:url(${MDI(t.icon)});mask-image:url(${MDI(t.icon)})"></span>${t.name}</a>`
  ).join("");

  const clock = (d) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });

  // Discovered app links carry only a port; the address is whatever this page was opened with,
  // so the same links work at home (192.168.31.125) and over Tailscale.
  function fillLinks(root) {
    const host = location.hostname;
    root.querySelectorAll("a[data-port]").forEach((a) => {
      const scheme = a.dataset.scheme === "https" ? "https" : "http";
      const path = (a.dataset.path || "/").startsWith("/") ? a.dataset.path || "/" : "/";
      a.href = `${scheme}://${host}:${a.dataset.port}${path}`;
      a.querySelectorAll("[data-hostport]").forEach((s) => { s.textContent = `${host}:${a.dataset.port}`; });
    });
  }

  // "#incident-12" -> incidents tab, /panel/incident/12 ; "#incidents?status=open" -> /panel/incidents?status=open
  function resolve(hash) {
    const [id, qs] = (hash || "home").split("?");
    let m;
    if ((m = id.match(/^incident-(\d+)$/))) return { tab: byId.incidents, path: `/panel/incident/${m[1]}` };
    if ((m = id.match(/^logs-([A-Za-z0-9_.-]{1,128})$/))) return { tab: byId.incidents, path: `/panel/logs/${m[1]}` };
    const tab = byId[id] || byId.home;
    return { tab, path: `/panel/${tab.id}` + (qs && tab.id === "incidents" ? `?${qs}` : "") };
  }

  async function load(first) {
    const r0 = route, my = ++seq;
    try {
      const sep = r0.path.includes("?") ? "&" : "?";
      const r = await fetch(`${r0.path}${sep}_=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const html = await r.text();
      if (my !== seq || r0 !== route) return;  // user switched page meanwhile
      // keep opened log sections open and the scroll position of log boxes across refreshes
      const opened = [...view.querySelectorAll("details[data-keep][open]")].map((d) => d.id);
      const scrolls = Object.fromEntries([...view.querySelectorAll("pre.logs[id]")].map((p) => [p.id, p.scrollTop]));
      view.innerHTML = html;
      opened.forEach((id) => { const d = document.getElementById(id); if (d) d.open = true; });
      Object.entries(scrolls).forEach(([id, top]) => { const p = document.getElementById(id); if (p) p.scrollTop = top; });
      fillLinks(view);
      lastOk = Date.now();
      stamp.textContent = `Live · updated ${clock(new Date())}`;
      document.body.classList.remove("stale");
    } catch (e) {
      document.body.classList.add("stale");
      stamp.textContent = `Can't reach the server${lastOk ? ` · last update ${clock(new Date(lastOk))}` : ""}`;
      if (first) view.innerHTML = `<p class="empty">Couldn't load this page (${e.message}). Retrying…</p>`;
    }
  }

  function show() {
    const hash = (location.hash || "#home").slice(1);
    const next = resolve(hash);
    if (route && next.path === route.path) return;
    const tabChanged = next.tab !== current;
    current = next.tab;
    route = next;
    document.documentElement.style.setProperty("--accent", current.accent);
    nav.querySelectorAll("a").forEach((a) => a.classList.toggle("on", a.dataset.id === current.id));
    nav.querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    head.innerHTML = current.id === "home" ? "" : `<h1>${current.name}</h1><p>${current.sub}</p>`;
    if (tabChanged || !route.path.startsWith("/panel/incidents")) {
      view.innerHTML = `<p class="empty">Loading ${current.name}…</p>`;
      window.scrollTo({ top: 0 });
    }
    clearInterval(timer);
    load(true);
    if (current.every) timer = setInterval(() => { if (!document.hidden) load(false); }, current.every * 1000);
  }

  // Copy buttons (logs). navigator.clipboard needs https or localhost, so fall back to a hidden textarea.
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    const src = document.getElementById(btn.dataset.copy);
    if (!src) return;
    const lines = src.querySelectorAll(":scope > span");  // logs: one span per line
    const text = lines.length ? [...lines].map((s) => s.textContent).join("\n") : src.textContent;
    let done = false;
    try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); done = true; } } catch (e) { /* fall back */ }
    if (!done) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { done = document.execCommand("copy"); } catch (e) { done = false; }
      ta.remove();
    }
    btn.textContent = done ? "Copied ✓" : "Select and copy manually";
    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
  });

  document.addEventListener("visibilitychange", () => { if (!document.hidden && current?.every) load(false); });
  window.addEventListener("hashchange", show);
  show();
})();
