// swastikserver dashboard: hash-routed tabs; each tab is an HTML fragment from /panel/<id>,
// re-fetched every few seconds and swapped in place (no page reloads, no framework).
(() => {
  const MDI = (n) => `https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg/${n}.svg`;
  const TABS = [
    { id: "home", name: "Home", icon: "home-variant", accent: "#38bdf8", every: 15,
      sub: "Everything important at a glance. Click any tile for details." },
    { id: "health", name: "Server health", icon: "laptop", accent: "#2dd4bf", every: 15,
      sub: "Is the laptop itself OK? Load, memory, disk, heat, battery and updates." },
    { id: "power", name: "Power and cost", icon: "flash", accent: "#f59e0b", every: 30,
      sub: "Electricity the server uses, what it costs, and how that compares with before." },
    { id: "traffic", name: "Traffic", icon: "swap-vertical", accent: "#38bdf8", every: 30,
      sub: "Data the server sent and received, including home-network transfers like Immich uploads." },
    { id: "immich", name: "Immich", icon: "image-multiple", accent: "#f472b6", every: 30,
      sub: "Your photo library: how big it is and whether it is busy." },
    { id: "bots", name: "Bots and automation", icon: "robot-happy-outline", accent: "#a78bfa", every: 20,
      sub: "Discord bots, the energy tracker and automatic deploys from GitHub." },
    { id: "docker", name: "Docker", icon: "docker", accent: "#60a5fa", every: 20,
      sub: "Every app container on the server and the disk space Docker uses." },
    { id: "security", name: "Security", icon: "shield-check", accent: "#34d399", every: 30,
      sub: "Login attempts, firewall and the playit tunnel." },
    { id: "links", name: "Links", icon: "link-variant", accent: "#94a3b8", every: 0,
      sub: "The other apps running on the server." },
  ];
  const byId = Object.fromEntries(TABS.map((t) => [t.id, t]));
  const nav = document.getElementById("tabs");
  const view = document.getElementById("view");
  const head = document.getElementById("sechead");
  const stamp = document.getElementById("stamp");
  let current = null, timer = null, lastOk = 0, seq = 0;

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

  async function load(first) {
    const t = current, my = ++seq;
    try {
      const r = await fetch(`/panel/${t.id}?_=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const html = await r.text();
      if (my !== seq || t !== current) return;  // user switched tab meanwhile
      view.innerHTML = html;
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
    const id = (location.hash || "#home").slice(1);
    const t = byId[id] || byId.home;
    if (t === current) return;
    current = t;
    document.documentElement.style.setProperty("--accent", t.accent);
    nav.querySelectorAll("a").forEach((a) => a.classList.toggle("on", a.dataset.id === t.id));
    nav.querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    head.innerHTML = t.id === "home" ? "" : `<h1>${t.name}</h1><p>${t.sub}</p>`;
    view.innerHTML = `<p class="empty">Loading ${t.name}…</p>`;
    window.scrollTo({ top: 0 });
    clearInterval(timer);
    load(true);
    if (t.every) timer = setInterval(() => { if (!document.hidden) load(false); }, t.every * 1000);
  }

  document.addEventListener("visibilitychange", () => { if (!document.hidden && current?.every) load(false); });
  window.addEventListener("hashchange", show);
  show();
})();
