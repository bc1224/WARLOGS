import unittest

from wdt.catalog import page_text, parse_item


# trimmed from real wardogstracker.gg pages (2026-10-26)
GALIL = ("Home | › | Database | › | Weapons | › | Galil | Galil | Assault Rifle · 5.56x45mm · Infantry role · $2,000 · LVL 10 Assault | "
         "Details | Unlocks at | LVL 10 Assault | Unlock cost | $35,000 | Unlocked by | 710 of 1,232 players (58%) | Role | Infantry |")
DEFIB = ("Home | › | Database | › | Equipment | › | Defibrillator | Defibrillator | Medical | Changelog | 2026-10-17 | "
         "Unlock level | 11 | → | 22 | ▲ | Unlock cost | $50,000 | → | $110,000 | ▲ | Stats | Price | $1,600 | "
         "Level requirement | LVL 22 Medic | Unlock cost | $110,000 | Weight | 2.6 kg | Role | Medic | Unlocked by | 172 | of | 1,232 | ( | 14 | %) |")
AK = "Home | › | Database | › | Weapons | › | AK74 | AK74 | Assault Rifle · 5.45x39mm · Infantry role · $800 · Starter |"


class CatalogParseTests(unittest.TestCase):
    def test_weapon_page(self):
        self.assertEqual(parse_item(GALIL), {"name": "Galil", "category": "weapons", "url": "", "level": 10, "role": "assault",
                                             "cost": 35000, "price": 2000, "owned_pct": 58})

    def test_current_values_win_over_the_changelog(self):
        it = parse_item(DEFIB)
        self.assertEqual((it["level"], it["role"], it["cost"], it["price"], it["owned_pct"]), (22, "medic", 110000, 1600, 14))

    def test_starter_items_have_no_unlock(self):
        self.assertIsNone(parse_item(AK)["level"])

    def test_page_text_joins_cells(self):
        self.assertEqual(page_text("<div><b>Unlock cost</b><span>$35,000</span></div><script>x</script>"), " | Unlock cost | $35,000 | ")


if __name__ == "__main__":
    unittest.main()
