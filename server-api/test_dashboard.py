"""Smoke tests, run in mock mode before every deploy:  MOCK=1 DATA_DIR=<tmp> python -m unittest -v"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

os.environ.setdefault("MOCK", "1")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["DASHBOARD_ADMIN_PIN"] = "4821"

import app  # noqa: E402
import incidents  # noqa: E402


class Pure(unittest.TestCase):
    def test_error_line_skips_info(self):
        lines = ["✅ [SYSTEM] Sentry Error Tracking: ENABLED", "INFO ready"]
        self.assertEqual(incidents.find_error(lines), (None, None))

    def test_traceback_final_exception(self):
        _, head = incidents.find_error(app.MOCK_TRACEBACK)
        self.assertEqual(head, "TypeError: Unknown option 'send_default_pii'")

    def test_redact(self):
        out = incidents.redact("token=abcdef123456 Bearer abc.def.ghi1234 https://u:pw@host/x")
        self.assertNotIn("abcdef123456", out)
        self.assertNotIn("abc.def.ghi1234", out)
        self.assertNotIn(":pw@", out)

    def test_normalize(self):
        self.assertEqual(incidents.normalize_error("Error 42 at 0xdeadbeef"), incidents.normalize_error("Error 7 at 0x1f"))

    def test_oneoff_and_manual_stop(self):
        c = {"name": "nice_mcnulty", "state": "exited", "ran_secs": 3, "labels": {}}
        self.assertTrue(app.is_oneoff(c))
        self.assertFalse(app.is_oneoff(dict(c, name="crashtest")))
        self.assertTrue(app.stopped_by_user({"name": "x", "state": "exited", "restart_policy": "unless-stopped"}, []))
        ev = [{"ts": 10, "action": "kill", "name": "x"}, {"ts": 11, "action": "die", "name": "x"}]
        self.assertTrue(app.stopped_by_user({"name": "x", "state": "exited"}, ev))
        self.assertFalse(app.stopped_by_user({"name": "x", "state": "exited"}, ev[1:]))  # crashed by itself
        self.assertFalse(app.stopped_by_user({"name": "x", "state": "exited", "restart_policy": "always",
                                              "state_error": "port is already allocated"}, []))  # failed to start

    def test_events_deduplicated(self):
        e = {"ts": 123.5, "action": "die", "name": "dup-test", "exit": "1"}
        app.remember_events([e, dict(e)])
        app.remember_events([dict(e)])
        self.assertEqual(sum(1 for x in app.RECENT_EVENTS if x["name"] == "dup-test"), 1)

    def test_corrupt_db_recovers(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "incidents.db"), "wb") as f:
            f.write(b"this is not a database" * 100)
        s = incidents.Store(d)
        self.assertEqual(s.count(), 0)
        self.assertTrue(any(n.startswith("incidents.db.corrupt-") for n in os.listdir(d)))
        s.close()

    def test_no_false_resolve_while_blind(self):
        class N:
            enabled = True
            sent = []

            def send(self, *a):
                self.sent.append(a)
                return True
        m = incidents.Monitor(incidents.Store(None), N(), lambda p: {"summary": "s", "evidence": {}, "logs": ""})
        p = {"key": "oom:y", "kind": "container", "type": "oom", "target": "y", "level": "🔴", "priority": "urgent",
             "title": "y ran out of memory", "detail": "killed", "immediate": True, "fingerprint": "f"}
        m.tick([p], 1000)
        for t in (1100, 1200, 1300):  # Docker unreachable: the problem "disappears", but we can't see
            m.tick([], t, hold_kinds={"container"})
        self.assertEqual(len(m.store.open_incidents()), 1)
        for t in (1400, 1500):  # visible again and really gone: resolves
            m.tick([], t)
        self.assertEqual(len(m.store.open_incidents()), 0)

    def test_monitor_survives_capture_failure(self):
        sent = []

        class N:
            enabled = True

            def send(self, *a):
                sent.append(a)
                return True

        def boom(p):
            raise RuntimeError("docker gone")
        m = incidents.Monitor(incidents.Store(None), N(), boom)
        p = {"key": "oom:x", "kind": "container", "type": "oom", "target": "x", "level": "🔴", "priority": "urgent",
             "title": "x ran out of memory", "detail": "killed", "immediate": True, "fingerprint": "f"}
        m.tick([p], 1000)
        self.assertEqual(len(m.store.open_incidents()), 1)
        self.assertEqual(len(sent), 1)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.status, r.read().decode(), r.headers

    def post(self, path, body, headers=None):
        h = {"Content-Type": "application/json", "X-Dashboard": "1", **(headers or {})}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}"), e.headers

    def test_page_and_static(self):
        for path in ("/", "/app.js", "/app.css", "/favicon.svg"):
            status, body, h = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn(app.VERSION, self.get("/")[1])
        self.assertNotIn("{{", self.get("/")[1])

    def test_every_panel_renders(self):
        app.seed_mock_incidents()
        for tab in list(app.PANEL_RENDERERS) + ["incidents", "incident/1", "logs/pvt-discord-bot"]:
            status, body, _ = self.get(f"/panel/{tab}")
            self.assertEqual(status, 200, tab)
            self.assertGreater(len(body), 100, tab)
            self.assertNotIn("�", body, tab)  # no broken characters

    def test_json_api(self):
        for path in app.ROUTES:
            status, body, _ = self.get(path)
            self.assertEqual(status, 200, path)
            json.loads(body)
        status, body, _ = self.get("/healthz")
        self.assertEqual(json.loads(body)["version"], app.VERSION)

    def test_hostile_ids(self):
        for path in ("/panel/incidents?open=99999999999999999999999", "/panel/incidents?open=-5&show=%00&range=zz",
                     "/panel/incident/999999999999999"):
            self.assertEqual(self.get(path)[0], 200, path)
        status, _, h = self.post("/api/login", {"pin": "4821"})
        cookie = {"Cookie": h["Set-Cookie"].split(";")[0]}
        for body in ({"incident": 10 ** 30, "kind": "mute", "hours": 1}, {"incident": "abc", "kind": "mute", "hours": 1}):
            self.assertEqual(self.post("/api/rules", body, cookie)[0], 400, body)
        self.assertEqual(self.post("/api/rules/delete", {"rule": 10 ** 30}, cookie)[0], 400)

    def test_unknown_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/../../etc/passwd")
        self.assertEqual(e.exception.code, 404)
        e.exception.close()

    def test_pin_flow(self):
        self.assertEqual(self.post("/api/rules", {"incident": 1, "kind": "mute", "hours": 1})[0], 401)
        self.assertEqual(self.post("/api/login", {"pin": "4821"}, {"X-Dashboard": "0"})[0], 400)
        self.assertEqual(self.post("/api/login", {"pin": "0000"})[0], 401)
        status, _, h = self.post("/api/login", {"pin": "4821"})
        self.assertEqual(status, 200)
        cookie = h["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertEqual(self.post("/api/rules", {"incident": 1, "kind": "bogus"}, {"Cookie": cookie})[0], 400)
        self.assertEqual(self.post("/api/rules", ["not", "a", "dict"], {"Cookie": cookie})[0], 400)
        self.assertEqual(self.post("/api/rules", {"incident": 1}, {"Cookie": "dash_admin=1.forged"})[0], 401)

    def test_no_pin_means_open_but_header_still_required(self):
        pin, app.ADMIN_PIN = app.ADMIN_PIN, ""
        try:
            app.seed_mock_incidents()
            self.assertIn("data-rule=", self.get("/panel/incidents")[1])  # buttons shown, no PIN form
            self.assertNotIn("data-pin", self.get("/panel/incidents")[1])
            self.assertEqual(self.post("/api/rules", {"incident": 1, "kind": "mute", "hours": 1}, {"X-Dashboard": "0"})[0], 400)
            status, data, _ = self.post("/api/rules", {"incident": 1, "kind": "mute", "hours": 1})
            self.assertEqual(status, 200)
            self.assertEqual(self.post("/api/rules/delete", {"rule": data["rule"]})[0], 200)
        finally:
            app.ADMIN_PIN = pin


if __name__ == "__main__":
    unittest.main()
