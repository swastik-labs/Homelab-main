"""Incidents: open/resolve problems, keep the evidence, and send phone alerts through ntfy.

app.py supplies the problem list (the same one that drives Home's "needs attention" banner)
and a capture function; this module decides when something becomes an incident, stores it
(SQLite in DATA_DIR, so it survives redeploys), and talks to ntfy.
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

KEEP_DAYS = 30
LOG_CAP = 200 * 1024        # bytes of logs kept per incident
DEBOUNCE = 60               # a problem must last this long before it becomes an incident (crash loops/OOM: at once)
RESOLVE_AFTER = 60          # ...and must be gone this long before the incident is resolved
DETECTED = {"crash_loop": "Crash loop detected", "container_down": "Down detected", "oom": "Out-of-memory kill detected",
            "unhealthy": "Failing health check detected"}
REMIND = 3 * 3600           # "still happening" reminder interval
NTFY_PRIORITY = {"urgent": 5, "high": 4}
NTFY_TAGS = {"urgent": ["rotating_light"], "high": ["warning"]}

# ------------------------------------------------------------------ redaction
_SECRET_PATTERNS = [
    (re.compile(r"[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}"), "[discord-token]"),
    (re.compile(r"https?://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+", re.I), "[discord-webhook]"),
    (re.compile(r"https?://hooks\.slack\.com/\S+", re.I), "[webhook]"),
    (re.compile(r"\btk_[A-Za-z0-9]{10,}"), "[ntfy-token]"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}"), "[github-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"(?i)\b(x-api-key|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|dsn)"
                r"(\"?\s*[:=]\s*\"?)(?!\[)([^\s\"',;&]{4,})"), r"\1\2[redacted]"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1[redacted]@"),  # user:password@host
]


def redact(text):
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


# ------------------------------------------------------------------ logs
_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\s?")
_EXC = re.compile(r"^(?:[A-Za-z_][\w.]*\.)?[A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt|Fault|Failure)\b(?::.*)?$")
# a real log level, not just the word "error" somewhere in an info line
_LEVEL = re.compile(r"\b(?:ERROR|CRITICAL|FATAL|PANIC|EMERG|SEVERE)\b"
                    r"|(?i:\blevel[=:]\s*\"?(?:error|critical|fatal|panic)\b)"
                    r"|(?i:\[(?:error|crit|critical|fatal|panic|emerg)\])"
                    r"|(?i:^\s*(?:error|fatal|critical|panic)\s*[:|\]])")
_FATAL = re.compile(r"(?i)segmentation fault|core dumped|out of memory|oom[- ]?kill|no space left on device"
                    r"|address already in use|unhandled (?:promise )?rejection|uncaught exception|^panic:"
                    r"|cannot find module|exited with code [1-9]|permission denied")
_BENIGN = re.compile(r"✅|✔|☑|\b(?:INFO|DEBUG|TRACE|NOTICE)\b|(?i:\benabled\b|\bsuccess(?:ful(?:ly)?)?\b|\b(?:0|no) errors?\b)")


def is_error_line(line):
    """True for lines that look like a real failure (used for highlighting and the headline)."""
    l = strip_ts(line).strip()
    if not l or _BENIGN.search(l):
        return False
    return bool(_EXC.match(l) or _LEVEL.search(l) or _FATAL.search(l))


def normalize_error(line):
    """Make an error line comparable across repeats: no timestamps, ids, hex or numbers."""
    s = strip_ts(line or "").strip().lower()
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", s)
    s = re.sub(r"\d{4}-\d\d-\d\d[t ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?z?", "<time>", s)
    s = re.sub(r"\b0x[0-9a-f]+\b|\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", "<hex>", s)
    s = re.sub(r"\d+(?:\.\d+)?", "<n>", s)
    return re.sub(r"\s+", " ", s)[:300]


def strip_ts(line):
    return _TS.sub("", line)


def demux_docker_logs(raw):
    """Docker's log stream for non-TTY containers has an 8-byte header per frame."""
    if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\x00\x00\x00":
        out, i = [], 0
        while i + 8 <= len(raw):
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + size])
            i += 8 + size
        raw = b"".join(out)
    return raw.decode("utf-8", "replace")


