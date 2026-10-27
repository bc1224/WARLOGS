"""Background watcher and live dashboard. Sleeps until WARDOGS is running, then:

* every `scan_seconds` takes a 64x36 thumbnail of the game window
* when the picture has been still for a few seconds, reads its text once and
  logs a match if it is the post-match results screen (no key press needed)
* remembers what that screen looks like, so later ones are caught from the
  thumbnail alone
* Ctrl+Alt+E or Steam's F12 on the results screen still work as a manual log
* notes role level-ups from the game's own save file
* pulls your synced totals from the wardogstracker.gg API now and then
* serves the dashboard at http://127.0.0.1:<dashboard_port>
"""
import ctypes
import difflib
import glob
import json
import logging
import logging.handlers
import os
import queue
import threading
import time

from . import winapi
from .matcher import EndScreenMatcher, distance
from .ocr import ZONE_REWARD, OcrWorker, cash_summary, details, downed, hud_state, kill_feed, promotions, reward_feed, role_progress, top_bar, vendor_item, weapon, looks_like_end_screen, mentions, parse, xp_sources
from .catalog import refresh_catalog
from .sources import pull_api, pull_status, read_role_levels
from .scoreboard import Gray, is_scoreboard, read_board
from .store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {
    "steam_id": "",
    "game_exe": "WardogsClient-Win64-Shipping.exe",
    "steam_app_id": "1867240",
    "steam_userdata": r"C:\Program Files (x86)\Steam\userdata",
    "role_save": r"%LOCALAPPDATA%\Wardogs\Saved\SaveGames\PlayerRoleProgress.sav",
    "hotkey_end": "ctrl+alt+e",
    "hotkey_start": "ctrl+alt+s",
    "scan_seconds": 2.0,
    "idle_seconds": 10.0,
    "api_minutes": 15,
    "match_threshold": 18.0,
    "auto_read": True,
    "still_scans": 2,
    "read_gap_seconds": 10.0,
    "shot_max_width": 2560,
    "read_width": 1920,
    "dashboard_port": 8790,
    "hud_seconds": 10.0,       # how often to read the screen during a match
    "feed_seconds": 3.0,       # how often to read just the kill feed and reward feed corners
    "player_name": "",         # your in-game name, to find your kills and deaths in the kill feed
    "sample_seconds": 180.0,   # keep one plain in-match screenshot this often
    "samples_max": 150,        # screenshots kept in data/samples for tuning
}
SIG_W, SIG_H = 64, 36
STILL = 3.0        # thumbnail change below this counts as "not moving"
NEW_SCREEN = 6.0   # don't re-read a still screen we already read
NEW_ROUND_GAP = 90  # seconds without the top bar before a new starting balance can be a new round
HK_END, HK_START = 1, 2
# screen corners read every few seconds, as fractions of the window: x, y, w, h
FEED_AREAS = ((0.78, 0.045, 0.22, 0.18),   # reward feed under the top-right cash
              (0.0, 0.36, 0.32, 0.24))     # kill feed on the left
MENU_STRIP = (0.0, 0.0, 0.32, 0.045)        # INVENTORY / SCOREBOARD / PROGRESSION tabs of the in-match menu
log = logging.getLogger("wdt")


def is_blank(sig):
    return max(sig) - min(sig) < 12


