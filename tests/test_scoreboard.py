import json
import os
import unittest
import zlib

from wdt.scoreboard import Gray, is_scoreboard, read_board

HERE = os.path.join(os.path.dirname(__file__), "fixtures")
# real captures show other players' names, so they stay out of the public repo
HAVE = os.path.isfile(os.path.join(HERE, "expect.json"))
NAMES = json.load(open(os.path.join(HERE, "expect.json"), encoding="utf-8")) if HAVE else {}
# a real in-match Scoreboard tab (Global view, rows 70 to 86, 2026-10-26), scaled to 1920x1080
TRUE = [(13381, 3, 2, 3, 3), (12884, 0, 0, 2, 0), (12833, 1, 0, 7, 4), (12688, 1, 0, 3, 2), (10833, 1, 1, 6, 2),
        (9589, 0, 3, 3, 0), (9346, 1, 2, 7, 0), (9216, 0, 4, 2, 1), (8163, 1, 6, 2, 0), (7598, 0, 1, 3, 0),
        (7485, 0, 1, 3, 1), (7129, 0, 1, 11, 0), (6095, 0, 0, 0, 0), (5850, 0, 1, 5, 2), (3470, 0, 0, 8, 0),
        (3450, 1, 0, 3, 0), (3390, 0, 0, 4, 0)]


def load(name="scoreboard"):
    with open(os.path.join(HERE, name + ".json"), encoding="utf-8") as f:
        d = json.load(f)
    x0, y0, x1, y1 = d["box"]
    with open(os.path.join(HERE, name + ".gray.z"), "rb") as f:
        px = Gray(zlib.decompress(f.read()), x1 - x0, y1 - y0, 1, x0, y0)
    return d["lines"], px


@unittest.skipUnless(HAVE, "scoreboard captures are kept local")
class ScoreboardTests(unittest.TestCase):
    def setUp(self):
        self.lines, self.px = load()

    def test_detects_the_scoreboard_tab(self):
        self.assertTrue(is_scoreboard(self.lines))
        self.assertFalse(is_scoreboard([{"text": "INVENTORY", "x": 1, "y": 1}, {"text": "SCOREBOARD", "x": 2, "y": 1}]))

    def test_rows_ranks_and_your_place(self):
        b = read_board(self.lines, self.px, NAMES["me"], {})
        self.assertEqual((b["view"], b["players"]), ("Global", 99))
        self.assertEqual([r["rank"] for r in b["rows"]], list(range(70, 87)))
        me = b["me"]
        self.assertEqual((me["rank"], me["name"], me["kills"], me["assists"], me["deaths"], me["revives"]), (72, NAMES["me"], 1, 0, 7, 4))
        self.assertEqual(b["above"]["name"], NAMES["above"])

    def test_most_stat_cells_read_right(self):
        rows = read_board(self.lines, self.px, "", {})["rows"]
        got = [(r["kills"], r["assists"], r["deaths"], r["revives"]) for r in rows]
        ok = sum(a == b for g, t in zip(got, TRUE) for a, b in zip(g, t[1:]))
        self.assertGreaterEqual(ok, 62)  # of 68; this sample is a blurry upscale, live reads are sharper

    def test_name_not_on_screen(self):
        self.assertIsNone(read_board(self.lines, self.px, NAMES["absent"], {})["me"])

    def test_your_highlighted_row(self):
        # a 4K screen read at 1920 wide (2026-10-26); your row has a gold outline
        lines, px = load("scoreboard_me")
        b = read_board(lines, px, NAMES["me_hl"], {})
        me = b["me"]
        self.assertEqual((me["rank"], me["cash"], me["kills"], me["assists"], me["deaths"], me["revives"]),
                         (13, 71410, 2, 12, 2, 0))
        self.assertEqual((b["above"]["name"], b["above"]["cash"]), (NAMES["above_hl"], 71744))


if __name__ == "__main__":
    unittest.main()
