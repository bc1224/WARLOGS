import os
import tempfile
import unittest
from unittest import mock

from wdt.ocr import details, downed, kill_feed, promotions, reward_feed, vendor_item
from wdt.store import Store
from wdt.tracker import DEFAULTS, Tracker


def L(text, x=0, y=0):
    return {"text": text, "x": x, "y": y}


# text read from real in-match samples (data/samples, 2026-10-26)
class RewardFeedTests(unittest.TestCase):
    def test_cash_and_xp_entries(self):
        got = reward_feed([L("CONTROL ZONE PRESENCE *$200"), L("REVIVED PLAYER ASSIST 50XP +"),
                           L("VEHICLE REFUELLING -$5"), L("BRIBE -$500"), L("[ +$15"), L("+$20")])
        self.assertEqual(got, [{"label": "Control Zone Presence", "cash": 200, "xp": None},
                               {"label": "Revived Player Assist", "cash": None, "xp": 50},
                               {"label": "Vehicle Refuelling", "cash": -5, "xp": None},
                               {"label": "Bribe", "cash": -500, "xp": None}])

    def test_xp_entries_as_the_game_shows_them(self):
        # 2026-10-28 samples: the number runs into "XP" and an icon follows it
        got = reward_feed([L("TARGET SPOTTED 25XP O"), L("TARGET SPOTTED REFRESHED 13XP O"),
                           L("TARGET SPOTTED REFRESHED soxp O"), L("TARGET SPOTTED REFRESHED loxp O"),
                           L("VEHI ,LECJESTRPYED ASSI T 7SXP"), L("150XP O"), L("50XP +"), L("TEAMMATE 250XP +")])
        self.assertEqual([(g["label"], g["xp"]) for g in got],
                         [("Target Spotted", 25), ("Target Spotted Refreshed", 13), ("Target Spotted Refreshed", 50),
                          ("Target Spotted Refreshed", 10), ("Vehicle Destroyed Assist", 75), ("Other", 150),
                          ("Other", 50), ("Teammate", 250)])

    def test_lone_cash_amounts_stay_out(self):
        self.assertEqual(reward_feed([L("+$25"), L("$63,900"), L("100%"), L("68")]), [])

    def test_ocr_noise_maps_to_known_labels(self):
        got = reward_feed([L("CJERYIYEPPLAYER ASSIST 38xp"), L("DEPLOYED PLAYER ASSIST 38XP")])
        self.assertEqual([g["label"] for g in got], ["Player Assist", "Deployed Player Assist"])


class KillFeedTests(unittest.TestCase):
    def test_kills_and_deaths(self):
        lines = [L("Dogtag Hollisx", 53, 502), L("V (KILO] Dogtag (KNTX]Pavlov Helldiver", 36, 525),
                 L("[A2M]junobug [MEDC]Sixgunner v'", 52, 573), L("e [OLD] Dogtag Wrenx", 36, 549),
                 L("• [TEAM] Dogtag: push the hot zone", 44, 68)]
        self.assertEqual(kill_feed(lines, "Dogtag"),
                         [("kill", "Hollisx"), ("kill", "Pavlov Helldiver"), ("kill", "Wrenx")])
        self.assertEqual(kill_feed([L("Foul Brickhouse Dogtag")], "Dogtag"), [("death", "Foul Brickhouse")])
        self.assertEqual(kill_feed(lines, ""), [])


