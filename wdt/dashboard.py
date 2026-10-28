"""Live dashboard served by the tracker at http://127.0.0.1:<dashboard_port>.

The page asks /api/version every couple of seconds (a few bytes) and only
reloads the full data when the tracker has written something new.
"""
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .sources import pull_api, pull_status
from . import track
from .store import Store
from .update import Updater, installed_version

HERE = os.path.dirname(os.path.abspath(__file__))
# the Report an issue button posts here (relay/ in this repo), which files a GitHub issue
REPORT_URL = "https://wardogs-report-relay.bcf1224.workers.dev/report"
REPORT_MAX = 6 * 1024 * 1024  # a 4 MB screenshot, base64 encoded, plus the text
ROUTE_MAX = 400 * 1024        # a worked-out replay route: a few thousand points
BODY_MAX = 256 * 1024         # settings, match edits and the rest
AUTO_EVERY = 15 * 60          # how often Install updates automatically looks
log = logging.getLogger(__name__)
LOCAL = re.compile(r"(127\.0\.0\.1|localhost)(:\d+)?")
# only our own inline scripts run; names read off the screen or the API can't become code
CSP = ("default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: https:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


class App:
    def __init__(self, cfg, ddir, on_templates_changed=None, on_updated=None, updater=None):
        self.cfg, self.ddir = cfg, ddir
        self.store = Store(os.path.join(ddir, "wardogs.db"))  # own connection
        self.on_templates_changed = on_templates_changed or (lambda: None)
        self.on_updated = on_updated or (lambda: None)
        self.updater = updater or Updater(os.path.dirname(HERE), ddir)
        self.lock = threading.Lock()

    def report(self, body):
        """Send a Report an issue form to the relay: (status, reply)."""
        out = {"text": str(body.get("text") or ""), "page": str(body.get("page") or "")[:40],
               "version": installed_version(os.path.dirname(HERE), self.ddir)}
        if body.get("image"):
            out["image"] = str(body["image"])
        req = urllib.request.Request(self.cfg.get("report_url") or REPORT_URL, data=json.dumps(out).encode(),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "wardogs-local-tracker/1.0 (report)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return 200, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, {"error": json.loads(e.read()).get("error") or "The report didn't go through."}
            except ValueError:
                return 502, {"error": "The report didn't go through. Try again later."}
        except (OSError, ValueError):
            return 502, {"error": "Couldn't reach the report server. Check your connection."}

    def auto_update(self):
        """With Install updates automatically on, install a new version while no game is
        running. Returns True when it installed one (the tracker then restarts)."""
        with self.lock:
            on = self.store.auto_update() and self.store.updates_on()
        s = self.status()
        if not on or not self.cfg.get("check_updates", True) or not s["alive"] or s.get("session") is not None:
            return False
        st = self.updater.state()
        if not st.get("available"):
            return False
        res = self.updater.apply(st["sha"])
        if not res["ok"]:
            log.warning("automatic update failed: %s", res.get("error"))
            return False
        log.info("installed an update automatically")
        self.on_updated()
        return True

    def _auto_loop(self):
        while True:
            time.sleep(AUTO_EVERY)
            try:
                self.auto_update()
            except Exception:
                log.exception("automatic update check failed")

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

        def _send(self, code, body, ctype="application/json", csp=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if csp:
                self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def _local_host(self):
            # refuse other hostnames pointed at 127.0.0.1 (DNS rebinding)
            if LOCAL.fullmatch(self.headers.get("Host", "")):
                return True
            self._send(403, {"error": "forbidden"})
            return False

        def _length(self):
            """The body's declared size; ValueError unless it's plain digits."""
            n = self.headers.get("Content-Length") or "0"
            if not re.fullmatch(r"[0-9]{1,12}", n):
                raise ValueError("bad length")
            return int(n)

        def _json_body(self):
            """The body as a JSON object; ValueError for anything else."""
            try:
                body = json.loads(self.rfile.read(self._length()) or b"{}")
            except RecursionError:
                raise ValueError("too deep") from None
            if not isinstance(body, dict):
                raise ValueError("not an object")
            return body

        def do_GET(self):
            if not self._local_host():
                return
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                nonce = secrets.token_urlsafe(16)
                with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as f:
                    page = f.read().replace("<script>", f'<script nonce="{nonce}">')
                return self._send(200, page, "text/html; charset=utf-8", CSP.format(nonce=nonce))
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
                data["switches"] = app.store.switches()
                return self._send(200, data)
            if path == "/api/update":
                if not app.cfg.get("check_updates", True) or not app.store.updates_on():
                    return self._send(200, {"available": False, "off": True})
                return self._send(200, app.updater.state())
            m = re.fullmatch(r"/api/match/(\d+)", path)
            if m:
                with app.lock:
                    full = app.store.match_full(int(m.group(1)))
                return self._send(200 if full else 404, full or {"error": "no such match"})
            m = re.fullmatch(r"/api/match/(\d+)/text", path)
            if m:
                with app.lock:
                    return self._send(200, {"lines": app.store.match_text(int(m.group(1)))})
            m = re.fullmatch(r"/api/track/([0-9]{8}-[0-9]{6})", path)
            if m:  # a round's minimap pictures and the big map pictures, for the replay map
                return self._send(200, track.listing(app.ddir, m.group(1)))
            m = re.fullmatch(r"/track/(maps|[0-9]{8}-[0-9]{6})/([0-9A-Za-z_.-]+)", path)
            if m:
                p = track.file_path(app.ddir, m.group(1), m.group(2))
                try:
                    with open(p, "rb") as f:
                        return self._send(200, f.read(), "image/png" if p.endswith(".png") else "image/jpeg")
                except (OSError, TypeError):  # not one of ours, or cleared away just now
                    pass
            m = re.fullmatch(r"/shot/(\d+)", path)
            if m:
                with app.lock:
                    row = app.store.db.execute("SELECT screenshot FROM matches WHERE id=?",
                                               (int(m.group(1)),)).fetchone()
                if row and row[0] and os.path.isfile(row[0]):
                    ctype = "image/png" if row[0].lower().endswith(".png") else "image/jpeg"
                    with open(row[0], "rb") as f:
                        return self._send(200, f.read(), ctype)
            m = re.fullmatch(r"/shot/(\d+)/(\d+)", path)  # the later results tabs, in order
            if m:
                with app.lock:
                    full = app.store.match_full(int(m.group(1)))
                shots = (full or {}).get("details", {}).get("end_shots", [])
                n = int(m.group(2))
                if n < len(shots) and os.path.isfile(shots[n]):
                    with open(shots[n], "rb") as f:
                        return self._send(200, f.read(), "image/png")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_host():
                return
            # only accept requests from our own page: other programs on this PC serve pages on other ports
            origin = self.headers.get("Origin", "")
            port = self.server.server_address[1]
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self._send(403, {"error": "forbidden"})
            try:
                n = self._length()
            except ValueError:
                return self._send(400, {"error": "bad request"})
            limit = REPORT_MAX if self.path == "/api/report" else ROUTE_MAX if self.path.endswith("/track") else BODY_MAX
            if n > limit:
                return self._send(413, {"error": "That screenshot is too big (4 MB max)." if limit == REPORT_MAX
                                        else "too big"})
            try:
                return self._post(origin)
            except (ValueError, UnicodeDecodeError):  # a body that isn't a JSON object
                return self._send(400, {"error": "bad request"})

        def _post(self, origin):
            m = re.fullmatch(r"/api/match/([0-9]+)/track", self.path)
            if m:  # the dashboard worked out a round's replay route: keep it with the match
                route = track.clean_route(self._json_body())
                if route is None:
                    return self._send(400, {"error": "bad route"})
                with app.lock:
                    app.store.fill_match(int(m.group(1)), {}, {"track_route": route})
                return self._send(200, {"ok": True})
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
            if self.path == "/api/switch":
                if not origin:  # these decide what leaves this PC: only from the dashboard page itself
                    return self._send(403, {"error": "forbidden"})
                body = self._json_body()
                with app.lock:
                    if body.get("name") not in app.store.switches():
                        return self._send(400, {"error": "no such switch"})
                    app.store.set_switch(body["name"], bool(body.get("on")))
                    return self._send(200, app.store.switches())
            if self.path == "/api/refresh" and not app.store.stats_on():
                return self._send(409, {"ok": False, "error": "Stats from wardogstracker.gg are off. Turn them on under More at the top of the page."})
            if self.path == "/api/update" and not app.store.updates_on():
                return self._send(409, {"ok": False, "error": "Update checks are off. Turn them on under More at the top of the page."})
            if self.path == "/api/update":
                if not origin:  # installs code: only from the dashboard page itself
                    return self._send(403, {"error": "forbidden"})
                res = app.updater.apply(str(self._json_body().get("sha", "")))
                if res["ok"]:
                    threading.Timer(1.0, app.on_updated).start()  # after this reply is sent
                return self._send(200 if res["ok"] else 409, res)
            if self.path == "/api/report":
                if not origin:  # sends data off this PC: only from the dashboard page itself
                    return self._send(403, {"error": "forbidden"})
                return self._send(*app.report(self._json_body()))
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


def start_server(cfg, ddir, on_templates_changed=None, on_updated=None):
    """Serve in a background thread. Returns the server, or None if the port is taken."""
    app = App(cfg, ddir, on_templates_changed, on_updated)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", cfg["dashboard_port"]), make_handler(app))
    except OSError:
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=app._auto_loop, daemon=True, name="auto-update").start()
    return server
