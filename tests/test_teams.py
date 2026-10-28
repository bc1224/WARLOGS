import unittest

from wdt import teams


def L(text, x):
    return {"text": text, "x": x, "y": 10}


class TeamScoreTests(unittest.TestCase):
    def test_strip_puts_cells_side_by_side(self):
        a = (b"\x01\x02\x03\xff" * 4, 2, 2)
        b = (b"\x09\x09\x09\xff" * 3, 3, 1)
        data, w, h, starts = teams.strip([a, b])
        self.assertEqual((w, h, starts), (2 + 3 + 3 * teams.GAP, 22, [teams.GAP, 2 * teams.GAP + 2]))
        px = lambda x, y: data[(y * w + x) * 4:(y * w + x) * 4 + 3]
        self.assertEqual(px(teams.GAP, 10), b"\x01\x02\x03")
        self.assertEqual(px(starts[1] + 2, 10), b"\x09\x09\x09")
        self.assertEqual(px(starts[1], 11), b"\x14\x14\x14")  # the shorter crop leaves background

    def test_reads_cells_by_position_with_ocr_lookalikes(self):
        starts = [60, 285, 500, 715, 835, 965]
        # 'ooo 028' reads as one line across two cells; 'cc' is the blue count 00
        r = teams.read([L("ooo 028", 60), L("075", 500), L("cc", 715), L("29", 965)], starts)
        self.assertEqual(r, {"s": [0, 28, 75], "n": [0, None, 29]})
        # junk, a 2-digit score and a 3-digit count are dropped
        r = teams.read([L("J", 20), L("63", 285), L("079", 500), L("380", 835)], starts)
        self.assertEqual(r, {"s": [None, None, 79], "n": [None, None, None]})

    def test_final_score_skips_misreads(self):
        reads = [{"s": [0, s, g], "n": [0, 38, n]} for s, g, n in
                 ((4, 10, 52), (15, 50, 40), (83, 52, 39), (21, 60, None), (40, 89, 39), (44, 100, 40))]
        out = teams.summary(reads)
        self.assertEqual(out["scores"], {"blue": 0, "red": 44, "green": 100})  # the 83 spike is ignored
        self.assertEqual(out["players"], {"blue": 0, "red": 38, "green": 40})
        self.assertEqual(out["mode"], "2 teams")

    def test_three_teams_and_nothing_read(self):
        reads = [{"s": [12, 30, 40], "n": [33, 33, 34]}, {"s": [20, 35, 41], "n": [None, 33, 33]}]
        self.assertEqual(teams.summary(reads)["mode"], "3 teams")
        self.assertIsNone(teams.summary([{"s": [None] * 3, "n": [None] * 3}]))
        self.assertIsNone(teams.summary([]))
        # 50 v 50: a few players waiting on blue for a spot, and one misread blue score
        wait = [{"s": [0, 20, 30], "n": [4, 50, 50]}, {"s": [8, 25, 33], "n": [3, 50, 49]},
                {"s": [0, 30, 40], "n": [6, 48, 50]}]
        self.assertEqual(teams.summary(wait)["mode"], "2 teams")
        self.assertEqual(teams.summary([dict(r, n=[30, 33, 33]) for r in wait])["mode"], "3 teams")
        self.assertIsNone(teams.summary([{"s": [0, 5, 7], "n": [None] * 3}]))  # one read could be a misread


if __name__ == "__main__":
    unittest.main()