class ScreenStateTests(unittest.TestCase):
    def test_downed(self):
        self.assertEqual(downed([L("GIVE UP", 796, 967)]), "down")
        self.assertEqual(downed([L("BEING REVIVED BY", 922, 868), L("GIVE UP")]), "reviving")
        self.assertIsNone(downed([L("DEPLOY FLARES")]))

    def test_vendor_item(self):
        lines = [L("// Garage Vendor", 222, 14), L("WEAPONS", 632, 23), L("AMMO", 774, 23),
                 L("INDIVIDUAL FIRST AID KIT", 796, 356), L("MEDICAL // OUICK SLOT", 796, 378), L("C] Purchase", 781, 573)]
        self.assertEqual(vendor_item(lines), "Individual First Aid Kit")
        self.assertIsNone(vendor_item([L("INDIVIDUAL FIRST AID KIT"), L("MEDICAL // OUICK SLOT")]))  # not the vendor

    def test_promotions(self):
        self.assertEqual(promotions([L("A PROMOTED TD ASSAULT LVL9"), L("PROMOTED TO PILOT LVI_g"),
                                     L("5 WARODG LVLE2"), L("+ MEDIC LVL 16")]),
                         [("Assault", 9), ("Pilot", None)])

    def test_map_name_from_end_screen(self):
        lines = [L("BAKURANI 45'15.O\"N 122'25.OB\"W"), L("VALKYRA"), L("WINS")]
        self.assertEqual(details(lines)["map"], "Bakurani")


class TrackerFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))
        cfg = dict(DEFAULTS, steam_userdata=self.tmp.name, role_save=os.path.join(self.tmp.name, "none.sav"),
                   player_name="Dogtag")
        self.t = Tracker(cfg, self.store, self.tmp.name)
        self.t._start_session(100)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_feed_entries_count_once_while_they_stay_on_screen(self):
        reads = [([L("CONTROL ZONE PRESENCE +$200")], [L("Dogtag Hollisx")]),
                 ([L("CONTROL ZONE PRESENCE *$200"), L("BRIBE -$500")], [L("Dogtag Hollisx"), L("Foul Brickhouse Dogtag")]),
                 ([], []),
                 ([L("CONTROL ZONE PRESENCE +$200")], [])]
        for i, (reward, kill) in enumerate(reads):
            self.t._note_feeds(110 + 3 * i, reward, kill)
        live = self.t.live(130)
        self.assertEqual((live["kills"], live["deaths"]), (1, 1))
        self.assertEqual(live["rewards"]["Control Zone Presence"], {"n": 2, "cash": 400, "xp": 0})
        self.assertEqual(live["rewards"]["Bribe"]["cash"], -500)

    def test_zone_presence_rewards_mean_you_are_in_the_hot_zone(self):
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_feeds(108, [L("HOT ZONE PRESENCE +$320")], [])
            self.t._note_hud(110, [L("nothing")])
            self.t._note_hud(140, [L("nothing")])  # no presence pay for 30 s
        self.assertEqual([r["here"] for r in self.t.hud["timeline"]], ["hotzone", None])
        self.assertEqual(self.t.hud["hot"], 1)

    def test_misread_top_bar_does_not_swing_profit(self):
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("$9,835", 1706, 27), L("$44,920", 1818, 27)])
            self.t._note_hud(120, [L("$160", 1818, 27)])  # balance misread as $160
            self.t._note_hud(130, [L("$160", 1818, 27)])
            self.t._note_hud(140, [L("$11,815", 1706, 27), L("$46,900", 1818, 27)])
        self.assertEqual([e["net"] for e in self.t.live(141)["events"]], [9835, 11815])

    def test_profit_and_balance_must_agree_with_the_round_start(self):
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("-$12,220", 1695, 27), L("$26,792", 1814, 27)])
            self.t._note_hud(120, [L("-$8,840", 1695, 27), L("$30,172", 1814, 27)])
            self.t._note_hud(130, [L("-$8", 1695, 27), L("$39", 1814, 27)])  # both cut off
            self.t._note_hud(140, [L("-$8,520", 1695, 27), L("$30,492", 1814, 27)])
        live = self.t.live(141)
        self.assertEqual([e["net"] for e in live["events"]], [-12220, -8840, -8520])
        self.assertEqual((live["net"], live["balance"], live["start_balance"]), (-8520, 30492, 39012))

    def test_a_pair_that_implies_a_negative_start_is_ignored(self):
        with mock.patch.object(self.t, "_save_sample"):  # 2026-10-27: HUD showed -$12,938 / $29,759
            self.t._note_hud(110, [L("$29,759", 1700, 27), L("$360", 1830, 27)])
            self.t._note_hud(120, [L("-$12,938", 1695, 27), L("$29,759", 1814, 27)])
        live = self.t.live(121)
        self.assertEqual((live["net"], live["start_balance"], [e["net"] for e in live["events"]]), (-12938, 42697, [-12938]))

    def test_redeploying_restarts_the_bar_but_not_the_round(self):
        # 2026-10-27: downed at $10,536 / $53,233, then redeployed; the bar's profit starts over
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$8,096", 1695, 27), L("$50,793", 1814, 27)])
            self.t._note_hud(120, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(130, [L("-$13,292", 1695, 27), L("$39,941", 1814, 27)])
            self.t._note_hud(140, [L("-$12,709", 1695, 27), L("$40,524", 1814, 27)])
            self.t._note_hud(150, [L("-$12,549", 1695, 27)])  # profit alone, later in that life
            self.t._note_hud(160, [L("$40,844", 1814, 27)])  # balance alone
        self.assertIsNone(self.t.store.last_match(self.t.session))
        live = self.t.live(161)
        self.assertEqual(live["start_balance"], 42697)
        self.assertEqual([e["net"] for e in live["events"]], [8096, 10536, -2756, -2173, -2013, -1853])

    def test_money_changing_while_dead_is_still_a_redeploy(self):
        # 2026-10-27: died, the balance moved on the deploy screen, then the bar started over
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(120, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(130, [L("-$13,292", 1695, 27), L("$39,941", 1814, 27)])
            self.t._note_hud(140, [L("$42,844", 1814, 27)])
            self.t._note_hud(150, [L("$42,844", 1695, 27)])  # the balance alone, taken for profit
            self.t._note_hud(160, [L("$42,844", 1695, 27)])
            self.t._note_hud(170, [L("-$920", 1695, 27), L("$41,924", 1814, 27)])
            self.t._note_hud(180, [L("-$760", 1695, 27), L("$42,084", 1814, 27)])
            self.t._note_hud(190, [L("-$760", 1695, 27)])
        self.assertIsNone(self.t.store.last_match(self.t.session))
        live = self.t.live(191)
        self.assertEqual(live["start_balance"], 42697)
        self.assertEqual([e["net"] for e in live["events"]], [10536, -2756, 147, -773, -613])

    def test_a_lone_profit_that_disagrees_with_the_balance_is_skipped(self):
        # 2026-10-27 4:15 PM: "-$733" alone while the balance still said -$10,990
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("-$12,220", 1695, 27), L("$26,792", 1814, 27)])
            self.t._note_hud(120, [L("-$12,220", 1695, 27), L("$26,792", 1814, 27)])
            self.t._note_hud(130, [L("-$733", 1695, 27)])
            self.t._note_hud(140, [L("-$12,100", 1695, 27)])  # close enough: money moved a little
        self.assertEqual([e["net"] for e in self.t.live(141)["events"]], [-12220, -12100])

    def test_a_long_wait_to_redeploy_is_still_the_same_round(self):
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(120, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            # three minutes dead, then the bar starts over from your balance
            self.t._note_hud(300, [L("-$1,500", 1695, 27), L("$51,733", 1814, 27)])
            self.t._note_hud(310, [L("-$1,500", 1695, 27), L("$51,733", 1814, 27)])
        self.assertIsNone(self.t.store.last_match(self.t.session))
        self.assertEqual(self.t.live(311)["net"], 9036)

    def test_a_redeploy_is_logged_as_a_respawn(self):
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(120, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(130, [L("-$13,292", 1695, 27), L("$39,941", 1814, 27), L("FOB")])
            self.t._note_hud(140, [L("-$12,709", 1695, 27), L("$40,524", 1814, 27)])  # same life
            self.t._note_hud(150, [L("-$12,549", 1695, 27), L("$40,684", 1814, 27)])
        live = self.t.live(151)
        self.assertEqual(live["respawns"], 1)
        self.t.record_end("hotkey", 200)
        d = self.store.match_full(1)["details"]
        self.assertEqual(d["respawns_log"], [{"t": 130, "here": "fob"}])
        self.assertEqual(d["combat"]["respawns"], 1)

    def test_one_misread_start_is_not_a_respawn(self):
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(120, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(130, [L("$10,536", 1695, 27), L("$58,233", 1814, 27)])  # 3 read as 8
            self.t._note_hud(140, [L("$10,700", 1695, 27), L("$53,397", 1814, 27)])
        self.assertEqual(self.t.live(141)["respawns"], 0)

    def test_a_respawn_right_after_going_down_counts_on_one_read(self):
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(120, [L("$10,536", 1695, 27), L("$53,233", 1814, 27)])
            self.t._note_hud(125, [L("GIVE UP", 796, 967), L("$53,233", 1814, 27)])
            self.t._note_hud(130, [L("-$13,292", 1695, 27), L("$39,941", 1814, 27)])
            self.t._note_hud(140, [L("$40,524", 1814, 27)])
        self.assertEqual(self.t.hud["respawns_log"], [{"t": 130, "here": None}])

    def test_being_revived_is_logged_once(self):
        self.t.hud["here"] = "hotzone"
        self.t._note_feeds(110, [L("WAITED TO BE REVIVED +$100"), L("BE REVIVED +50 XP")], [])
        self.t._note_feeds(113, [L("WAITED TO BE REVIVED +$100"), L("BE REVIVED +50 XP")], [])
        self.t._note_feeds(140, [L("REVIVED PLAYER ASSIST +50 XP")], [])  # you helped revive someone
        self.t._note_feeds(200, [L("BE REVIVED +50 XP")], [])
        live = self.t.live(201)
        self.assertEqual(live["revives"], 2)
        self.assertEqual(self.t.hud["revives_log"], [{"t": 110, "here": "hotzone"}, {"t": 200, "here": "hotzone"}])

    def test_waiting_for_the_world_to_load_starts_a_new_round(self):
        # 2026-10-26: shown in the safe zone at the start of every round, balance alone in the corner
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("-$12,220", 1695, 27), L("$26,792", 1814, 27)])
            self.t._note_hud(120, [L("$9,765", 1695, 27), L("$48,777", 1814, 27)])
            self.t._note_hud(130, [L("$9,765", 1695, 27), L("$48,777", 1814, 27)])
            self.t._note_hud(150, [L("SAFE ZONE", 899, 79), L("WAITING FOR WORLD TO LOAD", 765, 118),
                                   L("$48,777", 1811, 27)])
            mid = self.t.store.last_match(self.t.session)
            self.assertIsNotNone(mid)
            self.t._note_hud(160, [L("-$500", 1695, 27), L("$48,277", 1814, 27)])
            self.t._note_hud(170, [L("-$500", 1695, 27), L("$48,277", 1814, 27)])
        m = next(x for x in self.t.store.dump()["matches"] if x["id"] == mid)
        self.assertEqual((m["source"], m["ended_at"], m["cash_earned"] - m["cash_spent"]), ("hud", 130, 9765))
        live = self.t.live(171)
        self.assertEqual((live["net"], live["start_balance"]), (-500, 48777))
        self.assertEqual(self.t.store.last_match(self.t.session), mid)  # once, not on every loading read

    def test_the_server_browser_means_you_left_the_round(self):
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$9,765", 1695, 27), L("$48,777", 1814, 27)])
            self.t._note_hud(120, [L("// SERVER BROWSER", 222, 14), L("JOINING SERVER", 1393, 859)])
        self.assertIsNotNone(self.t.store.last_match(self.t.session))

    def test_chat_about_waiting_is_not_a_new_round(self):
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("$9,765", 1695, 27), L("$48,777", 1814, 27)])
            self.t._note_hud(120, [L("[TEAM] Bob: waiting for players to load lol", 55, 97)])
        self.assertIsNone(self.t.store.last_match(self.t.session))

    def test_a_new_starting_balance_means_a_missed_round_end(self):
        # 2026-10-27: the results screen went by unseen and the next round began
        with mock.patch.object(self.t, "_save_sample"), mock.patch.object(self.t, "_roles", return_value={}):
            self.t._note_hud(110, [L("-$12,220", 1695, 27), L("$26,792", 1814, 27)])
            self.t._note_hud(120, [L("$9,765", 1695, 27), L("$48,777", 1814, 27)])
            self.t._note_hud(130, [L("$9,765", 1695, 27), L("$48,777", 1814, 27)])
            # the end bonus landed after the last read, so the next round starts from $57,900
            self.t._note_hud(500, [L("-$2,400", 1695, 27), L("$55,500", 1814, 27)])  # one odd read: wait
            self.assertIsNone(self.t.store.last_match(self.t.session))
            self.t._note_hud(510, [L("-$2,000", 1695, 27), L("$55,900", 1814, 27)])
        mid = self.t.store.last_match(self.t.session)
        self.assertIsNotNone(mid)
        m = next(x for x in self.t.store.dump()["matches"] if x["id"] == mid)
        self.assertEqual((m["source"], m["cash_earned"], m["cash_spent"]), ("hud", 21985, 12220))
        live = self.t.live(511)
        self.assertEqual((live["net"], live["start_balance"]), (-2000, 57900))
        self.assertFalse(live["between"])

    def test_lobby_is_not_a_match_until_the_hud_shows_one(self):
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("PLAY"), L("LOADOUTS")])
            self.assertTrue(self.t.live(111)["between"])
            self.t._note_hud(150, [L("$1,200", 1706, 27), L("$45,000", 1818, 27)])
        live = self.t.live(151)
        self.assertFalse(live["between"])
        self.assertEqual(live["started"], 150)

    def test_menu_balance_is_not_round_profit(self):
        with mock.patch.object(self.t, "_save_sample"):  # server browser, 2026-10-26 sample
            self.t._note_hud(110, [L("// SERVER BROWSER", 222, 14), L("$117,732", 1583, 24), L("62", 1836, 26)])
        live = self.t.live(111)
        self.assertTrue(live["between"])
        self.assertIsNone(live["net"])

    def test_downs_vendor_and_combat_end_up_on_the_match(self):
        with mock.patch.object(self.t, "_save_sample"):
            self.t._note_hud(110, [L("$20,000", 1817, 27)])
            self.t._note_hud(120, [L("// Garage Vendor"), L("WEAPONS"), L("AMMO"), L("INDIVIDUAL FIRST AID KIT", 796, 356),
                                   L("MEDICAL // OUICK SLOT", 796, 378), L("-$800", 1709, 27), L("$19,200", 1817, 27)])
            self.t._note_hud(130, [L("GIVE UP", 796, 967)])
            self.t._note_hud(140, [L("GIVE UP", 796, 967)])  # same down
            self.t._note_hud(150, [L("nothing")])
            self.t._note_hud(160, [L("GIVE UP", 796, 967)])
        self.t._note_feeds(165, [], [L("Dogtag Wrenx")])
        self.t.record_end("hotkey", 200, lines=[L("BAKURANI 45'15.0\"N 122'25.08\"W"), L("VALKYRA"), L("WINS")])
        m = self.store.dump()["matches"][0]
        self.assertEqual(m["details"]["map"], "Bakurani")
        self.assertEqual(m["details"]["combat"], {"kills": 1, "deaths": 0, "downs": 2, "revives": 0, "respawns": 0})
        self.assertEqual(m["details"]["vendor"], {"Individual First Aid Kit": 800})
        full = self.store.match_full(m["id"])["details"]
        self.assertEqual(full["kill_log"][0]["other"], "Wrenx")
        self.assertNotIn("feed", m["details"])


if __name__ == "__main__":
    unittest.main()
