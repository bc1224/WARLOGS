import http.client
import tempfile
import threading
import io
import json
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer

from wdt.dashboard import App, make_handler


class DashboardSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = app = App({"steam_id": "76561190000000000", "dashboard_port": 0}, cls.tmp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.store.db.close()
        cls.tmp.cleanup()

    def get(self, path, host=None, method="GET", headers=None, body=b"{}"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        c.request(method, path, body=body if method == "POST" else None, headers=h)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_page_has_a_nonce_csp(self):
        r, body = self.get("/")
        self.assertEqual(r.status, 200)
        csp = r.getheader("Content-Security-Policy")
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'<script nonce="{nonce}">'.encode(), body)
        self.assertNotIn(b"<script>", body)
        self.assertNotIn(b" onclick=", body)

    def test_other_hostnames_are_refused(self):
        # a DNS-rebinding page arrives with its own hostname
        self.assertEqual(self.get("/api/data", host=f"evil.example:{self.port}")[0].status, 403)
        self.assertEqual(self.get("/api/refresh", host="evil.example", method="POST")[0].status, 403)
        self.assertEqual(self.get("/api/version", host=f"localhost:{self.port}")[0].status, 200)

    def test_replay_pictures_are_served_only_from_the_track_folder(self):
        import os
        rnd = os.path.join(self.tmp.name, "track", "20261027-210000")
        os.makedirs(rnd, exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "track", "maps"), exist_ok=True)
        with open(os.path.join(rnd, "1761600000.jpg"), "wb") as f:
            f.write(b"JPEGDATA")
        r, body = self.get("/track/20261027-210000/1761600000.jpg")
        self.assertEqual((r.status, r.getheader("Content-Type"), body), (200, "image/jpeg", b"JPEGDATA"))
        r, body = self.get("/api/track/20261027-210000")
        self.assertEqual(json.loads(body)["snaps"], [[1761600000, "1761600000.jpg"]])
        for bad in ("/track/20261027-210000/..%2F..%2Fwardogs.db", "/track/../wardogs.db", "/track/maps/..",
                    "/track/20261027-210000/wardogs.db", "/api/track/..%2F..", "/track/maps/x.json"):
            self.assertEqual(self.get(bad)[0].status, 404, bad)
        self.assertEqual(self.get("/track/20261027-210000/1761600000.jpg", host="evil.example")[0].status, 403)

    def test_replay_routes_are_checked_before_they_are_kept(self):
        mid = self.app.store.add_match(self.app.store.start_session(1), 1, 2, "auto", details={"track": {"dir": "x"}})
        ok = json.dumps({"path": [[1761600000, 70.5, 104.25, 0.7]], "refs": ["20261027-210100"]}).encode()
        evil = {"Origin": "https://evil.example"}
        self.assertEqual(self.get(f"/api/match/{mid}/track", method="POST", headers=evil, body=ok)[0].status, 403)
        for bad in ({"path": "x"}, {"path": [[1, 2, 3]]}, {"path": [[1, 2, 3, "<b>"]]}, {"path": [[1, 2, 3, True]]},
                    {"path": [], "refs": ["../../t"]}, {"path": [[0, 0, 0, 0]] * 5001}, [1]):
            r, _ = self.get(f"/api/match/{mid}/track", method="POST", body=json.dumps(bad).encode())
            self.assertIn(r.status, (400, 413), bad)
        r, _ = self.get(f"/api/match/{mid}/track", method="POST", headers={"Content-Length": str(500 * 1024)})
        self.assertEqual(r.status, 413)
        self.assertEqual(self.get(f"/api/match/{mid}/track", method="POST", body=ok)[0].status, 200)
        det = self.app.store.match_full(mid)["details"]
        self.assertEqual(det["track_route"], {"path": [[1761600000, 70.5, 104.25, 0.7]], "refs": ["20261027-210100"]})

    def test_odd_request_bodies_are_turned_away(self):
        mid = self.app.store.add_match(self.app.store.start_session(1), 1, 2, "auto")
        for path in (f"/api/match/{mid}/track", "/api/settings", f"/api/match/{mid}"):
            for length in ("-1", "12abc", "+5", "1 2"):  # -1 would read until the client hangs up
                r, _ = self.get(path, method="POST", headers={"Content-Length": length})
                self.assertEqual(r.status, 400, (path, length))
            for body in (b"{not json", b"[1, 2]", b"\xff\xfe", b"[" * 100000):
                r, _ = self.get(path, method="POST", body=body)
                self.assertEqual(r.status, 400, (path, body[:10]))
        r, _ = self.get("/api/settings", method="POST", headers={"Content-Length": str(2 * 1024 * 1024)})
        self.assertEqual(r.status, 413)
        self.assertEqual(self.get("/api/version")[0].status, 200)  # still serving

    def test_posts_from_other_sites_are_refused(self):
        r, _ = self.get("/api/settings", method="POST", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)

    def test_updates_install_only_from_the_dashboard_page(self):
        r, _ = self.get("/api/update", method="POST", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        r, _ = self.get("/api/update", method="POST")  # no Origin: not a page in a browser
        self.assertEqual(r.status, 403)

    def test_reports_go_to_the_relay_only_from_the_dashboard_page(self):
        self.assertEqual(self.get("/api/report", method="POST")[0].status, 403)  # no Origin
        sent = []

        def fake(req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(b'{"ok": true, "number": 7, "url": "https://github.com/x/7"}')
        with mock.patch("wdt.dashboard.urllib.request.urlopen", fake):
            r, body = self.get("/api/report", method="POST", headers={"Origin": f"http://127.0.0.1:{self.port}"},
                               body=json.dumps({"text": "Round didn't log", "page": "#live", "image": "data:x"}).encode())
        self.assertEqual((r.status, json.loads(body)["number"]), (200, 7))
        self.assertEqual(set(sent[0]), {"text", "page", "version", "image"})

    def test_huge_reports_are_refused_before_reading(self):
        r, _ = self.get("/api/report", method="POST", headers={"Origin": f"http://127.0.0.1:{self.port}",
                                                                 "Content-Length": str(7 * 1024 * 1024)}, body=b"")
        self.assertEqual(r.status, 413)

    def test_offline_mode_stops_every_outside_call(self):
        page = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.get("/api/online", method="POST", body=b'{"on": false}')[0].status, 403)  # no Origin
        r, body = self.get("/api/online", method="POST", headers=page, body=b'{"on": false}')
        self.assertEqual((r.status, json.loads(body)), (200, {"online": False}))
        try:
            self.assertFalse(json.loads(self.get("/api/data")[1])["online"])
            with mock.patch("wdt.dashboard.urllib.request.urlopen", side_effect=AssertionError("went online")),                     mock.patch("wdt.dashboard.pull_api", side_effect=AssertionError("went online")):
                self.assertEqual(json.loads(self.get("/api/update")[1])["off"], True)
                self.assertEqual(self.get("/api/report", method="POST", headers=page,
                                          body=b'{"text": "Round did not log"}')[0].status, 409)
                self.assertEqual(self.get("/api/refresh", method="POST", headers=page)[0].status, 409)
        finally:
            self.get("/api/online", method="POST", headers=page, body=b'{"on": true}')
        self.assertTrue(json.loads(self.get("/api/data")[1])["online"])

    def test_auto_update_switch_only_from_the_page(self):
        page = {"Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.get("/api/auto_update", method="POST", body=b'{"on": true}')[0].status, 403)
        self.assertFalse(json.loads(self.get("/api/data")[1])["auto_update"])  # starts off
        r, body = self.get("/api/auto_update", method="POST", headers=page, body=b'{"on": true}')
        self.assertEqual((r.status, json.loads(body)), (200, {"auto_update": True}))
        self.assertTrue(json.loads(self.get("/api/data")[1])["auto_update"])
        self.get("/api/auto_update", method="POST", headers=page, body=b'{"on": false}')


class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.updater = mock.Mock()
        self.updater.state.return_value = {"available": True, "sha": "abc"}
        self.updater.apply.return_value = {"ok": True, "files": 3}
        self.restarted = mock.Mock()
        self.app = App({"steam_id": "", "dashboard_port": 0}, self.tmp.name,
                       on_updated=self.restarted, updater=self.updater)
        self.app.store.set_auto_update(True)

    def tearDown(self):
        self.app.store.db.close()
        self.tmp.cleanup()

    def run_with(self, status):
        with mock.patch.object(self.app, "status", return_value=status):
            return self.app.auto_update()

    def test_installs_and_restarts_between_games(self):
        self.assertTrue(self.run_with({"alive": True, "session": None}))
        self.updater.apply.assert_called_once_with("abc")
        self.restarted.assert_called_once()

    def test_never_mid_game_offline_or_switched_off(self):
        self.assertFalse(self.run_with({"alive": True, "session": 4}))
        self.app.store.set_online(False)
        self.assertFalse(self.run_with({"alive": True, "session": None}))
        self.app.store.set_online(True)
        self.app.store.set_auto_update(False)
        self.assertFalse(self.run_with({"alive": True, "session": None}))
        self.updater.apply.assert_not_called()

    def test_nothing_new_installs_nothing(self):
        self.updater.state.return_value = {"available": False}
        self.assertFalse(self.run_with({"alive": True, "session": None}))
        self.updater.apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