def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.join(ROOT, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["role_save"] = os.path.expandvars(cfg["role_save"])
    return cfg


def data_dir():
    d = os.environ.get("WDT_DATA") or os.path.join(ROOT, "data")
    os.makedirs(os.path.join(d, "shots"), exist_ok=True)
    return d


class Tracker:
    def __init__(self, cfg, store, ddir):
        self.cfg, self.store, self.ddir = cfg, store, ddir
        self.events = queue.Queue()
        self.matcher = EndScreenMatcher(store.templates(), threshold=cfg["match_threshold"])
        self.ocr = OcrWorker()
        self.session = None
        self.match_start = None
        self.pids = set()
        self.rect = None
        self.seen_shots = set(self._steam_shots())
        self.roles_at_last_match = None
        self.next_api = 0.0
        self.next_status = 0.0
        self.next_live_save = 0.0
        self.next_proc_check = 0.0
        self.next_shot_check = 0.0
        self.prev_sig = None
        self.still = 0
        self.last_read_sig = None
        self.next_read = 0.0
        self.reads = 0
        self.item_names = self.store.unlock_names()
        self.used = {}  # roles/items named on screens during the current match
        self.hud = self._new_hud()
        self.read_size = (1920, 1080)
        self.next_hud = 0.0
        self.digits = None  # scoreboard digit shapes, learned from its cash column
        self.next_feed = 0.0
        self.next_sample = 0.0
        self.last_event = None

    # --- inputs -----------------------------------------------------------
    def _steam_shots(self):
        pattern = os.path.join(self.cfg["steam_userdata"], "*", "760", "remote",
                               self.cfg["steam_app_id"], "screenshots", "*.jpg")
        return glob.glob(pattern)

    def _roles(self):
        try:
            return read_role_levels(self.cfg["role_save"])
        except OSError:
            return None

    def _thumb(self):
        return winapi.signature(winapi.grab(self.rect, SIG_W, SIG_H), SIG_W, SIG_H)

    def on_hotkey(self, hid):
        self.events.put(("hotkey", hid))

    def reload_templates(self):
        """Called by the dashboard after it removes a wrongly learned screen."""
        self.matcher.templates = self.store.templates()

    # --- session lifecycle -------------------------------------------------
    def _start_session(self, now):
        resumed = self.store.open_session()  # tracker restarted while the game was running
        self.session = resumed or self.store.start_session(now)
        self.match_start = (resumed and self.store.last_match_end(resumed)) or now
        self.roles_at_last_match = self._roles()
        self.used = {}
        self.hud = self._new_hud()
        self.next_api = now
        picked_up = resumed and self._load_live(now)
        log.info("%s session %s%s", "resumed" if resumed else "game started,", self.session,
                 " and its round in progress" if picked_up else "")

    # the round in progress is saved now and then, so a tracker restart mid-round picks it
    # back up; only a fresh save from this same session counts. If the round ended while the
    # tracker was down, the next top-bar read shows a new starting balance and _note_cash
    # logs the saved round then
    LIVE_MAX_AGE = 300

    def _save_live(self, now):
        h = dict(self.hud, saved=sorted(self.hud["saved"]),
                 starts=[[k, v] for k, v in self.hud["starts"].items()])
        state = {"session": self.session, "saved_at": now, "match_start": self.match_start,
                 "last_end": self.matcher.last_end if self.matcher.last_end > 0 else None,
                 "used": {k: sorted(v) for k, v in self.used.items()}, "hud": h}
        path = os.path.join(self.ddir, "live.json")
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(path + ".tmp", path)
        except (OSError, TypeError, ValueError) as e:
            log.warning("couldn't save the live round: %s", e)

    def _load_live(self, now):
        try:
            with open(os.path.join(self.ddir, "live.json"), encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return False
        if state.get("session") != self.session or not 0 <= now - state.get("saved_at", 0) <= self.LIVE_MAX_AGE:
            return False
        h = self._new_hud()
        h.update(state["hud"])
        h["saved"] = set(h["saved"])
        h["starts"] = {int(k): v for k, v in h["starts"]}
        self.hud = h
        self.match_start = state.get("match_start") or self.match_start
        if state.get("last_end"):
            self.matcher.last_end = state["last_end"]
        self.used = {k: set(v) for k, v in state.get("used", {}).items()}
        return True

    def _end_session(self, now):
        log.info("game closed, session %s (%d screen reads)", self.session, self.reads)
        self.store.end_session(self.session, now)
        self.session = None
        self.rect = None
        self.ocr.stop()
        self._pull_api(now)

    def _pull_status(self, now):
        self.next_status = now + 600
        try:
            pull_status(self.store)
        except Exception as e:
            log.warning("status fetch failed: %s", e)

    def _pull_api(self, now):
        self.next_api = now + self.cfg["api_minutes"] * 60
        if not self.cfg["steam_id"]:
            return
        try:
            new, unlocked = pull_api(self.store, self.cfg["steam_id"], now)
            if new:
                log.info("new API sync")
            if unlocked:
                log.info("new unlocks: %s", ", ".join(unlocked))
            self.item_names = self.store.unlock_names()
        except Exception as e:  # network trouble is not fatal
            log.warning("API fetch failed: %s", e)
        if not getattr(self, "_catalog_busy", False):  # weekly item prices, off the tick thread
            self._catalog_busy = True
            threading.Thread(target=self._refresh_catalog, daemon=True, name="catalog").start()

    def _refresh_catalog(self):
        try:
            refresh_catalog(self.store)
        except Exception as e:
            log.warning("catalog fetch failed: %s", e)
        finally:
            self._catalog_busy = False

    # --- match end ---------------------------------------------------------
    def _shot_path(self, now):
        return os.path.join(self.ddir, "shots",
                            time.strftime("%Y%m%d-%H%M%S.png", time.localtime(now)))

    def _capture(self, now):
        x, y, w, h = self.rect
        ow = min(w, self.cfg["shot_max_width"])
        oh = round(h * ow / w)
        path = self._shot_path(now)
        winapi.write_png(path, winapi.grab(self.rect, ow, oh), ow, oh)
        return path

    def _learn(self, sig, match_id):
        if sig is None or is_blank(sig):
            # a black or flat frame would match every loading screen
            if sig is not None:
                log.warning("capture looked blank, not learning it as an end screen")
                self.last_event = "capture was blank (see README)"
            return
        known = self.matcher.best(sig)
        if known is not None and known < self.cfg["match_threshold"] / 2:
            return  # already know this screen
        self.matcher.learn(sig)
        self.store.add_template(sig, match_id)
        log.info("learned end-screen look (%d total)", len(self.matcher.templates))

    def record_end(self, source, now, image=None, lines=None, screen=True):
        """Log the match that just ended. screen=False: the round is already over and
        the next one is on screen, so log it from the in-match reads alone."""
        if self.session is None:
            return
        sig = self._thumb() if self.rect and screen else None
        if screen and source != "auto" and self.matcher.recently_ended(now):
            # this end screen is already logged; just remember how it looks
            self._learn(sig, None)
            return
        if screen:
            self.matcher.mark_end(now)
        if image is None and self.rect and screen:
            image = self._capture(now)
        if lines is None and image:
            try:
                lines = self.ocr.read(image)
            except Exception as e:
                log.warning("OCR failed: %s", e)
        parsed = parse(lines) if lines else {}
        info = details(lines) if lines else {}
        if lines:
            self._note_used(lines)
        info["used"] = {k: sorted(v) for k, v in self.used.items()}
        if lines:
            info["sources"] = xp_sources(lines)
            self._note_progress(lines, parsed, info)
        self._round_cash(lines, parsed, info)
        if self.hud["reads"]:
            h = self.hud
            info["hud"] = {"reads": h["reads"], "hot": h["hot"], "fob": h["fob"],
                           "team": max(h["teams"], key=h["teams"].get) if h["teams"] else None,
                           "guns": h["guns"]}
            info["timeline"] = h["timeline"]
            info["cash_events"] = h["events"]
        h = self.hud
        if h["reads"] or h["feed"] or h["kills"] or h["deaths"]:
            info["combat"] = {"kills": len(h["kills"]), "deaths": len(h["deaths"]), "downs": h["downs"]}
            info["kill_log"] = sorted([dict(k, kind="kill") for k in h["kills"]] +
                                      [dict(d, kind="death") for d in h["deaths"]], key=lambda e: e["t"])
            info["downs_log"] = h["downs_log"]
        if h["board"]:
            info["board"] = h["board"]
        for key in ("rewards", "feed", "vendor", "promos"):
            if h[key]:
                info[key] = h[key]
        self.hud = self._new_hud()
        roles = self._roles()
        level_ups = None
        if roles and self.roles_at_last_match:
            level_ups = {k: v for k, v in roles.items()
                         if v > self.roles_at_last_match.get(k, v)} or None
        self.roles_at_last_match = roles or self.roles_at_last_match
        started = self.match_start or self.store.last_match_end(self.session)
        mid = self.store.add_match(self.session, started, now, source, image, lines,
                                   parsed, level_ups, info)
        if source != "auto" and screen:
            self._learn(sig, mid)
        self.match_start = now
        self.used = {}
        self.last_event = f"match {mid} ({source})"
        log.info("match %s ended via %s: %s", mid, source,
                 {k: parsed.get(k) for k in ("xp", "cash_earned", "cash_spent")})
        self.next_api = min(self.next_api, now + 120)

    def _maybe_read(self, sig, now):
        """Read the text of a screen that has stopped moving, once."""
        moving = self.prev_sig is None or distance(sig, self.prev_sig) >= STILL
        self.prev_sig = sig
        self.still = 0 if moving else self.still + 1
        if (self.still < self.cfg["still_scans"] or now < self.next_read or is_blank(sig)
                or self.matcher.recently_ended(now)):
            return
        if self.last_read_sig is not None and distance(sig, self.last_read_sig) < NEW_SCREEN:
            return
        self.last_read_sig = sig
        self.next_read = now + self.cfg["read_gap_seconds"]
        self._read_and_log(now, "auto-read")

    def _note_used(self, lines):
        """Remember roles and owned items named on loadout/deploy screens."""
        for k, names in mentions(lines, self.item_names).items():
            self.used.setdefault(k, set()).update(names)

    def _note_progress(self, lines, parsed, info):
        """Each role card on the Progression tab reads "XP <before> <after>", so the
        round's XP per role is after minus before."""
        prog = role_progress(lines)
        if not prog:
            return
        info["role_xp"] = {r: {"before": a, "after": b, "gain": b - a if 0 <= b - a < 100000 else None}
                           for r, (a, b) in prog.items()}
        gains = [v["gain"] for v in info["role_xp"].values() if v["gain"] is not None]
        if parsed.get("xp") is None and gains:
            parsed["xp"] = sum(gains)
        # its top bar is this round's profit and your balance, not money earned
        parsed["cash_earned"] = None

    def _merge_end(self, now, lines):
        """Another results screen in the same break (the Score, Progression or
        Scoreboard tab): fill in what the match is missing rather than log it twice."""
        self.matcher.mark_end(now)  # the break is still going
        mid = self.store.last_match(self.session)
        if mid is None:
            return
        parsed = parse(lines)
        info = details(lines)
        info["sources"] = xp_sources(lines)
        self._note_progress(lines, parsed, info)
        self._round_cash(lines, parsed, info)
        self.store.fill_match(mid, parsed, info)

    def _round_cash(self, lines, parsed, info):
        """When the results screen gave no cash, use the top bar: profit for the
        round, split into spent (every drop seen during the match) and earned."""
        h = self.hud
        bar = top_bar(lines, *self.read_size) if lines else {}
        net = bar["net"] if "net" in bar else h["net"]
        if net is None and not (lines and cash_summary(lines)):
            return
        info["live_cash"] = {"net": net, "start_balance": h["start_balance"],
                             "end_balance": bar.get("balance", h["balance"])}
        summ = cash_summary(lines) if lines else {}
        if summ:
            info["cash_summary"] = summ
            parsed["cash_spent"] = summ.get("spent")
            earned = summ.get("gained")
            if earned is not None and summ.get("bonus") is not None:
                # some screens count the bonus inside "gained"; trust whichever matches the profit
                with_bonus = earned + summ["bonus"]
                if net is None or abs(with_bonus - (summ.get("spent") or 0) - net) <= abs(earned - (summ.get("spent") or 0) - net):
                    earned = with_bonus
            parsed["cash_earned"] = earned
            return
        cand = parsed.get("candidates", {})
        if bar and all(abs(c) in {abs(v) for v in bar.values()}
                       for c in cand.get("cash_earned", []) + cand.get("cash_spent", [])):
            parsed["cash_earned"] = parsed["cash_spent"] = None  # only the top bar was read
        if parsed.get("cash_earned") is None and parsed.get("cash_spent") is None:
            spent = -sum(e["delta"] for e in h["events"] if e["delta"] < 0)
            spent = max(spent, -net, 0)
            parsed["cash_spent"] = spent or None
            parsed["cash_earned"] = net + spent

    @staticmethod
    def _new_hud():
        return {"reads": 0, "hot": 0, "fob": 0, "teams": {}, "saved": set(), "here": None,
                "last": None, "net": None, "balance": None, "start_balance": None,
                "starts": {}, "events": [], "timeline": [], "guns": {}, "gun": None,
                "feed_prev": [], "kill_prev": [], "rewards": {}, "feed": [], "kills": [], "deaths": [],
                "downs": 0, "downs_log": [], "down": None, "promos": [], "vendor": {},
                "vendor_item": None, "vendor_prev": None, "zone_seen": None, "odd_net": None, "odd_start": None, "life_start": None,
                "cash_seen": None,
                "playing": None, "board": None}

    def _note_cash(self, now, lines):
        """Round profit and balance from the top bar; each change is logged with
        the gear seen on screen at that moment."""
        h = self.hud
        bar = top_bar(lines, *self.read_size)
        if bar.get("menu"):  # lobby or menus: not this round's profit
            return
        net, bal = bar.get("net"), bar.get("balance")
        gap = now - (h.get("cash_seen") or 0)  # since the top bar was last read
        if net is not None or bal is not None:
            h["cash_seen"] = now
        if net is None and bal is not None and h["balance"] is not None and abs(bal - h["balance"]) > 20000:
            bal = None  # a lone balance this far off is a misread ("$160" for "$46,160")
        if (bal is None and net is not None and h["balance"] is not None and h["net"] is not None
                and abs(net - h["balance"]) <= 2000 and abs(net - h["net"]) > 5000):
            net, bal = None, net  # the balance alone, read where the profit sits
        if net is not None and bal is not None:
            s = bal - net  # balance when the round began; the most-seen value wins
            if s < 0:
                return  # the two amounts got mixed up ($29,759 as profit beside a stray $360)
            known = h["start_balance"]
            # redeploying after a death starts the bar's profit over from your balance at
            # that moment (money can move on the deploy screen too); the round goes on, so
            # its profit is balance minus the round's start. A new round comes after a break
            # with no top bar: the results screen and the lobby.
            after_break = gap >= NEW_ROUND_GAP or (h["odd_start"] is not None and abs(s - h["odd_start"]) <= 100)
            if (known is not None and abs(s - known) > 100
                    and (not after_break or abs(s - (h["life_start"] or -1)) <= 100)):
                h["life_start"] = s
                net, s = bal - known, known
            if known is not None and h["starts"][known] >= 2 and abs(s - known) > 100:
                # disagrees with how the round started: a misread, or a new round whose
                # results screen went by unseen (believe that once the same start repeats)
                if h["odd_start"] is None or abs(s - h["odd_start"]) > 100:
                    h["odd_start"] = s
                    return
                self.record_end("hud", now, screen=False)
                return self._note_cash(now, lines)
            h["odd_start"] = None

            h["starts"][s] = h["starts"].get(s, 0) + 1
            h["start_balance"] = max(h["starts"], key=h["starts"].get)
        elif net is not None and h["life_start"] is not None and h["start_balance"] is not None:
            net += h["life_start"] - h["start_balance"]  # profit alone, in a later life
        if net is None and bal is not None and h["start_balance"] is not None:
            net = bal - h["start_balance"]
        if net is not None and h["net"] is not None and abs(net - h["net"]) > 20000:
            # a misread digit swings this by tens of thousands; believe it once it repeats
            if h["odd_net"] is None or abs(net - h["odd_net"]) > 2000:
                h["odd_net"] = net
                return
        h["odd_net"] = None
        if net is not None:
            prev = h["net"] or 0  # profit starts each round at $0
            if net != prev:
                seen = mentions(lines, self.item_names)
                bought = h["vendor_item"] or h["vendor_prev"]  # the drop can show a read after the buy
                h["events"] = (h["events"] + [{"t": now, "delta": net - prev, "net": net, "here": h["here"],
                                              "gun": h.get("gun"), "vendor": bought,
                                              "items": seen["roles"] + seen["items"]}])[-300:]
                if bought and net < prev:
                    h["vendor"][bought] = h["vendor"].get(bought, 0) + prev - net
            h["net"] = net
        if bal is not None:
            h["balance"] = bal

    def live(self, now):
        """What the dashboard's Live tab shows about the match in progress."""
        if self.session is None:
            return None
        h = self.hud
        # "between" until something on screen shows a match is actually being played
        return {"started": h["playing"] or self.match_start,
                "between": self.matcher.recently_ended(now) or h["playing"] is None,
                "reads": h["reads"], "hot": h["hot"], "fob": h["fob"], "here": h["here"],
                "last_read": h["last"], "net": h["net"], "balance": h["balance"],
                "start_balance": h["start_balance"], "events": h["events"][-15:],
                "team": max(h["teams"], key=h["teams"].get) if h["teams"] else None,
                "gun": h["gun"], "guns": h["guns"],
                "kills": len(h["kills"]), "deaths": len(h["deaths"]), "downs": h["downs"], "down": h["down"],
                "rewards": h["rewards"], "feed": h["feed"][-12:], "vendor": h["vendor"],
                "kill_log": sorted([dict(k, kind="kill") for k in h["kills"]] + [dict(d, kind="death") for d in h["deaths"]],
                                   key=lambda e: e["t"])[-8:],
                "used": {k: sorted(v) for k, v in self.used.items()}, "board": h["board"],
                "series": self._series()}

    def _series(self):
        """Round profit and reward-feed XP over time, for the Live tab's line charts."""
        h = self.hud
        cash = [[round(e["t"]), e["net"]] for e in h["events"]]
        xp, total = [], 0
        for e in h["feed"]:
            if e.get("xp"):
                total += e["xp"]
                xp.append([e["t"], total])
        return {"cash": cash, "xp": xp}

    def _note_hud(self, now, lines):
        """Tally what an in-match read shows: Hot Zone, FOB, team name. The first
        screen of each kind per match (and a plain one every few minutes) is kept
        in data/samples so the detection can be checked and tuned."""
        st = hud_state(lines)
        h = self.hud
        h["reads"] += 1
        h["last"] = now
        # the reward feed pays "HOT ZONE PRESENCE" every few seconds while you're in it
        in_zone = st["hot"] or (h["zone_seen"] is not None and now - h["zone_seen"] <= 20)
        st = dict(st, hot=bool(in_zone))
        h["here"] = "hotzone" if in_zone else "fob" if st["fob"] else None
        h["gun"] = weapon(lines, self.item_names)
        if h["gun"]:
            h["guns"][h["gun"]] = h["guns"].get(h["gun"], 0) + 1
        h["vendor_prev"], h["vendor_item"] = h["vendor_item"], vendor_item(lines)
        state = downed(lines)
        if state and not h["down"]:
            h["downs"] += 1
            h["downs_log"].append({"t": round(now), "here": h["here"], "gun": h["gun"]})
        h["down"] = state
        seen_promos = {(p["role"], p["level"]) for p in h["promos"]}
        for role, lvl in promotions(lines):  # chat keeps them up for a while
            if (role, lvl) not in seen_promos:
                seen_promos.add((role, lvl))
                h["promos"].append({"t": round(now), "role": role, "level": lvl})
        self._note_cash(now, lines)
        h = self.hud  # a new round may have started from that read
        seen = mentions(lines, self.item_names)
        h["timeline"] = (h["timeline"] + [{"t": round(now), "here": h["here"], "gun": h["gun"],
                                          "net": h["net"], "bal": h["balance"], "down": state,
                                          "vendor": h["vendor_item"],
                                          "roles": seen["roles"], "items": seen["items"]}])[-720:]
        h["hot"] += st["hot"]
        h["fob"] += st["fob"]
        if (h["net"] is not None or st["hot"] or st["fob"]) and h["playing"] is None:
            h["playing"] = now
        if st["team"]:
            h["teams"][st["team"]] = h["teams"].get(st["team"], 0) + 1
        tag = "hotzone" if st["hot"] else "fob" if st["fob"] else "team" if st["team"] else None
        if tag and tag not in h["saved"]:
            h["saved"].add(tag)
            self._save_sample(now, tag, lines)
        elif now >= self.next_sample:
            self._save_sample(now, "play", lines)

    def _note_feeds(self, now, reward_lines, kill_lines):
        """The reward feed (top right) and kill feed (left) from a quick read of
        just those corners. An entry stays up for a few reads, so only entries
        that weren't there last time count."""
        h = self.hud
        cur = reward_feed(reward_lines)
        if any(ZONE_REWARD.search(e["label"]) and (e["cash"] or 0) > 0 for e in cur):
            h["zone_seen"] = now
        same = lambda a, b: a["cash"] == b["cash"] and a["xp"] == b["xp"] and _alike(a["label"], b["label"])
        for e in _fresh(h["feed_prev"], cur, same):
            r = h["rewards"].setdefault(e["label"], {"n": 0, "cash": 0, "xp": 0})
            r["n"] += 1
            r["cash"] += e["cash"] or 0
            r["xp"] += e["xp"] or 0
            h["feed"] = (h["feed"] + [dict(e, t=round(now), here=h["here"], gun=h["gun"])])[-500:]
        h["feed_prev"] = cur
        cur = kill_feed(kill_lines, self.cfg["player_name"])
        for kind, other in _fresh(h["kill_prev"], cur, lambda a, b: a[0] == b[0] and _alike(a[1], b[1])):
            h["kills" if kind == "kill" else "deaths"].append(
                {"t": round(now), "other": other, "gun": h["gun"], "here": h["here"]})
        h["kill_prev"] = cur
        if (h["rewards"] or h["kills"] or h["deaths"]) and h["playing"] is None:
            h["playing"] = now

    def _read_feeds(self, now):
        x, y, w, h = self.rect
        scale = min(1.0, self.cfg["read_width"] / w)
        got = []
        for fx, fy, fw, fh in FEED_AREAS + (MENU_STRIP,):
            r = (x + round(fx * w), y + round(fy * h), round(fw * w), round(fh * h))
            ow, oh = max(1, round(r[2] * scale)), max(1, round(r[3] * scale))
            lines = self._ocr_frame(winapi.grab(r, ow, oh), ow, oh)
            if lines is None:
                return
            got.append(lines)
        menu = got.pop()
        if any("SCOREBOARD" in (l.get("text") or "").upper() for l in menu if isinstance(l, dict)):
            self.next_hud = min(self.next_hud, now)  # the in-match menu is open: read it next tick
        else:
            self._note_feeds(now, *got)

    def _save_sample(self, now, tag, lines):
        self.next_sample = now + self.cfg["sample_seconds"]
        if not self.rect:
            return
        d = os.path.join(self.ddir, "samples")
        os.makedirs(d, exist_ok=True)
        base = os.path.join(d, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + tag)
        x, y, w, h = self.rect
        ow = min(w, 1280)
        oh = round(h * ow / w)
        try:
            winapi.write_jpg(base + ".jpg", winapi.grab(self.rect, ow, oh), ow, oh)
            with open(base + ".txt", "w", encoding="utf-8") as f:  # what OCR read, with positions
                f.write("\n".join(f"{l.get('x', 0)},{l.get('y', 0)}\t{l.get('text', '')}"
                                  if isinstance(l, dict) else str(l) for l in lines))
        except OSError as e:
            log.warning("sample save failed: %s", e)
        shots = sorted(glob.glob(os.path.join(d, "*.jpg")) + glob.glob(os.path.join(d, "*.png")),
                       key=os.path.basename)
        for old in shots[:-self.cfg["samples_max"]]:
            for p in (old, old[:-4] + ".txt"):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _read_screen(self):
        """OCR the current screen: (frame, w, h, lines), or None."""
        x, y, w, h = self.rect
        rw = min(w, self.cfg["read_width"])
        rh = round(h * rw / w)
        frame = winapi.grab(self.rect, rw, rh)
        self.read_size = (rw, rh)
        lines = self._ocr_frame(frame, rw, rh)
        return None if lines is None else (frame, rw, rh, lines)

    def _ocr_frame(self, frame, rw, rh):
        """Text lines of a grabbed frame, or None if it couldn't be read."""
        # Windows OCR can hold a file open for a while, so every read gets a new name
        # and old ones are removed once they are free
        for old in glob.glob(os.path.join(self.ddir, "read-*.bmp")):
            try:
                os.remove(old)
            except OSError:
                pass
        err = None
        for _ in range(4):
            self.reads += 1
            tmp = os.path.join(self.ddir, f"read-{os.getpid()}-{self.reads}.bmp")
            try:
                winapi.write_bmp(tmp, frame, rw, rh)
            except OSError as e:
                err = e
                continue
            try:
                return self.ocr.read(tmp)
            except Exception as e:
                log.warning("screen read failed: %s", e)
                return None
        log.warning("screen read failed: %s", err)
        return None

    def _note_board(self, now, frame, rw, rh, lines):
        """The Scoreboard tab: your place by cash earned and your kills, assists,
        deaths and revives. While it stays open it is re-read every few seconds."""
        if self.digits is None:
            try:
                with open(os.path.join(self.ddir, "digits.json"), encoding="utf-8") as f:
                    self.digits = json.load(f)
            except (OSError, ValueError):
                self.digits = {}
        board = read_board(lines, Gray(frame, rw, rh), self.cfg["player_name"], self.digits)
        self.next_hud = now + 3
        if not board:
            return
        try:
            with open(os.path.join(self.ddir, "digits.json"), "w", encoding="utf-8") as f:
                json.dump(self.digits, f)
        except OSError:
            pass
        rows = board.pop("rows")
        board.update(t=round(now), shown=len(rows), first=rows[0].get("rank") if rows else None)
        if board["me"] is None and self.hud["board"] and self.hud["board"].get("me"):
            board["me_seen"] = self.hud["board"]["me"]  # scrolled away from your row: keep the last one
            board["me_t"] = self.hud["board"].get("me_t", self.hud["board"]["t"])
        if self.matcher.recently_ended(now):
            self.matcher.mark_end(now)  # a break tab: the Score and Progression tabs still merge after it
            mid = self.store.last_match(self.session)
            if mid is not None and board["me"]:
                self.store.fill_match(mid, {}, {"board_end": board})
            return
        self.hud["board"] = board
        if self.hud["playing"] is None:
            self.hud["playing"] = now
        self._save_sample(now, "scoreboard", lines) if "scoreboard" not in self.hud["saved"] else None
        self.hud["saved"].add("scoreboard")

    def _read_and_log(self, now, source, hud=False):
        """OCR the current screen; log a match if it is a results screen. In-match
        reads (hud=True) also tally Hot Zone / FOB / team."""
        got = self._read_screen()
        if got is None:
            return False
        frame, rw, rh, lines = got
        between = self.matcher.recently_ended(now)
        if is_scoreboard(lines):
            self._note_board(now, frame, rw, rh, lines)
            return False
        if not looks_like_end_screen(lines):
            if not between:  # break screens don't count toward the next match
                self._note_used(lines)
                if hud:
                    self._note_hud(now, lines)
            return False
        if between:
            self._merge_end(now, lines)
            return True
        image = self._shot_path(now)
        winapi.write_png(image, frame, rw, rh)
        self.record_end(source, now, image=image, lines=lines)
        return True

    # --- main loop ---------------------------------------------------------
    def status(self, now):
        return {"pid": os.getpid(), "updated": now, "game_running": bool(self.pids),
                "game_focused": bool(self.rect), "session": self.session,
                "templates": len(self.matcher.templates), "last_event": self.last_event,
                "auto_read": self.cfg["auto_read"], "live": self.live(now),
                "hotkey_end": self.cfg["hotkey_end"], "hotkey_start": self.cfg["hotkey_start"]}

    def _write_status(self, now):
        tmp = os.path.join(self.ddir, "status.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.status(now), f)
        os.replace(tmp, os.path.join(self.ddir, "status.json"))

    def tick(self, now):
        # the process list is the slowest check, so only refresh it every idle_seconds
        if now >= self.next_proc_check:
            self.pids = winapi.find_pids(self.cfg["game_exe"])
            self.next_proc_check = now + self.cfg["idle_seconds"]
        if self.pids and self.session is None:
            self._start_session(now)
        elif not self.pids and self.session is not None:
            self._end_session(now)
        if now >= self.next_status:  # cheap, and the dashboard shows it even when you're not playing
            self._pull_status(now)
        if self.session is None:
            return self.cfg["idle_seconds"]

        self.rect = winapi.foreground_rect(self.pids)
        while not self.events.empty():
            kind, hid = self.events.get()
            if hid == HK_END:
                self.record_end("hotkey", now)
            elif hid == HK_START:
                self.match_start = now
                self.last_event = "match start marked"
            elif hid < 0:
                log.warning("hotkey %s is taken by another program", -hid)
        if now >= self.next_shot_check:
            self.next_shot_check = now + 4
            for shot in self._steam_shots():
                if shot not in self.seen_shots:
                    self.seen_shots.add(shot)
                    time.sleep(0.5)  # let Steam finish writing the file
                    self.record_end("f12", now, image=shot)
        if self.rect:
            sig = self._thumb()
            before = self.matcher.last_end
            if self.matcher.templates and self.matcher.feed(sig, now):
                # looks like a results screen: confirm by reading it before logging,
                # without re-reading the same look-alike screen over and over
                fresh = (self.last_read_sig is None
                         or distance(sig, self.last_read_sig) >= NEW_SCREEN
                         or now >= self.next_read)
                if fresh:
                    self.last_read_sig = sig
                    self.next_read = now + self.cfg["read_gap_seconds"]
                if not (fresh and self._read_and_log(now, "auto")):
                    self.matcher.last_end = before
            elif self.cfg["auto_read"]:
                self._maybe_read(sig, now)
        else:
            self.prev_sig, self.still = None, 0
        if self.rect and self.cfg["auto_read"] and now >= self.next_hud:  # also reads break screens
            self.next_hud = now + self.cfg["hud_seconds"]
            self._read_and_log(now, "auto-read", hud=True)
        if (self.rect and self.cfg["auto_read"] and self.cfg["feed_seconds"] and now >= self.next_feed
                and not self.matcher.recently_ended(now)):
            self.next_feed = now + self.cfg["feed_seconds"]
            self._read_feeds(now)
        if now >= self.next_api:
            self._pull_api(now)
        return self.cfg["scan_seconds"]

    def run(self):
        from .dashboard import start_server
        start_server(self.cfg, self.ddir, on_templates_changed=self.reload_templates)
        threading.Thread(target=winapi.hotkey_loop, daemon=True,
                         args=({HK_END: self.cfg["hotkey_end"],
                                HK_START: self.cfg["hotkey_start"]}, self.on_hotkey)).start()
        self._pull_api(time.time())
        while True:
            now = time.time()
            try:
                wait = self.tick(now)
                self._write_status(now)
                if self.session is not None and now >= self.next_live_save:
                    self.next_live_save = now + 10
                    self._save_live(now)
            except Exception:
                log.exception("tick failed")
                wait = self.cfg["idle_seconds"]
            time.sleep(wait)


def _alike(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.6


def _fresh(prev, cur, same):
    """Entries of cur that have no match in prev (each prev entry matches once)."""
    left, out = list(prev), []
    for c in cur:
        i = next((i for i, p in enumerate(left) if same(p, c)), None)
        if i is None:
            out.append(c)
        else:
            left.pop(i)
    return out


def single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\wardogs-local-tracker")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def main():
    winapi.set_dpi_aware()
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    ddir = data_dir()
    handler = logging.handlers.RotatingFileHandler(os.path.join(ddir, "tracker.log"),
                                                   maxBytes=1_000_000, backupCount=2,
                                                   encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(message)s")
    mutex = single_instance()
    if mutex is None:
        log.info("already running, exiting")
        return
    cfg = load_config()
    store = Store(os.path.join(ddir, "wardogs.db"))
    if not winapi.find_pids(cfg["game_exe"]):  # otherwise the open session is resumed
        store.close_open_sessions()
    log.info("tracker started (pid %s)", os.getpid())
    Tracker(cfg, store, ddir).run()


if __name__ == "__main__":
    main()
