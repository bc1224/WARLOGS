"""Try the dashboard with made-up data, no game or setup needed.

    py -3 demo.py            # then open http://127.0.0.1:8791
    py -3 demo.py --idle     # same data, but between rounds instead of mid-round

Everything here is random and lives in a temp folder; your real data/ is untouched.
"""
import json
import os
import random
import sys
import tempfile
import threading
import time
import webbrowser
import struct
import zlib
from http.server import ThreadingHTTPServer

from wdt.dashboard import make_handler
from wdt.store import Store

PORT = 8791
NAME = "DemoDog"
STEAM_ID = "76561190000000000"
ROLES = ["Assault", "Medic", "Recon", "Support", "Driver", "Pilot"]
TEAMS = ["Viper", "Manticore", "Valkyra"]
OTHERS = ["Rook", "Bramble", "Cinder", "Kestrel", "Marlow", "Nettle", "Quill", "Tallow",
          "SgtPepper", "Moth", "Hollis", "Brickhouse", "Juno", "Pavlov", "Sixgun", "Wrenchy"]
REWARDS = [  # label, cash, xp, weight
    ("Hot Zone Presence", 390, 0, 26), ("Control Zone Presence", 140, 0, 18),
    ("Target Spotted", 25, 25, 8), ("Spotted Target Destroyed", 160, 105, 5),
    ("Spotted Vehicle Destroyed", 125, 90, 3), ("Healed Teammate", 15, 30, 6),
    ("Revived Teammate", 150, 120, 3), ("Kill", 250, 100, 4), ("Kill Assist", 0, 200, 2),
    ("Objective Captured", 1200, 400, 1)]
CATALOG = [
    ("Galil", "weapons", 10, "assault", 35000, 2000, 58), ("M4", "weapons", 28, "assault", 150000, 2800, 12),
    ("TCX-SPR", "weapons", 20, "assault", 100000, 3200, 26), ("MP5", "weapons", 4, "medic", 25000, 1800, 37),
    ("PP-19 Vityaz", "weapons", 15, "medic", 50000, 1200, 5), ("AMP-9", "weapons", 20, "medic", 75000, 1375, 9),
    ("Mosin Nagant", "weapons", 10, "recon", 50000, 4500, 39), ("SV98", "weapons", 18, "recon", 100000, 5200, 22),
    ("SVD", "weapons", 12, "recon", 50000, 4800, 16), ("M14", "weapons", 25, "recon", 100000, 7400, 3),
    ("PKM", "weapons", 15, "support", 100000, 4100, 6), ("RPG-7", "weapons", 6, "support", 30000, 1800, 31),
    ("MAAWS", "weapons", 18, "support", 100000, 2400, 21), ("M500", "weapons", 10, "support", 50000, 1200, 13),
    ("Defibrillator", "equipment", 22, "medic", 110000, 1600, 14),
    ("Adrenaline Pen", "equipment", 16, "medic", 25000, 250, 32),
    ("Infrared Range Finder", "equipment", 32, "recon", 50000, 1200, 4),
    ("Large Hammer", "equipment", 8, "support", 75000, 2400, 57)]
TRUTH = []  # where the demo round's minimap pictures were really taken: [t, x, y]


def _png(path, w, h, rgb):
    """rgb: bytes, 3 per pixel."""
    raw = b"".join(b"\x00" + rgb[r * w * 3:(r + 1) * w * 3] for r in range(h))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b""))


