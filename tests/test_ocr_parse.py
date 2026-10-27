import unittest

from wdt.ocr import looks_like_end_screen, parse


class ParseTests(unittest.TestCase):
    def test_reads_xp_and_cash(self):
        r = parse([{"text": "MATCH COMPLETE"}, {"text": "Medic XP +4,250"},
                   {"text": "Cash earned $12,400"}, {"text": "Loadout cost -$3, 100"}])
        self.assertEqual(r["xp"], 4250)
        self.assertEqual(r["cash_earned"], 12400)
        self.assertEqual(r["cash_spent"], 3100)

    def test_xp_after_number_and_largest_wins(self):
        r = parse(["+350 XP", "Total 2,980 XP", "Kill +100XP"])
        self.assertEqual(r["xp"], 2980)
        self.assertEqual(sorted(r["candidates"]["xp"]), [100, 350, 2980])

    def test_spend_words_mark_cash_as_spent(self):
        r = parse(["Vehicle repair $1,500", "Ammo resupply cost $200", "Reward $9,000"])
        self.assertEqual(r["cash_spent"], 1700)
        self.assertEqual(r["cash_earned"], 9000)

    def test_end_screen_needs_xp_and_results_words(self):
        self.assertTrue(looks_like_end_screen(["VICTORY", "1st place", "+2,450 XP"]))
        self.assertTrue(looks_like_end_screen(["Match summary", "Medic XP 1,200"]))
        # main menu / HUD: shows XP and cash but no results wording
        self.assertFalse(looks_like_end_screen(["PLAY", "LOADOUT", "Level 58  162,446 XP", "$45,551"]))
        # results wording without any XP figure
        self.assertTrue(looks_like_end_screen(["DEFEAT", "Dogtag 12 3"]))  # the winner banner alone counts
        self.assertFalse(looks_like_end_screen(["Dogtag 12 3", "[ALL] x: defeat"]))

    def test_nothing_found(self):
        r = parse(["SCOREBOARD", "Dogtag 12 3"])
        self.assertEqual((r["xp"], r["cash_earned"], r["cash_spent"]), (None, None, None))


if __name__ == "__main__":
    unittest.main()
