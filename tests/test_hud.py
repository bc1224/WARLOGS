import os
import tempfile
import unittest
from unittest import mock

from wdt.ocr import cash_summary, weapon, hud_state, looks_like_end_screen, parse, role_progress, top_bar
from wdt.store import Store
from wdt.tracker import DEFAULTS, Tracker


def L(text, x=0, y=0):
    return {"text": text, "x": x, "y": y}


class HudStateTests(unittest.TestCase):
    def test_inside_hot_zone(self):
        self.assertEqual(hud_state([L("CAPTURING HOT ZONE"), L("120 / 100")]),
                         {"hot": True, "fob": False, "team": None})

    def test_waypoint_with_distance_is_not_inside(self):
        self.assertEqual(hud_state([L("Hot Zone 320m"), L("FOB 85 m")]),
                         {"hot": False, "fob": False, "team": None})

    def test_fob_and_team(self):
        st = hud_state([L("FOB"), L("Team Viper")])
        self.assertEqual(st, {"hot": False, "fob": True, "team": "Viper"})

    def test_chat_lines_are_ignored(self):
        st = hud_state([L("[ALL] Marlow: green team can yall hurry up"), L("• [TEAM] Nettle: hot zone now")])
        self.assertEqual(st, {"hot": False, "fob": False, "team": None})

    def test_generic_team_words_are_ignored(self):
        self.assertIsNone(hud_state([L("Enemy team"), L("your team")])["team"])


PROGRESSION = [L("SCORE", 180, 30), L("PROGRESSION", 330, 30), L("$24,681", 1650, 34), L("$70,232", 1780, 34),
               L("[ALL] Marlow: green team can yall hurry up and win", 56, 124),
               L("PROMOTED TO MEDIC LVL 15", 64, 190), L("ASSAULT", 665, 633), L("MEDIC", 965, 633),
               L("RECON", 1242, 633), L("XP 19,597 22,500", 657, 682), L("XP 57,885 64,500", 938, 682),
               L("XP 30,537 33,750", 1220, 682), L("NEXT ROUND IN 00:26", 880, 961)]


class ProgressionTests(unittest.TestCase):
    def test_progression_tab_is_an_end_screen_and_totals_are_not_round_xp(self):
        self.assertTrue(looks_like_end_screen(PROGRESSION))
        self.assertIsNone(parse(PROGRESSION)["xp"])
        self.assertEqual(role_progress(PROGRESSION), {"Assault": [19597, 22500], "Medic": [57885, 64500],
                                                      "Recon": [30537, 33750]})


class GunAndMoneyScreenTests(unittest.TestCase):
    def test_weapon_nameplate(self):
        self.assertEqual(weapon([L("MOSIUNAGANT // 7.62XS4MM // SN 2TMHJP5")]), "Mosin Nagant")
        self.assertEqual(weapon([L("KH-2002 // E.E6MM // SN 2TMYJP5")]), "KH-2002")
        self.assertIsNone(weapon([L("MEDICAL // OUICK SLOT")]))

    def test_money_screen_columns(self):
        lines = [L("SPENT", 100, 100), L("-$12,400", 300, 100), L("GAINED", 100, 140), L("$30,100", 300, 140),
                 L("BONUS", 100, 180), L("$6,981", 300, 180)]
        self.assertEqual(cash_summary(lines), {"spent": 12400, "gained": 30100, "bonus": 6981})


