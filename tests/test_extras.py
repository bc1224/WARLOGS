import os
import tempfile
import unittest
from unittest import mock

from wdt import sources
from wdt.ocr import xp_sources
from wdt.store import Store


def L(text, x=0, y=0):
    return {"text": text, "x": x, "y": y}


class MetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_meta_round_trip_and_dump(self):
        self.assertIsNone(self.store.get_meta("settings"))
        self.store.set_meta("settings", {"daily_xp_goal": 10000})
        self.assertEqual(self.store.get_meta("settings"), {"daily_xp_goal": 10000})
        self.assertEqual(self.store.dump()["meta"]["settings"]["daily_xp_goal"], 10000)

    def test_settings_keep_only_known_positive_numbers(self):
        self.store.save_settings({"daily_xp_goal": "12000", "weekly_xp_goal": -5, "evil": 1})
        self.assertEqual(self.store.get_meta("settings"), {"daily_xp_goal": 12000, "weekly_xp_goal": None})

    def test_pull_api_also_stores_leaderboard_neighbours_for_a_new_sync(self):
        stats = {"syncedAt": "s1", "careerXp": 5, "rank": 855}
        board = {"total": 1190, "players": [{"rank": 854, "name": "A"}, {"rank": 855, "name": "me"}]}

        def get(url, timeout=15):
            if "leaderboard" in url:
                self.assertIn("offset=851", url)
                return board
            return {"unlocks": {"items": []}, "syncedAt": "s1"}
        with mock.patch.object(sources, "fetch_stats", return_value=stats), \
             mock.patch.object(sources, "_get", side_effect=get):
            sources.pull_api(self.store, "1", 10)
        self.assertEqual(self.store.get_meta("leaderboard")["players"][0]["name"], "A")

    def test_pull_status(self):
        with mock.patch.object(sources, "_get", return_value={"overall": "ok", "servers": {"players": 5}}):
            sources.pull_status(self.store)
        self.assertEqual(self.store.get_meta("server_status")["overall"], "ok")


class XpSourceTests(unittest.TestCase):
    def test_inline_rows_skip_totals(self):
        rows = xp_sources([L("Zone Capture +1,200 XP"), L("Kills  +800 XP"), L("Kills $3,000"),
                           L("Total XP 2,000"), L("VICTORY")])
        self.assertEqual(rows, [{"label": "Zone Capture", "xp": 1200, "cash": None},
                                {"label": "Kills", "xp": 800, "cash": 3000}])

    def test_label_and_value_in_columns(self):
        rows = xp_sources([L("Revives", 100, 300), L("+450 XP", 500, 302),
                           L("Building", 100, 340), L("$1,500", 500, 338)])
        self.assertEqual(rows, [{"label": "Revives", "xp": 450, "cash": None},
                                {"label": "Building", "xp": None, "cash": 1500}])

    def test_costs_are_negative_cash(self):
        rows = xp_sources([L("Repairs -$2,000")])
        self.assertEqual(rows, [{"label": "Repairs", "xp": None, "cash": -2000}])


if __name__ == "__main__":
    unittest.main()
