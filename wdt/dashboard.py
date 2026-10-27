"""Live dashboard served by the tracker at http://127.0.0.1:<dashboard_port>.

The page asks /api/version every couple of seconds (a few bytes) and only
reloads the full data when the tracker has written something new.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .sources import pull_api, pull_status
from .store import Store

HERE = os.path.dirname(os.path.abspath(__file__))


class App:
    def __init__(self, cfg, ddir, on_templates_changed=None):
        self.cfg, self.ddir = cfg, ddir
        self.store = Store(os.path.join(ddir, "wardogs.db"))  # own connection
        self.on_templates_changed = on_templates_changed or (lambda: None)
        self.lock = threading.Lock()

    def status(self):
        try:
            with open(os.path.join(self.ddir, "status.json"), encoding="utf-8") as f:
                s = json.load(f)
            s["alive"] = time.time() - s["updated"] < 45
            return s
        except (OSError, ValueError):
            return {"alive": False}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/version":
                with app.lock:
                    v = app.store.version()
                return self._send(200, {"v": v, "status": app.status()})
            if path == "/api/data":
                with app.lock:
                    data = app.store.dump()
                    data["version"] = app.store.version()
                data["status"] = app.status()
                data["steam_id"] = app.cfg["steam_id"]
                return self._send(200, data)
            m = re.fullmatch(r"/api/match/(\d+)", path)
            if m:
                with app.lock:
                    full = app.store.match_full(int(m.group(1)))
                return self._send(200 if full else 404, full or {"error": "no such match"})
            m = re.fullmatch(r"/api/match/(\d+)/text", path)
            if m:
                with app.lock:
                    return self._send(200, {"lines": app.store.match_text(int(m.group(1)))})
            m = re.fullmatch(r"/shot/(\d+)", path)
            if m:
                with app.lock:
                    row = app.store.db.execute("SELECT screenshot FROM matches WHERE id=?",
                                               (int(m.group(1)),)).fetchone()
                if row and row[0] and os.path.isfile(row[0]):
                    ctype = "image/png" if row[0].lower().endswith(".png") else "image/jpeg"
                    with open(row[0], "rb") as f:
                        return self._send(200, f.read(), ctype)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            # only accept requests from our own page
            origin = self.headers.get("Origin", "")
            if origin and not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+", origin):
                return self._send(403, {"error": "forbidden"})
            m = re.fullmatch(r"/api/match/(\d+)(/delete|/not-end-screen)?", self.path)
            if m:
                mid, action = int(m.group(1)), m.group(2)
                with app.lock:
                    if action:
                        app.store.delete_match(mid, forget_screen=action == "/not-end-screen")
                    else:
                        app.store.update_match(mid, self._json_body())
                if action == "/not-end-screen":
                    app.on_templates_changed()
                return self._send(200, {"ok": True})
            if self.path == "/api/settings":
                with app.lock:
                    return self._send(200, app.store.save_settings(self._json_body()))
            if self.path == "/api/refresh":
                try:
                    with app.lock:
                        new, unlocked = pull_api(app.store, app.cfg["steam_id"], time.time())
                        try:
                            pull_status(app.store)
                        except Exception:
                            pass
                    return self._send(200, {"ok": True, "new": new, "unlocked": unlocked})
                except Exception as e:
                    return self._send(502, {"ok": False, "error": str(e)})
            self._send(404, {"error": "not found"})

    return Handler


def start_server(cfg, ddir, on_templates_changed=None):
    """Serve in a background thread. Returns the server, or None if the port is taken."""
    app = App(cfg, ddir, on_templates_changed)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", cfg["dashboard_port"]), make_handler(app))
    except OSError:
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
