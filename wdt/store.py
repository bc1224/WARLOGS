"""SQLite storage for sessions, matches, API snapshots and screen templates."""
import json
import re
import sqlite3
import time

from .ocr import NOT_TEAM

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY, session_id INTEGER, started_at REAL, ended_at REAL NOT NULL,
  xp INTEGER, cash_earned INTEGER, cash_spent INTEGER,
  source TEXT, screenshot TEXT, ocr_json TEXT, role_levels TEXT,
  reviewed INTEGER NOT NULL DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY, taken_at REAL NOT NULL, synced_at TEXT UNIQUE, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY, created_at REAL NOT NULL, sig BLOB NOT NULL, match_id INTEGER);
CREATE TABLE IF NOT EXISTS unlocks (
  game_id TEXT PRIMARY KEY, name TEXT, grp TEXT, url TEXT, image TEXT, level INTEGER,
  first_seen REAL NOT NULL, synced_at TEXT, baseline INTEGER NOT NULL DEFAULT 0,
  level_seen REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT, updated REAL);
"""
HINT_RE = re.compile(r"xp|\$", re.I)

EDITABLE = ("xp", "cash_earned", "cash_spent", "reviewed", "notes")
SETTINGS = ("daily_xp_goal", "weekly_xp_goal")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(templates)")}
        if "match_id" not in cols:  # databases made before templates were linked to matches
            self.db.execute("ALTER TABLE templates ADD COLUMN match_id INTEGER")
        if "details" not in {r[1] for r in self.db.execute("PRAGMA table_info(matches)")}:
            self.db.execute("ALTER TABLE matches ADD COLUMN details TEXT")
        # older versions logged "Revived Team Mate" as a team called "mate"
        self.db.execute(
            f"""UPDATE matches SET details = json_set(details, '$.hud.team', json('null'))
                WHERE json_valid(details) AND lower(json_extract(details, '$.hud.team'))
                IN ({",".join("?" * len(NOT_TEAM))})""", tuple(NOT_TEAM))

    def version(self):
        """Changes whenever another connection (the tracker) writes."""
        return self.db.execute("PRAGMA data_version").fetchone()[0]

    # sessions
    def start_session(self, at=None):
        return self.db.execute("INSERT INTO sessions(started_at) VALUES (?)",
                               (at or time.time(),)).lastrowid

    def open_session(self):
        """The session still open after a tracker restart mid-game, if any."""
        row = self.db.execute("SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def end_session(self, sid, at=None):
        self.db.execute("UPDATE sessions SET ended_at=? WHERE id=?", (at or time.time(), sid))

    def close_open_sessions(self):
        """Sessions left open by a crash end at their last match (or start)."""
        self.db.execute("""UPDATE sessions SET ended_at = COALESCE(
            (SELECT MAX(ended_at) FROM matches m WHERE m.session_id = sessions.id), started_at)
            WHERE ended_at IS NULL""")

    # matches
    def add_match(self, session_id, started_at, ended_at, source, screenshot=None,
                  ocr=None, parsed=None, role_levels=None, details=None):
        parsed = parsed or {}
        return self.db.execute(
            """INSERT INTO matches(session_id, started_at, ended_at, xp, cash_earned, cash_spent,
               source, screenshot, ocr_json, role_levels, details)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, started_at, ended_at, parsed.get("xp"), parsed.get("cash_earned"),
             parsed.get("cash_spent"), source, screenshot,
             json.dumps(ocr) if ocr is not None else None,
             json.dumps(role_levels) if role_levels else None,
             json.dumps(details) if details else None)).lastrowid

    def update_match(self, mid, fields):
        cols = {k: v for k, v in fields.items() if k in EDITABLE}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE matches SET {sets} WHERE id=?", (*cols.values(), mid))

    def add_end_shot(self, mid, path):
        """Keep a picture of another results tab (cash, XP) with the match."""
        row = self.db.execute("SELECT details FROM matches WHERE id=?", (mid,)).fetchone()
        if not row:
            return
        det = json.loads(row[0]) if row[0] else {}
        det.setdefault("end_shots", []).append(path)
        self.db.execute("UPDATE matches SET details=? WHERE id=?", (json.dumps(det), mid))

    def fill_match(self, mid, parsed, info):
        """Fill what a match is still missing (XP, cash, detail keys) from another
        results screen of the same break; nothing already there is overwritten."""
        row = self.db.execute("SELECT xp, cash_earned, cash_spent, details FROM matches WHERE id=?",
                              (mid,)).fetchone()
        if not row:
            return
        xp, earned, spent, det = row
        det = json.loads(det) if det else {}
        for k, v in info.items():
            if v not in (None, {}, []) and det.get(k) in (None, {}, []):
                det[k] = v
        if xp is None:
            xp = parsed.get("xp")
        if earned is None and spent is None:
            earned, spent = parsed.get("cash_earned"), parsed.get("cash_spent")
        elif info.get("cash_summary") and parsed.get("cash_earned") is not None and not det.get("summary_used"):
            # the Score tab's own spent/earned beat an estimate from the top bar
            earned, spent, det["summary_used"] = parsed["cash_earned"], parsed.get("cash_spent"), True
        self.db.execute("UPDATE matches SET xp=?, cash_earned=?, cash_spent=?, details=? WHERE id=?",
                        (xp, earned, spent, json.dumps(det), mid))

    def match_full(self, mid):
        """One match with everything kept for it: timeline, money changes, screen text."""
        row = self.db.execute("SELECT details, ocr_json FROM matches WHERE id=?", (mid,)).fetchone()
        if not row:
            return None
        det = json.loads(row[0]) if row[0] else {}
        ocr = json.loads(row[1]) if row[1] else []
        return {"details": det, "lines": [l.get("text", "") if isinstance(l, dict) else str(l) for l in ocr]}

    def match_text(self, mid):
        """Every line read from a match's results screen, for its detail view."""
        row = self.db.execute("SELECT ocr_json FROM matches WHERE id=?", (mid,)).fetchone()
        return [l.get("text", "") if isinstance(l, dict) else str(l)
                for l in json.loads(row[0])] if row and row[0] else []

    def last_match(self, session_id):
        row = self.db.execute("SELECT id FROM matches WHERE session_id=? ORDER BY ended_at DESC LIMIT 1",
                              (session_id,)).fetchone()
        return row[0] if row else None

    def delete_match(self, mid, forget_screen=False):
        """forget_screen also drops the end-screen template learned from this match,
        for when something that wasn't an end screen got logged."""
        self.db.execute("DELETE FROM matches WHERE id=?", (mid,))
        if forget_screen:
            self.db.execute("DELETE FROM templates WHERE match_id=?", (mid,))

    def last_match_end(self, session_id):
        row = self.db.execute("SELECT MAX(ended_at) FROM matches WHERE session_id=?",
                              (session_id,)).fetchone()
        if row[0] is None:  # no match yet: the first one began with the session
            row = self.db.execute("SELECT started_at FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row[0] if row else None

    # snapshots
    def add_snapshot(self, stats, at=None):
        """Store an API stats snapshot; returns False if this sync was already stored."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO snapshots(taken_at, synced_at, data) VALUES (?,?,?)",
            (at or time.time(), stats.get("syncedAt"), json.dumps(stats)))
        return cur.rowcount == 1

    def has_snapshot(self, synced_at):
        return self.db.execute("SELECT 1 FROM snapshots WHERE synced_at=?",
                               (synced_at,)).fetchone() is not None

    # unlocks
    def unlock_count(self):
        return self.db.execute("SELECT COUNT(*) FROM unlocks").fetchone()[0]

    def sync_unlocks(self, unl, synced_at, at=None):
        """Record unlocks from an API pull. The very first pull is the baseline
        (things you had before tracking); after that, new items and level changes
        are stamped with when they were first seen. Returns their names."""
        at = at or time.time()
        baseline = self.unlock_count() == 0
        have = {r[0]: r[1] for r in self.db.execute("SELECT game_id, level FROM unlocks")}
        changes = []
        for u in unl["items"]:
            if u["game_id"] not in have:
                self.db.execute(
                    """INSERT INTO unlocks(game_id, name, grp, url, image, level, first_seen,
                       synced_at, baseline) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (u["game_id"], u["name"], u["grp"], u["url"], u["image"], u["level"],
                     at, synced_at, int(baseline)))
                if not baseline:
                    changes.append(u["name"])
            else:
                self.db.execute("UPDATE unlocks SET name=?, grp=?, url=?, image=? WHERE game_id=?",
                                (u["name"], u["grp"], u["url"], u["image"], u["game_id"]))
                if u["level"] is not None and u["level"] != have[u["game_id"]]:
                    self.db.execute("UPDATE unlocks SET level=?, level_seen=? WHERE game_id=?",
                                    (u["level"], at, u["game_id"]))
                    changes.append(f"{u['name']} (level {u['level']})")
        return changes

    def unlock_names(self):
        return [r[0] for r in self.db.execute("SELECT name FROM unlocks")]

    # small JSON values: settings, server status, leaderboard neighbours
    def get_meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES (?,?,?)",
                        (key, json.dumps(value), time.time()))

    def online(self):
        """False once Online mode is switched off on the dashboard: then nothing talks to
        GitHub, wardogstracker.gg or the report relay until it's switched back on."""
        return not self.get_meta("offline")

    def set_online(self, on):
        self.set_meta("offline", not on)

    def auto_update(self):
        """True once Install updates automatically is switched on (it starts off)."""
        return bool(self.get_meta("auto_update"))

    def set_auto_update(self, on):
        self.set_meta("auto_update", bool(on))

    def save_settings(self, fields):
        cur = self.get_meta("settings") or {}
        for k in SETTINGS:
            if k in fields:
                try:
                    v = int(float(fields[k]))
                except (TypeError, ValueError, OverflowError):  # "inf" too
                    v = None
                cur[k] = v if v and v > 0 else None
        self.set_meta("settings", cur)
        return cur

    # templates
    def add_template(self, sig, match_id=None):
        self.db.execute("INSERT INTO templates(created_at, sig, match_id) VALUES (?,?,?)",
                        (time.time(), sig, match_id))

    def templates(self):
        return [bytes(r[0]) for r in self.db.execute("SELECT sig FROM templates")]

    # dashboard
    def dump(self):
        rows = lambda q: [dict(r) for r in self.db.execute(q)]
        snaps = rows("SELECT taken_at, synced_at, data FROM snapshots ORDER BY synced_at")
        for s in snaps:
            s["data"] = json.loads(s["data"])
        matches = rows("SELECT * FROM matches ORDER BY ended_at")
        for m in matches:
            # only the lines that mention XP or cash, to keep live refreshes small
            ocr = json.loads(m.pop("ocr_json")) if m.get("ocr_json") else []
            m["ocr_hint"] = [l["text"] for l in ocr if HINT_RE.search(l.get("text", ""))][:8]
            m["role_levels"] = json.loads(m["role_levels"]) if m.get("role_levels") else None
            m["details"] = json.loads(m["details"]) if m.get("details") else None
            if m["details"]:  # the full timeline is fetched per match
                m["details"].pop("timeline", None)
                m["details"].pop("cash_events", None)
                m["details"].pop("feed", None)
                m["details"].pop("kill_log", None)
                m["details"].pop("downs_log", None)
                m["details"].pop("revives_log", None)
                m["details"].pop("respawns_log", None)
        return {
            "sessions": rows("SELECT * FROM sessions ORDER BY started_at"),
            "matches": matches,
            "snapshots": snaps,
            "unlocks": rows("SELECT * FROM unlocks ORDER BY first_seen DESC, name"),
            "meta": {r["key"]: dict(json.loads(r["value"]) if r["value"].startswith("{") else {"value": json.loads(r["value"])}, _updated=r["updated"])
                     for r in self.db.execute("SELECT key, value, updated FROM meta")},
            "templates": self.db.execute("SELECT COUNT(*) FROM templates").fetchone()[0],
        }