def find_error(lines):
    """-> (index, text) of the line that best explains the failure, or (None, None).

    In order: the final exception of the last Python traceback, a bare "XxxError: ..." line,
    the last line at ERROR/CRITICAL/FATAL level, the last known fatal pattern. Info lines
    (✅, INFO, "enabled", "success") never count, so "Sentry Error Tracking: ENABLED" is ignored.
    Nothing real found -> (None, None); the page then says so instead of inventing a headline."""
    plain = [strip_ts(l).rstrip() for l in lines]
    tb = max((i for i, l in enumerate(plain) if "Traceback (most recent call last)" in l), default=None)
    if tb is not None:
        for i in range(tb + 1, len(plain)):
            l = plain[i]
            if l and not l.startswith((" ", "\t")) and _EXC.match(l):
                return i, l.strip()
    for test in (lambda l: _EXC.match(l), lambda l: _LEVEL.search(l), lambda l: _FATAL.search(l)):
        for i in range(len(plain) - 1, -1, -1):
            l = plain[i].strip()
            if l and not _BENIGN.search(l) and test(l):
                return i, l[:300]
    return None, None


def cap_logs(text):
    b = text.encode("utf-8")
    if len(b) <= LOG_CAP:
        return text
    return "[… older lines cut to keep the incident small …]\n" + b[-LOG_CAP:].decode("utf-8", "ignore")


EXIT_MEANING = {0: "it exited normally", 1: "the app crashed with an error", 2: "the app was started wrongly (bad arguments)",
                125: "Docker couldn't start it", 126: "the command couldn't run", 127: "the command wasn't found",
                130: "it was interrupted (Ctrl+C)", 134: "the app aborted", 137: "it was killed (out of memory or force-stopped)",
                139: "the app crashed (segmentation fault)", 143: "it was asked to stop (docker stop or shutdown)"}


def exit_meaning(code):
    if code is None:
        return "unknown"
    return EXIT_MEANING.get(int(code), "the app exited with an error")


