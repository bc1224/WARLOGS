import os
import tempfile
import unittest
from unittest import mock

from wdt import sources
from wdt.store import Store


def item(gid, name, level=1, group="gear"):
    return {"gameId": gid, "level": level,
            "item": {"name": name, "group": group, "url": "u", "imageUrl": "i"}}


def unlocks_body(items, synced="2026-10-26T20:00:00Z", value=1000):
    return {"syncedAt": synced, "unlocks": {"items": items}, "unlockValue": {"cash": value}}


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_first_sync_is_baseline_then_new_items_are_reported(self):
        first = sources.parse_unlocks(unlocks_body([item("A", "Hammer"), item("B", "C4")]))
        self.assertEqual(self.store.sync_unlocks(first, "s1", 100), [])
        later = sources.parse_unlocks(unlocks_body(
            [item("A", "Hammer"), item("B", "C4", level=2), item("C", "Scope")]))
        self.assertEqual(self.store.sync_unlocks(later, "s2", 200), ["C4 (level 2)", "Scope"])
        rows = {u["game_id"]: u for u in self.store.dump()["unlocks"]}
        self.assertEqual(rows["A"]["baseline"], 1)
        self.assertEqual(rows["C"]["baseline"], 0)
        self.assertEqual(rows["C"]["first_seen"], 200)
        self.assertEqual(rows["B"]["level"], 2)
        self.assertEqual(rows["B"]["level_seen"], 200)

    def test_unmatched_item_keeps_game_id_as_name(self):
        parsed = sources.parse_unlocks(unlocks_body([{"gameId": "Id.Item.STANAGMagazine",
                                                      "level": 1, "item": None}]))
        self.assertEqual(parsed["items"][0]["name"], "STANAGMagazine")

    def test_pull_api_fetches_unlocks_only_for_a_new_sync(self):
        stats = {"syncedAt": "s1", "careerXp": 5}
        body = unlocks_body([item("A", "Hammer")], synced="s1", value=750)
        with mock.patch.object(sources, "fetch_stats", return_value=stats), \
             mock.patch.object(sources, "_get", return_value=body) as get:
            self.assertEqual(sources.pull_api(self.store, "1", 10), (True, []))
            self.assertEqual(sources.pull_api(self.store, "1", 20), (False, []))
        self.assertEqual(get.call_count, 1)
        snap = self.store.dump()["snapshots"][-1]["data"]
        self.assertEqual(snap["unlockValue"], 750)


if __name__ == "__main__":
    unittest.main()