class TopBarTests(unittest.TestCase):
    def test_two_amounts_are_profit_then_balance(self):
        self.assertEqual(top_bar([L("-$6,555", 1709, 27), L("$11,528", 1821, 27), L("$9", 100, 27)], 1920, 1080),
                         {"net": -6555, "balance": 11528})

    def test_lone_amount(self):
        self.assertEqual(top_bar([L("$28,475", 1815, 27)], 1920, 1080), {"balance": 28475})
        self.assertEqual(top_bar([L("-$7,680", 1718, 27)], 1920, 1080), {"net": -7680})

    def test_minus_read_as_another_dash(self):
        # a real read, 2026-10-26: the profit's minus sign came back as an underscore
        self.assertEqual(top_bar([L("_ $4,980", 1703, 35), L("$38,075", 1816, 27)], 1920, 1080),
                         {"net": -4980, "balance": 38075})
        for dash in ("~", "–", "—", "−", "="):
            self.assertEqual(top_bar([L(dash + "$4,980", 1703, 27)], 1920, 1080), {"net": -4980})

    def test_cut_off_amounts_are_skipped(self):
        # 2026-10-27: "-$8,520" and "$30,492" came back as -$8 and $39
        self.assertEqual(top_bar([L("-$8,52", 1695, 27), L("$39,4g2", 1814, 27)], 1920, 1080), {})
        self.assertEqual(top_bar([L("-$8,", 1695, 27), L("$30,492", 1814, 27)], 1920, 1080), {"balance": 30492})

    def test_letters_misread_as_digits(self):
        self.assertEqual(top_bar([L("-$8,52O", 1695, 27), L("$3O,492", 1814, 27)], 1920, 1080),
                         {"net": -8520, "balance": 30492})


class TrackerHudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))
        cfg = dict(DEFAULTS, steam_userdata=self.tmp.name, role_save=os.path.join(self.tmp.name, "none.sav"))
        self.t = Tracker(cfg, self.store, self.tmp.name)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_hud_reads_end_up_on_the_match(self):
        self.t._start_session(100)
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("CAPTURING HOT ZONE"), L("Team Viper")])
            self.t._note_hud(130, [L("FOB")])
            self.t._note_hud(150, [L("nothing here")])
        self.t.record_end("hotkey", 200)
        m = self.store.dump()["matches"][0]
        self.assertEqual(m["details"]["hud"], {"reads": 3, "hot": 1, "fob": 1, "team": "Viper", "guns": {}})
        full = self.store.match_full(m["id"])["details"]
        self.assertEqual([e["here"] for e in full["timeline"]], ["hotzone", "fob", None])
        self.assertNotIn("timeline", m["details"])  # kept out of the dashboard's live refresh
        self.assertEqual(self.t.hud["reads"], 0)  # reset for the next match

    def test_round_xp_is_after_minus_before_per_role(self):
        self.t._start_session(100)
        self.t.record_end("auto", 200, lines=PROGRESSION)
        m = self.store.dump()["matches"][0]
        self.assertEqual(m["xp"], 2903 + 6615 + 3213)
        self.assertEqual((m["cash_earned"], m["cash_spent"]), (24681, None))  # top bar profit
        self.assertEqual(m["details"]["role_xp"]["Recon"], {"before": 30537, "after": 33750, "gain": 3213})

    def test_money_screen_replaces_the_top_bar_estimate(self):
        # 2026-10-26: logged from the "wins" banner with cash guessed from the top bar,
        # then the Score tab's real numbers and the Progression tab arrived in the same break
        self.t._start_session(100)
        self.t.hud["net"] = 70957
        self.t.record_end("auto", 200, lines=[L("$70,957", 1587, 25), L("$114,012", 1703, 24), L("MANTICORE"), L("WINS")])
        money = [L("$70,957", 1660, 30), L("$114,012", 1790, 30), L("VICTORY"), L("1ST PLACE + CONTRIBUTION", 599, 808),
                 L("$13,200", 1298, 808), L("CASH SPENT", 599, 863), L("-$12,823", 1288, 863), L("CASH EARNED", 599, 918),
                 L("$71,410", 1304, 918), L("TOTAL PROFIT", 599, 1028), L("$70,957", 1297, 1028)]
        self.t._merge_end(230, money)
        board = {"view": "Global", "players": 84, "rows": [], "me": {"rank": 15, "cash": 71410}, "above": None, "below": None}
        with mock.patch("wdt.tracker.read_board", return_value=board):
            self.t._note_board(300, b"", 1, 1, [])  # sitting on the Scoreboard tab keeps the break going
        self.assertTrue(self.t.matcher.recently_ended(380))
        self.t._merge_end(380, [L("MEDIC"), L("XP 82,178 84,750"), L("RECON"), L("XP 43,183 43,750")])
        m = self.store.dump()["matches"][0]
        self.assertEqual((m["cash_earned"], m["cash_spent"], m["xp"]), (71410, 12823, 2572 + 567))
        self.assertEqual(m["details"]["board_end"]["me"]["rank"], 15)

    def test_a_learned_end_screen_logs_its_own_round(self):
        # 2026-10-27: a 1.5 hour round's results screen matched a learned look, but the
        # matcher marked the end before the read, so it was merged into the round before
        self.t._start_session(100)
        self.t.record_end("auto", 200, lines=PROGRESSION)
        self.t.matcher.templates = [[0] * 4]
        money = [L("$70,957", 1660, 30), L("$114,012", 1790, 30), L("VICTORY"), L("CASH SPENT", 599, 863),
                 L("-$12,823", 1288, 863), L("CASH EARNED", 599, 918), L("$71,410", 1304, 918),
                 L("TOTAL PROFIT", 599, 1028), L("$70,957", 1297, 1028)]
        with mock.patch.object(self.t, "_read_screen", return_value=(b"", 1, 1, money)), \
                mock.patch.object(self.t, "_shot_path", return_value=os.path.join(self.tmp.name, "s.png")), \
                mock.patch("wdt.tracker.winapi.write_png"):
            self.t._auto_end([0] * 4, 6000)
            self.t._auto_end([0] * 4, 6003)
        first, second = self.store.dump()["matches"]
        self.assertEqual((first["cash_earned"], first["cash_spent"]), (24681, None))
        self.assertEqual((second["started_at"], second["ended_at"]), (200, 6003))
        self.assertEqual((second["cash_earned"], second["cash_spent"]), (71410, 12823))

    def test_a_short_xp_screen_after_the_money_screen_is_read(self):
        # 2026-10-27: the XP tab shows for a few seconds after the money screen, and reading
        # the screen only every 10 s during the break missed it in most rounds
        self.t._start_session(100)
        self.t.record_end("auto", 200, lines=[L("VICTORY"), L("CASH EARNED", 599, 918), L("$71,410", 1304, 918)])
        self.t.next_read = 210  # the money screen read just now
        self.assertEqual(self.t._scan_wait(201), 1.0)  # every second until the XP tab is read
        with mock.patch.object(self.t, "_read_screen", return_value=(b"", 1, 1, PROGRESSION)):
            self.t._maybe_read([10, 200, 10, 200], 202)  # read on the first frame, not once it settles
        self.assertEqual(self.store.dump()["matches"][0]["xp"], 2903 + 6615 + 3213)
        self.assertEqual(self.t._scan_wait(203), DEFAULTS["scan_seconds"])  # back to the usual pace

    def test_live_cash_and_round_split(self):
        self.t._start_session(100)
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("$18,083", 1817, 27)])
            self.t._note_hud(120, [L("-$6,555", 1709, 27), L("$11,528", 1821, 27)])
            self.t._note_hud(130, [L("$1,362", 1717, 27), L("$19,445", 1816, 27)])
            self.t._note_hud(140, [L("$30,470", 1814, 27)])  # profit hidden, balance only
        live = self.t.live(141)
        self.assertEqual((live["net"], live["start_balance"], live["balance"]), (12387, 18083, 30470))
        self.assertEqual([e["delta"] for e in live["events"]], [-6555, 7917, 11025])
        self.t.record_end("hotkey", 200, lines=[L("VICTORY"), L("+1,200 XP")])
        m = self.store.dump()["matches"][0]
        self.assertEqual((m["cash_earned"], m["cash_spent"]), (18942, 6555))

    def test_winner_banner_ends_the_match_and_progression_fills_it_in(self):
        banner = [L("$13,000", 1588, 25), L("$48,285", 1704, 24), L("62", 1836, 26),
                  L("[ALL] Foul Brickhouse: GG", 44, 132), L("VALKYRA", 986, 453), L("WINS", 1091, 578)]
        self.assertTrue(looks_like_end_screen(banner))
        self.assertFalse(looks_like_end_screen([L("[ALL] Bob: blue team wins")]))
        self.assertTrue(looks_like_end_screen([L("SPENT"), L("-$12,400"), L("GAINED"), L("$30,100"), L("BONUS"), L("$6,981")]))
        self.assertFalse(looks_like_end_screen([L("$1,600"), L("Defibrillator cost")]))
        self.t._start_session(100)
        self.t.rect = (0, 0, 1920, 1080)
        reads = iter([banner, PROGRESSION])
        with mock.patch.object(self.t, "_read_screen", side_effect=lambda: (b"", 1920, 1080, next(reads))), \
                mock.patch("wdt.tracker.winapi.write_png"):
            self.assertTrue(self.t._read_and_log(200, "auto-read", hud=True))
            self.assertTrue(self.t._read_and_log(230, "auto-read", hud=True))
        ms = self.store.dump()["matches"]
        self.assertEqual(len(ms), 1)
        self.assertEqual((ms[0]["xp"], ms[0]["cash_earned"]), (12731, 13000))
        self.assertIn("role_xp", ms[0]["details"])

    def test_restart_resumes_the_open_session(self):
        sid = self.store.start_session(50)
        self.t._start_session(100)
        self.assertEqual(self.t.session, sid)

    def test_restart_mid_round_picks_the_round_back_up(self):
        self.t._start_session(100)
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("-$6,555", 1709, 27), L("$11,528", 1821, 27), L("INSIDE HOT ZONE")])
            self.t._note_hud(120, [L("$1,362", 1709, 27), L("$19,445", 1821, 27)])
        self.t._note_used([L("MEDIC")])
        self.t._save_live(125)
        before = self.t.live(125)
        t2 = Tracker(self.t.cfg, self.store, self.tmp.name)
        t2._start_session(200)
        after = t2.live(200)
        for k in ("started", "net", "balance", "start_balance", "events", "hot"):
            self.assertEqual(after[k], before[k], k)
        self.assertEqual(t2.used["roles"], {"Medic"})
        with mock.patch.object(t2, "_save_sample"):  # and it keeps going from there
            t2._note_hud(210, [L("$2,000", 1709, 27), L("$20,083", 1821, 27)])
        self.assertEqual([e["delta"] for e in t2.live(211)["events"]], [-6555, 7917, 638])

    def test_a_round_that_ended_during_the_restart_is_not_continued(self):
        self.t._start_session(100)
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("-$6,555", 1709, 27), L("$11,528", 1821, 27)])
            self.t._note_hud(120, [L("$1,362", 1709, 27), L("$19,445", 1821, 27)])
        self.t._save_live(125)
        t2 = Tracker(self.t.cfg, self.store, self.tmp.name)
        t2._start_session(200)
        with mock.patch.object(t2, "_save_sample"), mock.patch.object(t2, "_roles", return_value={}):
            t2._note_hud(205, [L("WAITING FOR WORLD TO LOAD", 765, 118), L("$22,500", 1811, 27)])
            t2._note_hud(210, [L("-$3,000", 1709, 27), L("$19,500", 1821, 27)])  # next round, from $22,500 after the bonus
            t2._note_hud(220, [L("-$2,800", 1709, 27), L("$19,700", 1821, 27)])
        ms = self.store.dump()["matches"]
        self.assertEqual([(m["source"], m["cash_earned"] - m["cash_spent"]) for m in ms], [("hud", 1362)])
        live = t2.live(221)
        self.assertEqual((live["net"], live["start_balance"], [e["net"] for e in live["events"]]), (-2800, 22500, [-3000, -2800]))

    def test_old_saved_rounds_are_not_picked_up(self):
        self.t._start_session(100)
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("-$6,555", 1709, 27), L("$11,528", 1821, 27)])
        self.t._save_live(125)
        t2 = Tracker(self.t.cfg, self.store, self.tmp.name)
        t2._start_session(125 + 3600)
        self.assertIsNone(t2.live(125 + 3600)["net"])


if __name__ == "__main__":
    unittest.main()