# ------------------------------------------------------------------ storage
class Store:
    def __init__(self, data_dir):
        path = None
        if data_dir:
            try:
                os.makedirs(data_dir, exist_ok=True)
                test = os.path.join(data_dir, ".write-test")
                with open(test, "w") as f:
                    f.write("ok")
                os.remove(test)
                path = os.path.join(data_dir, "incidents.db")
            except OSError:
                path = None
        self.persistent = path is not None
        if not self.persistent:
            print(f"incidents: {data_dir or 'DATA_DIR'} not writable, keeping incidents in memory only", flush=True)
        self.lock = threading.Lock()
        try:
            self._open(path)
        except sqlite3.DatabaseError as e:  # corrupt file: keep it aside for inspection, start a fresh one
            bad = f"{path}.corrupt-{int(time.time())}"
            print(f"incidents: database unreadable ({e}); moved to {bad}", flush=True)
            try:
                self.db.close()
            except Exception:
                pass
            os.replace(path, bad)
            self._open(path)

    def close(self):
        with self.lock:
            self.db.close()

    def _open(self, path):
        self.db = sqlite3.connect(path or ":memory:", check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA quick_check").fetchone()
            self.db.execute("PRAGMA temp_store=MEMORY")
            self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")  # takes effect for a new database file
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS incidents (
                  id INTEGER PRIMARY KEY, key TEXT NOT NULL, kind TEXT, type TEXT, target TEXT,
                  severity TEXT, priority TEXT, title TEXT, summary TEXT, headline TEXT,
                  status TEXT NOT NULL, started REAL, ended REAL, last_seen REAL,
                  last_notified REAL, last_capture REAL, evidence TEXT, logs TEXT);
                CREATE INDEX IF NOT EXISTS inc_status ON incidents(status, key);
                CREATE INDEX IF NOT EXISTS inc_started ON incidents(started);
                CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE IF NOT EXISTS rules (
                  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, label TEXT,
                  created REAL, until REAL);
            """)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(incidents)").fetchall()}
            if "ignored" not in cols:  # older databases: add the column in place
                self.db.execute("ALTER TABLE incidents ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0")
            self.db.commit()

    def _row(self, r):
        if r is None:
            return None
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"] or "{}")
        return d

    def create(self, inc):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO incidents (key, kind, type, target, severity, priority, title, summary, headline, status,"
                " started, ended, last_seen, last_notified, last_capture, evidence, logs)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inc["key"], inc["kind"], inc["type"], inc["target"], inc["severity"], inc["priority"], inc["title"],
                 inc["summary"], inc.get("headline"), inc.get("status", "open"), inc["started"], inc.get("ended"),
                 inc.get("last_seen", inc["started"]), inc.get("last_notified"), inc.get("last_capture", inc["started"]),
                 json.dumps(inc.get("evidence") or {}), cap_logs(redact(inc.get("logs") or ""))))
            self.db.commit()
            return cur.lastrowid

    def update(self, iid, **fields):
        if "evidence" in fields:
            fields["evidence"] = json.dumps(fields["evidence"])
        if "logs" in fields:
            fields["logs"] = cap_logs(redact(fields["logs"] or ""))
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE incidents SET {cols} WHERE id=?", (*fields.values(), iid))
            self.db.commit()

    def get(self, iid):
        with self.lock:
            return self._row(self.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone())

    def open_incidents(self):
        with self.lock:
            return [self._row(r) for r in self.db.execute("SELECT * FROM incidents WHERE status='open'").fetchall()]

    def open_by_key(self, key):
        with self.lock:
            return self._row(self.db.execute(
                "SELECT * FROM incidents WHERE status='open' AND key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone())

    def list(self, since=0, status=None, kind=None, limit=300, ignored=False):
        q, args = "SELECT id, key, kind, type, target, severity, priority, title, summary, headline, status, started," \
                  " ended, last_notified, evidence, ignored FROM incidents WHERE (started >= ? OR status='open')", [since]
        q += " AND ignored != 0" if ignored else " AND ignored = 0"
        if status in ("open", "resolved"):
            q += " AND status=?"
            args.append(status)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY (status='open') DESC, started DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [self._row(r) for r in self.db.execute(q, args).fetchall()]  # with evidence, without logs

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]

    # ---- ignore / mute rules
    def add_rule(self, kind, value, label, until=None, now=None):
        now = now or time.time()
        with self.lock:
            old = self.db.execute("SELECT id FROM rules WHERE kind=? AND value=?", (kind, value)).fetchone()
            if old:  # same rule again: just refresh its expiry
                self.db.execute("UPDATE rules SET until=?, label=?, created=? WHERE id=?", (until, label, now, old[0]))
                rid = old[0]
            else:
                rid = self.db.execute("INSERT INTO rules (kind, value, label, created, until) VALUES (?,?,?,?,?)",
                                      (kind, value, label, now, until)).lastrowid
            self.db.commit()
        return rid

    def rules(self, now=None):
        now = now or time.time()
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM rules WHERE until IS NULL OR until > ? ORDER BY created DESC", (now,)).fetchall()]

    def delete_rule(self, rid):
        """Un-ignore: drop the rule and show its incidents again."""
        with self.lock:
            n = self.db.execute("DELETE FROM rules WHERE id=?", (rid,)).rowcount
            self.db.execute("UPDATE incidents SET ignored=0 WHERE ignored=?", (rid,))
            self.db.commit()
        return n

    def expire_rules(self, now=None):
        """Finished mutes: remove them and show their incidents again."""
        now = now or time.time()
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM rules WHERE until IS NOT NULL AND until <= ?", (now,))]
            for rid in ids:
                self.db.execute("DELETE FROM rules WHERE id=?", (rid,))
                self.db.execute("UPDATE incidents SET ignored=0 WHERE ignored=?", (rid,))
            self.db.commit()
        return ids

    def mark_ignored(self, rid, where, args=()):
        with self.lock:
            n = self.db.execute(f"UPDATE incidents SET ignored=? WHERE ignored=0 AND ({where})", (rid, *args)).rowcount
            self.db.commit()
        return n

    def headlines(self):
        with self.lock:
            return [(r[0], r[1]) for r in self.db.execute("SELECT id, headline FROM incidents WHERE headline IS NOT NULL AND ignored=0")]

    def delete_low(self):
        """Low-priority incidents from before 1.0 (they're Home-banner-only now): remove quietly."""
        with self.lock:
            n = self.db.execute("DELETE FROM incidents WHERE priority='low'").rowcount
            self.db.execute("DELETE FROM kv WHERE k LIKE 'lowsent:%'")
            self.db.commit()
        return n

    def kv_get(self, k, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def kv_set(self, k, v):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, str(v)))
            self.db.commit()

    def prune(self, now):
        cutoff = now - KEEP_DAYS * 86400
        with self.lock:
            self.db.execute("DELETE FROM incidents WHERE status='resolved' AND ended < ?", (cutoff,))
            self.db.commit()
            if self.persistent:
                self.db.execute("PRAGMA incremental_vacuum")


# ------------------------------------------------------------------ ntfy
class Notifier:
    """Publishes to a (private) ntfy server with an access token. Disabled when not configured."""

    def __init__(self, url, token, topic, public_url):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.topic = topic or "server-alerts"
        self.public_url = (public_url or "").rstrip("/")
        self.enabled = bool(self.url and self.token)
        self.last_error = None

    def send(self, title, message, priority, tags, incident_id=None):
        if not self.enabled:
            return False
        body = {"topic": self.topic, "title": title[:250], "message": message[:3500],
                "priority": NTFY_PRIORITY.get(priority, priority if isinstance(priority, int) else 3), "tags": tags}
        if incident_id and self.public_url:
            body["click"] = f"{self.public_url}/#incidents/{incident_id}"
        req = urllib.request.Request(self.url + "/", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    ok = 200 <= r.status < 300
                self.last_error = None if ok else f"HTTP {r.status}"
                return ok
            except Exception as e:  # never log the token: only the error type/status
                code = getattr(e, "code", None)
                self.last_error = f"HTTP {code}" if code else type(e).__name__
                if code or attempt == 2:  # the server answered (bad token, bad topic...): retrying won't help
                    print(f"ntfy: send failed ({self.last_error})", flush=True)
                    return False
                time.sleep(3)
        return False


# ------------------------------------------------------------------ monitor
def fmt_duration(secs):
    secs = int(max(0, secs))
    if secs < 90:
        return f"{secs} s"
    if secs < 5400:
        return f"{secs // 60} min"
    if secs < 172800:
        return f"{secs // 3600} h {secs % 3600 // 60} min"
    return f"{secs // 86400} days"


class Monitor:
    """Turns the current problem list into incidents, once per tick (~30 s).

    problem = {key, kind, type, target, level, priority, title, detail, tab, immediate, fingerprint}
    capture(problem) -> {"summary", "headline", "evidence": {...}, "logs": str}
    """

    def __init__(self, store, notifier, capture, error_rule=None):
        self.store, self.notifier, self._capture = store, notifier, capture
        self.error_rule = error_rule or (lambda headline: 0)  # -> rule id that ignores this error line, or 0
        self.pending = {}   # key -> first time seen (debounce)
        self.absent = {}    # key -> first time missing (resolve debounce)
        self.last_prune = 0

    def capture(self, p):
        try:
            return self._capture(p) or {}
        except Exception as e:
            print(f"incidents: capture failed for {p.get('key')}: {type(e).__name__}: {str(e)[:120]}", flush=True)
            return {"summary": p.get("detail") or p.get("title"), "headline": None,
                    "evidence": {"capture_error": f"{type(e).__name__}: {str(e)[:160]}"}, "logs": ""}

    def _timeline(self, ev, now, text):
        ev.setdefault("timeline", []).append({"ts": now, "text": text})
        ev["timeline"] = ev["timeline"][-60:]
        return ev

    def _notify_open(self, iid, p, cap, now, reminder=False, opened=None):
        pr = p["priority"]
        if reminder:
            title = f"Still happening: {p['title']}"
            msg = f"Open for {fmt_duration(now - opened)}. " + (cap.get("headline") or p.get("detail") or "")
        else:
            title = p["title"]
            msg = (cap.get("headline") + "\n" if cap.get("headline") else "") + (cap.get("summary") or p.get("detail") or "")
        return self.notifier.send(title, msg.strip(), pr, NTFY_TAGS.get(pr, ["warning"]), iid)

    def tick(self, problems, now=None, hold_kinds=()):
        """hold_kinds: incident kinds whose data source is down right now; they are never resolved meanwhile."""
        now = now or time.time()
        active = {p["key"]: p for p in problems}
        # new or continuing problems
        for key, p in active.items():
            self.absent.pop(key, None)
            inc = self.store.open_by_key(key)
            if inc:
                fields = {"last_seen": now, "title": p["title"], "priority": p["priority"], "severity": p["level"]}
                ev = inc["evidence"]
                if p.get("fingerprint") and p["fingerprint"] != ev.get("fingerprint") and now - (inc["last_capture"] or 0) > 120:
                    cap = self.capture(p)  # something changed (e.g. more restarts): refresh the evidence
                    ev2 = dict(cap.get("evidence") or {}, timeline=ev.get("timeline", []), fingerprint=p["fingerprint"])
                    self._timeline(ev2, now, f"Changed: {p.get('detail') or 'new details captured'}")
                    fields.update(evidence=ev2, logs=cap.get("logs") or "", last_capture=now,
                                  summary=cap.get("summary") or inc["summary"], headline=cap.get("headline") or inc["headline"])
                    ev = ev2
                interval = REMIND
                if not inc.get("ignored") and fields.get("headline") and self.error_rule(fields["headline"]):
                    fields["ignored"] = self.error_rule(fields["headline"])  # its new error line is ignored
                if inc.get("ignored") or fields.get("ignored"):
                    self.store.update(inc["id"], **fields)  # ignored/muted: keep it up to date, never notify
                    continue
                if self.notifier.enabled and inc["last_notified"] and now - inc["last_notified"] >= interval:
                    if self._notify_open(inc["id"], p, {"headline": inc["headline"]}, now, reminder=True, opened=inc["started"]):
                        fields["last_notified"] = now
                        self._timeline(ev, now, "Reminder sent to phone")
                        fields["evidence"] = ev
                self.store.update(inc["id"], **fields)
                continue
            first = self.pending.setdefault(key, now)
            if not p.get("immediate") and now - first < DEBOUNCE:
                continue
            self.pending.pop(key, None)
            cap = self.capture(p)
            ev = dict(cap.get("evidence") or {}, fingerprint=p.get("fingerprint"))
            self._timeline(ev, first, f"{DETECTED.get(p['type'], 'Problem detected')}: {p.get('detail') or p['title']}")
            for t in cap.get("events") or []:
                self._timeline(ev, t["ts"], t["text"])
            ev["timeline"].sort(key=lambda x: x["ts"])
            ignored_by = self.error_rule(cap.get("headline")) if cap.get("headline") else 0
            iid = self.store.create({"key": key, "kind": p["kind"], "type": p["type"], "target": p["target"],
                                     "severity": p["level"], "priority": p["priority"], "title": p["title"],
                                     "summary": cap.get("summary") or p.get("detail") or "", "headline": cap.get("headline"),
                                     "started": first, "last_seen": now, "last_capture": now, "evidence": ev,
                                     "logs": cap.get("logs") or ""})
            if ignored_by:  # "Ignore this error everywhere": recorded, hidden, never alerted
                self.store.update(iid, ignored=ignored_by)
                continue
            if self._notify_open(iid, p, cap, now):
                self._timeline(ev, now, "Alert sent to phone")
                self.store.update(iid, last_notified=now, evidence=ev)
        for key in list(self.pending):
            if key not in active:
                self.pending.pop(key)  # flapped back to normal before the debounce: no incident
        # resolve what has been gone long enough
        for inc in self.store.open_incidents():
            if inc["key"] in active or inc["kind"] in hold_kinds:
                self.absent.pop(inc["key"], None)
                continue
            gone = self.absent.setdefault(inc["key"], now)
            if now - gone < RESOLVE_AFTER:
                continue
            self.absent.pop(inc["key"], None)
            ev = self._timeline(inc["evidence"], gone, "Back to normal")
            dur = fmt_duration(gone - inc["started"])
            if inc["last_notified"] and self.notifier.enabled and not inc.get("ignored"):
                if self.notifier.send(f"Resolved: {inc['title']}", f"✅ resolved after {dur}", 3, ["white_check_mark"], inc["id"]):
                    self._timeline(ev, now, "Resolved message sent to phone")
            self.store.update(inc["id"], status="resolved", ended=gone, evidence=ev)
        if now - self.last_prune > 3600:
            self.store.prune(now)
            self.last_prune = now
