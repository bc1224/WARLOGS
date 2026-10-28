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


if __name__ == "__main__":
    unittest.main()
