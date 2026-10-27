import unittest

from wdt.ocr import details, mentions


def L(text, x=0, y=0):
    return {"text": text, "x": x, "y": y}


class DetailsTests(unittest.TestCase):
    def test_inline_values(self):
        d = details([L("VICTORY"), L("Bravo Team wins"), L("100 - 74"),
                     L("Match Time 47:12"), L("Kills: 6"), L("3 Deaths"), L("+3,420 XP")])
        self.assertEqual(d["result"], "victory")
        self.assertEqual(d["winner"], "Bravo Team")
        self.assertEqual(d["score"], "100-74")
        self.assertEqual(d["duration_s"], 47 * 60 + 12)
        self.assertEqual(d["stats"], {"kills": 6, "deaths": 3})

    def test_values_in_a_column_next_to_labels(self):
        d = details([L("KILLS", 100, 500), L("12", 400, 504), L("REVIVES", 100, 540),
                     L("4", 400, 541), L("DURATION", 100, 580), L("1:05:30", 400, 579),
                     L("Winner", 900, 100), L("Team Hazard", 900, 140)])
        self.assertEqual(d["stats"], {"kills": 12, "revives": 4})
        self.assertEqual(d["duration_s"], 3930)
        self.assertEqual(d["winner"], "Team Hazard")

    def test_defeat_and_minutes_text(self):
        d = details([L("DEFEAT"), L("Match length 52m")])
        self.assertEqual(d["result"], "defeat")
        self.assertEqual(d["duration_s"], 52 * 60)
        self.assertIsNone(d["winner"])

    def test_cash_lines_are_not_stats(self):
        self.assertEqual(details([L("-$2,000 repairs"), L("Repairs 3")])["stats"], {"repairs": 3})

    def test_clock_like_text_is_not_a_score(self):
        self.assertIsNone(details([L("12:34"), L("100 XP")])["score"])

    def test_mentions_roles_and_owned_items(self):
        m = mentions([L("MEDIC loadout"), L("Primary: MP5"), L("Kobra Reflex sight")],
                     ["MP5", "Kobra Reflex", "Judge", "C4 Charge"])
        self.assertEqual(m, {"roles": ["Medic"], "items": ["Kobra Reflex", "MP5"]})


if __name__ == "__main__":
    unittest.main()
