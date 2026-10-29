"""Quick UI check: open every dashboard page with demo data in headless Chrome, on a
desktop and a phone-sized window, and fail on script errors, "NaN" or "undefined" on
screen, a page wider than the window, a Report an issue window that won't open, or a
replay map that puts the demo round's route in the wrong place.

    py -3 -m pip install playwright && py -3 -m playwright install chromium
    py -3 tests/ui_smoke.py
"""
import os
import re
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402

import demo  # noqa: E402
from wdt.dashboard import make_handler  # noqa: E402

PAGES = ["#live", "#overview", "#matches", "#insights", "#career"]
SIZES = {"desktop": (1280, 900), "phone": (390, 844), "4k": (2560, 1440)}
BAD_TEXT = re.compile(r"\bNaN\b|\bundefined\b|\[object Object\]|\bInfinity\b")


def serve():
    ddir = tempfile.mkdtemp(prefix="wardogs-qa-")
    store, sid, balance = demo.build(ddir, time.time(), False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(demo.DemoApp(store, sid, balance, False, ddir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    latest = max(m["id"] for m in store.dump()["matches"])
    return server, f"http://127.0.0.1:{server.server_address[1]}/", latest, store


def check(page, base, hsh, size):
    errors = []
    page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text} {m.location.get('url', '')}"))
    page.goto(base + hsh)
    page.wait_for_timeout(1200)
    text = page.inner_text("main")
    if len(text) < 200:
        errors.append(f"almost nothing on the page ({len(text)} characters)")
    for line in text.splitlines():
        if BAD_TEXT.search(line):
            errors.append(f"bad value on screen: {line.strip()[:120]!r}")
    wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if wide > 1:
        errors.append(f"page is {wide}px wider than the {size} window")
    return errors


def check_replay(page, base, store, latest):
    """The replay on the latest demo match finds most of the round, near where it was."""
    page.goto(base + f"#match/{latest}")
    try:
        page.wait_for_selector("#rpBody[data-status=ready]", timeout=60000)
    except Exception:
        return [f"the replay map never finished (status {page.get_attribute('#rpBody', 'data-status')!r})"]
    page.wait_for_timeout(500)
    route = (store.match_full(latest)["details"].get("track_route") or {}).get("path") or []
    truth = {t: (x, y) for t, x, y in demo.TRUTH}
    near = [p for p in route if p[0] in truth and abs(p[1] - truth[p[0]][0]) < 0.3 and abs(p[2] - truth[p[0]][1]) < 0.3]
    errors = []
    if len(near) < 0.7 * len(truth):
        errors.append(f"replay placed {len(near)} of {len(truth)} minimap pictures within 30 m ({len(route)} placed)")
    if truth:
        page.eval_on_selector("#rpTime", "(s, v) => { s.value = v; s.dispatchEvent(new Event('input')); }",
                              sorted(truth)[len(truth) // 2])
    if not page.inner_text(".rp-now").strip():
        errors.append("the replay slider shows nothing for the moment it is on")
    return errors


def main():
    server, base, latest, store = serve()
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("QA_BROWSER_CHANNEL") or None)
        for size, (w, h) in SIZES.items():
            ctx = browser.new_context(viewport={"width": w, "height": h})
            ctx.add_init_script("try { localStorage.setItem('wdt-skip-update','*'); } catch {}")
            for hsh in PAGES + [f"#match/{latest}"]:
                page = ctx.new_page()
                for e in check(page, base, hsh, size):
                    failures.append(f"{size} {hsh}: {e}")
                page.close()
            page = ctx.new_page()
            for e in check_replay(page, base, store, latest):
                failures.append(f"{size} replay: {e}")
            page.close()
            page = ctx.new_page()
            page.goto(base + "#matches")
            page.click("#reportOpen")
            if not page.is_visible("#reportText"):
                failures.append(f"{size}: the Report an issue window didn't open")
            page.close()
            ctx.close()
        browser.close()
    server.shutdown()
    for f in failures:
        print("FAIL", f)
    print(f"{len(failures)} problem(s) across {len(SIZES)} window sizes and {len(PAGES) + 1} pages")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