def build_track(ddir, rng, t0, t1):
    """A made-up map (blocks and roads on a 10 m grid), one big map picture of it and
    a round of minimap pictures along a known route, for the replay map."""
    X0, Y0, N = 60.0, 110.0, 150  # 15 x 15 (100 m units), cells of 10 m
    cells = bytearray(rng.randrange(40, 90) for _ in range(N * N))
    for _ in range(140):  # buildings
        x, y, w, h, v = rng.randrange(N), rng.randrange(N), rng.randrange(2, 7), rng.randrange(2, 7), rng.randrange(120, 200)
        for yy in range(y, min(N, y + h)):
            cells[yy * N + x:yy * N + min(N, x + w)] = bytes([v]) * (min(N, x + w) - x)
    for _ in range(9):  # roads
        if rng.random() < 0.5:
            x = rng.randrange(N)
            for yy in range(N):
                cells[yy * N + x] = 25
        else:
            y = rng.randrange(N)
            cells[y * N:(y + 1) * N] = bytes([25]) * N
    at = lambda X, Y: cells[min(N - 1, max(0, int((Y0 - Y) * 10))) * N + min(N - 1, max(0, int((X - X0) * 10)))]
    root = os.path.join(ddir, "track")
    rnd = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
    os.makedirs(os.path.join(root, "maps"), exist_ok=True)
    os.makedirs(os.path.join(root, rnd), exist_ok=True)
    ppu = 40  # the big map picture
    rgb = bytearray()
    for py in range(N * ppu // 10):
        for px in range(N * ppu // 10):
            v = at(X0 + (px + 0.5) / ppu, Y0 - (py + 0.5) / ppu)
            rgb += bytes((v, v, v))
    name = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0 + 60))
    _png(os.path.join(root, "maps", name + ".png"), N * ppu // 10, N * ppu // 10, bytes(rgb))
    with open(os.path.join(root, "maps", name + ".json"), "w") as f:
        json.dump({"x0": X0, "x1": X0 + 15, "y0": Y0, "y1": Y0 - 15, "t": round(t0 + 60), "file": name + ".png",
                   "round": rnd, "w": N * ppu // 10, "h": N * ppu // 10}, f)
    TRUTH.clear()
    n = 0
    for i, t in enumerate(range(int(t0) + 30, int(t1) - 30, 20)):
        a = i / 9
        X, Y = 67.5 + 2.4 * __import__("math").cos(a), 102.5 + 2.0 * __import__("math").sin(a * 1.3)
        TRUTH.append([t, round(X, 2), round(Y, 2)])
        rgb = bytearray()
        for py in range(153):
            for px in range(171):
                if abs(px - 85) + abs(py - 76) < 6:
                    rgb += b"\xff\xff\xff"  # your arrow
                elif (px - 30 - i % 40) ** 2 + (py - 40) ** 2 < 25 or (px - 120) ** 2 + (py - 100 - i % 20) ** 2 < 25:
                    rgb += b"\x30\xd0\x50"  # teammates
                elif px > 100 and py > 135:
                    rgb += b"\x60\x60\x60"  # the NAV text
                else:
                    v = at(X + (px - 85.5) / 93, Y - (py - 76.5) / 93)
                    rgb += bytes((v // 2 + 20, v // 2 + 40, v // 2 + 20))  # tinted green, like a held zone
        _png(os.path.join(root, rnd, f"{t}.png"), 171, 153, bytes(rgb))
        n += 1
    return {"dir": rnd, "n": n, "maps": [name]}


OWNED = ["Galil", "MP5", "Mosin Nagant", "SKS", "RPG-7", "Large Hammer", "Adrenaline Pen", "Field Resuscitator"]


def pick_rewards(rng, n, t0, t1):
    feed, totals = [], {}
    labels = [r for r in REWARDS for _ in range(r[3])]
    for _ in range(n):
        label, cash, xp, _w = rng.choice(labels)
        cash = int(cash * rng.uniform(0.7, 1.3)) // 5 * 5
        t = rng.uniform(t0, t1)
        feed.append({"label": label, "cash": cash or None, "xp": xp or None, "t": round(t), "here": None, "gun": None})
        r = totals.setdefault(label, {"n": 0, "cash": 0, "xp": 0})
        r["n"] += 1
        r["cash"] += cash
        r["xp"] += xp
    feed.sort(key=lambda e: e["t"])
    return feed, totals


def build(ddir, now, idle):
    rng = random.Random(7)
    store = Store(os.path.join(ddir, "wardogs.db"))
    levels = {"Assault": [9, 22100], "Medic": [15, 61000], "Recon": [12, 36000], "Support": [10, 26000],
              "Driver": [8, 21000], "Pilot": [7, 15000]}
    career, balance, unlocks = 120000, 30000, len(OWNED)
    # three weeks of evenings, plus tonight's session that's still going
    days = sorted(rng.sample(range(1, 22), 11), reverse=True)
    midnight = now - (now - time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1)))
    starts = [midnight - d * 86400 + rng.uniform(18.5, 21) * 3600 for d in days] + [now - 50 * 60 - 3 * 27 * 60]
    for si, s0 in enumerate(starts):
        last = si == len(starts) - 1
        sid = store.start_session(s0)
        t = s0 + rng.uniform(60, 300)
        n = 3 if last else rng.randint(2, 6)
        skill = 1 + si * 0.035  # gets a little better over time
        for _ in range(n):
            length = rng.uniform(20, 34) * 60
            role = rng.choices(ROLES, [3, 5, 4, 3, 1, 1])[0]
            win = rng.random() < 0.52
            feed, totals = pick_rewards(rng, rng.randint(40, 90), t, t + length)
            spent = int(rng.uniform(6000, 26000)) // 5 * 5
            earned = int(sum(r["cash"] for r in totals.values()) * skill * rng.uniform(0.8, 1.6)) // 5 * 5
            bonus = 6600 if win else 2200
            earned += bonus
            gained = rng.randint(1800, 5200) * (1.3 if win else 1) * skill
            xp = int(gained)
            other = rng.choice([r for r in ROLES if r != role])
            split = {role: int(xp * 0.75), other: xp - int(xp * 0.75)}
            role_xp = {}
            for r, g in split.items():
                before = levels[r][1]
                levels[r][1] += g
                role_xp[r] = {"before": before, "after": levels[r][1], "gain": g}
            kills, deaths = rng.randint(0, 9), rng.randint(0, 6)
            players = rng.randint(70, 100)
            rank = max(1, int(players * rng.uniform(0.08, 0.6) / skill))
            me = {"rank": rank, "level": 60, "name": NAME, "cash": earned, "kills": kills,
                  "assists": rng.randint(2, 14), "deaths": deaths, "revives": rng.randint(0, 6)}
            a, b = rng.sample(OTHERS, 2)
            board = {"view": "Global", "players": players, "me": me,
                     "above": {"rank": rank - 1, "name": a, "cash": earned + rng.randint(100, 3000)} if rank > 1 else None,
                     "below": {"rank": rank + 1, "name": b, "cash": max(0, earned - rng.randint(100, 3000))}}
            hud_reads = int(length / 11)
            hot = int(hud_reads * rng.uniform(0.2, 0.65))
            details = {
                "result": "victory" if win else "defeat", "map": "Bakurani",
                "used": {"roles": sorted({role, other}), "items": rng.sample(OWNED, 3)},
                "live_cash": {"net": earned - spent, "start_balance": balance, "end_balance": balance + earned - spent},
                "hud": {"reads": hud_reads, "hot": hot, "fob": int(hud_reads * rng.uniform(0.02, 0.15)),
                        "team": rng.choice(TEAMS), "guns": {rng.choice(OWNED[:4]): hud_reads // 3}},
                "combat": {"kills": kills, "deaths": deaths, "downs": rng.randint(0, 4)},
                "teams": {"mode": "2 teams" if hot % 2 else "3 teams", "players": {},
                          "scores": {"blue": None if hot % 2 else 30 + kills, "red": 100 if win else 60 + deaths,
                                     "green": 70 + kills if win else 100}},
                "rewards": totals, "feed": feed,
                "sources": [{"label": k, "xp": v["xp"] or None, "cash": v["cash"] or None}
                            for k, v in totals.items()] + [{"label": "Round bonus", "xp": None, "cash": bonus}],
                "cash_summary": {"spent": spent, "gained": earned, "bonus": bonus}, "summary_used": True,
                "role_xp": role_xp, "board_end": board,
                "timeline": [], "cash_events": []}
            mid = store.add_match(sid, t, t + length, "auto-read",
                                  parsed={"xp": xp, "cash_earned": earned, "cash_spent": spent}, details=details)
            store.update_match(mid, {"reviewed": int(rng.random() < 0.8)})
            balance += earned - spent
            career += xp
            t += length + rng.uniform(60, 240)
        if not last:
            store.end_session(sid, t)
        if si % 3 == 2 or last:
            synced = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
            store.add_snapshot({"wardogLevel": 50 + si, "careerXp": career, "cash": balance, "gold": 0,
                                "unlocks": unlocks, "syncedAt": synced, "name": NAME,
                                "rank": 900 - si * 12, "players": 1192, "unlockValue": 380000 + si * 9000,
                                "roles": {r.lower(): {"level": v[0] + si // 4, "xp": v[1]} for r, v in levels.items()}}, at=t)
    store.sync_unlocks({"items": [{"game_id": n, "name": n, "grp": "weapons", "url": None, "image": None, "level": 1}
                                  for n in OWNED]}, "base", at=now - 22 * 86400)
    store.sync_unlocks({"items": [{"game_id": "SV98", "name": "SV98", "grp": "weapons", "url": None, "image": None,
                                   "level": 1}]}, "later", at=now - 4 * 86400)
    store.set_meta("catalog", {"fetched": now, "items": [
        {"name": n, "category": c, "url": None, "level": lv, "role": r, "cost": co, "price": p, "owned_pct": o}
        for n, c, lv, r, co, p, o in CATALOG]})
    board = [{"rank": 880 + i, "steamId": STEAM_ID if i == 4 else f"7656119000000{i:04d}",
              "name": NAME if i == 4 else OTHERS[i], "wardogLevel": 61, "careerXp": career + (4 - i) * 3100,
              "cash": 90000 + i * 7000, "gold": 0, "unlocks": 20 + i, "syncedAt": None} for i in range(9)]
    store.set_meta("leaderboard", {"total": 1192, "players": board})
    hourly = [int(95000 + 40000 * abs(12 - h) / 12) for h in range(24)]
    store.set_meta("server_status", {"overall": "ok", "servers": {
        "health": "ok", "summary": "Game servers are up", "players": 121400, "officialServers": 902,
        "usualPlayers": 118000, "detail": "121,400 players on 902 official servers."},
        "login": {"health": "ok", "summary": "Login servers are up", "latencyMs": 92}, "playersHourly": hourly})
    # the latest round has a replay map, with a few fights and purchases on it
    last = store.dump()["matches"][-1]
    t0, t1 = last["started_at"], last["ended_at"]
    trk = build_track(ddir, rng, t0, t1)
    ts = [p[0] for p in TRUTH]
    store.fill_match(last["id"], {}, {
        "track": trk,
        "kill_log": [{"t": ts[8], "kind": "kill", "other": "Rook", "gun": "Galil"},
                     {"t": ts[21], "kind": "kill", "other": "Moth", "gun": "Galil"},
                     {"t": ts[30], "kind": "death", "other": "Cinder", "gun": "Galil"}],
        "downs_log": [{"t": ts[30] - 5, "here": "hotzone"}], "respawns_log": [{"t": ts[31], "here": None}],
        "cash_events": [{"t": ts[4], "delta": -1800, "net": -1800, "vendor": "RPG-7", "here": "fob", "gun": "Galil"}],
        "timeline": [{"t": t, "here": "hotzone" if 10 < i < 30 else None, "gun": "Galil", "net": i * 300}
                     for i, t in enumerate(ts)]})
    return store, sid, balance


def live_round(now, balance, idle):
    """A round 14 minutes in: what the tracker would be reading off the HUD."""
    rng = random.Random(3)
    start = now - 14 * 60
    events, net, feed = [], 0, []
    for i in range(16):
        t = start + 30 + i * 50 + rng.uniform(-10, 10)
        delta = -rng.choice([5300, 1800, 600]) if i in (0, 6, 11) else rng.choice([780, 1170, 2400, 1600, 3100])
        net += delta
        events.append({"t": t, "delta": delta, "net": net, "here": "hotzone" if delta > 700 else None,
                       "gun": "Mosin Nagant", "vendor": None, "items": []})
    labels = [r for r in REWARDS for _ in range(r[3])]
    xp_total, xp_series = 0, []
    for i in range(30):
        label, cash, xp, _w = rng.choice(labels)
        t = start + 20 + i * 27
        feed.append({"label": label, "cash": cash or None, "xp": xp or None, "t": round(t), "here": None, "gun": None})
        if xp:
            xp_total += xp
            xp_series.append([t, xp_total])
    kills = [{"t": round(start + 200), "other": "Kestrel", "gun": "Mosin Nagant", "here": "hotzone", "kind": "kill"},
             {"t": round(start + 470), "other": "Hollis", "gun": "Mosin Nagant", "here": None, "kind": "kill"},
             {"t": round(start + 610), "other": "Pavlov", "gun": None, "here": "hotzone", "kind": "death"},
             {"t": round(start + 790), "other": "Juno", "gun": "Mosin Nagant", "here": "hotzone", "kind": "kill"}]
    rewards = {}
    for e in feed:
        r = rewards.setdefault(e["label"], {"n": 0, "cash": 0, "xp": 0})
        r["n"] += 1
        r["cash"] += e["cash"] or 0
        r["xp"] += e["xp"] or 0
    return {"started": start, "between": idle, "reads": 76, "hot": 41, "fob": 6, "here": "hotzone",
            "last_read": now - 2, "net": net, "balance": balance + net, "start_balance": balance,
            "events": events[-15:], "team": "Viper", "gun": "Mosin Nagant", "guns": {"Mosin Nagant": 60},
            "kills": 3, "deaths": 1, "downs": 1, "down": None, "rewards": rewards, "feed": feed[-12:],
            "vendor": {"Medic": 5300}, "kill_log": kills,
            "used": {"roles": ["Recon"], "items": ["Mosin Nagant", "Large Hammer"]},
            "board": {"view": "Global", "players": 92, "t": now - 40,
                      "me": {"rank": 11, "level": 61, "name": NAME, "cash": net + 12000, "kills": 3, "assists": 5,
                             "deaths": 1, "revives": 0},
                      "above": {"rank": 10, "name": "Rook", "cash": net + 13100},
                      "below": {"rank": 12, "name": "Moth", "cash": net + 9800}},
            "series": {"cash": [[round(e["t"]), e["net"]] for e in events], "xp": xp_series}}


class DemoApp:
    def __init__(self, store, sid, balance, idle, ddir):
        self.store, self.sid, self.balance, self.idle, self.ddir = store, sid, balance, idle, ddir
        self.cfg = {"steam_id": STEAM_ID, "player_name": NAME, "dashboard_port": PORT, "check_updates": False}
        self.lock = threading.Lock()
        self.on_templates_changed = lambda: None

    def status(self):
        now = time.time()
        return {"alive": True, "updated": now, "game_running": True, "game_focused": True, "session": self.sid,
                "templates": 6, "auto_read": True, "last_event": None,
                "hotkey_end": "ctrl+alt+e", "hotkey_start": "ctrl+alt+s",
                "live": live_round(now, self.balance, self.idle)}


def main():
    global PORT
    idle = "--idle" in sys.argv
    if "--port" in sys.argv:
        PORT = int(sys.argv[sys.argv.index("--port") + 1])
    ddir = tempfile.mkdtemp(prefix="wardogs-demo-")
    store, sid, balance = build(ddir, time.time(), idle)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(DemoApp(store, sid, balance, idle, ddir)))
    print(f"Demo dashboard with made-up data: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    if "--no-open" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}/#live")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
