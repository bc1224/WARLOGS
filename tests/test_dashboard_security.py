import http.client
import tempfile
import threading
import unittest
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

    def get(self, path, host=None, method="GET", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        c.request(method, path, body=b"{}" if method == "POST" else None, headers=h)
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

if __name__ == "__main__":
    unittest.main()
