import os
import tempfile
import unittest

from wdt.matcher import EndScreenMatcher, distance
from wdt.sources import read_role_levels
from wdt.store import Store

END = bytes([200] * 50 + [20] * 50)
GAME = bytes([90] * 100)


class MatcherTests(unittest.TestCase):
    def test_needs_consecutive_hits_then_cools_down(self):
        m = EndScreenMatcher([END], threshold=10, confirm=2, cooldown=60)
        self.assertFalse(m.feed(END, 0))
        self.assertTrue(m.feed(END, 2))
        self.assertFalse(m.feed(END, 4))       # same end screen still showing
        self.assertFalse(m.feed(GAME, 30))
        self.assertFalse(m.feed(END, 70))
        self.assertTrue(m.feed(END, 72))       # next match

    def test_miss_resets_streak(self):
        m = EndScreenMatcher([END], threshold=10, confirm=2)
        m.feed(END, 0)
        m.feed(GAME, 2)
        self.assertFalse(m.feed(END, 4))

    def test_no_templates_never_matches(self):
        self.assertFalse(EndScreenMatcher([]).feed(END, 0))

    def test_small_changes_still_match(self):
        noisy = bytes(min(255, b + 5) for b in END)
        self.assertLess(distance(END, noisy), 10)

    def test_manual_end_suppresses_auto(self):
        m = EndScreenMatcher([END], threshold=10, confirm=1, cooldown=60)
        m.mark_end(0)
        self.assertFalse(m.feed(END, 5))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.s = Store(os.path.join(self.dir.name, "t.db"))

    def tearDown(self):
        self.s.db.close()
        self.dir.cleanup()

    def test_match_roundtrip_and_edit(self):
        sid = self.s.start_session(100)
        mid = self.s.add_match(sid, 100, 900, "auto", parsed={"xp": 500, "cash_earned": 9000})
        self.s.update_match(mid, {"xp": 650, "reviewed": 1, "session_id": 99})
        m = self.s.dump()["matches"][0]
        self.assertEqual((m["xp"], m["cash_earned"], m["reviewed"], m["session_id"]),
                         (650, 9000, 1, sid))
        self.assertEqual(self.s.last_match_end(sid), 900)

    def test_misread_team_names_are_cleared_on_open(self):
        sid = self.s.start_session(100)
        for team in ("MATE", "mates", "Viper"):
            self.s.add_match(sid, 100, 900, "auto", details={"hud": {"reads": 3, "team": team}})
        self.s.db.close()
        self.s = Store(os.path.join(self.dir.name, "t.db"))
        teams = [m["details"]["hud"]["team"] for m in self.s.dump()["matches"]]
        self.assertEqual(sorted(teams, key=str), sorted([None, None, "Viper"], key=str))

    def test_snapshot_dedupes_by_sync_time(self):
        self.assertTrue(self.s.add_snapshot({"syncedAt": "a", "careerXp": 1}))
        self.assertFalse(self.s.add_snapshot({"syncedAt": "a", "careerXp": 1}))
        self.assertTrue(self.s.add_snapshot({"syncedAt": "b", "careerXp": 2}))

    def test_crashed_session_closed_at_last_match(self):
        sid = self.s.start_session(100)
        self.s.add_match(sid, 100, 500, "auto")
        self.s.close_open_sessions()
        self.assertEqual(self.s.dump()["sessions"][0]["ended_at"], 500)


class RoleSaveTests(unittest.TestCase):
    def test_reads_utf16_save(self):
        text = '{\r\n\t"_x":\r\n\t{\r\n\t\t"(TagName=\\"Meta.Role.Wardog\\")": 58,\r\n' \
               '\t\t"(TagName=\\"Meta.Role.Medic\\")": 14\r\n\t}\r\n}'
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sav") as f:
            f.write(text.encode("utf-16-le"))
        try:
            self.assertEqual(read_role_levels(f.name), {"Wardog": 58, "Medic": 14})
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
