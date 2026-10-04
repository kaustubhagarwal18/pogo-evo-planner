import json
import unittest
from pathlib import Path

from pogo_evo_planner.dashboard import build_view, render_html
from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.inventory import Inventory, Specimen
from pogo_evo_planner.planner import plan

ROOT = Path(__file__).resolve().parent.parent
SPECIES = load_species(ROOT / "pogo_evo_planner/data/species_sample.json")
TIERS = json.loads((ROOT / "pogo_evo_planner/data/rarity_tiers.json").read_text())


class DashboardTest(unittest.TestCase):
    def test_view_has_sprites_and_new_dex_flags(self):
        # Dragonair is registered; Dragonite is not, so only the final step adds a Pokédex entry
        inv = Inventory(150, {"DRATINI": 0}, {}, {"DRAGONAIR"}, [Specimen("DRATINI")])
        view = build_view(plan(SPECIES, TIERS, inv), inv, SPECIES)
        steps = view["families"][0]["paths"][0]["steps"]
        self.assertEqual([(s["to"], s["new_dex"]) for s in steps], [("DRAGONAIR", False), ("DRAGONITE", True)])
        self.assertEqual(view["dex"], {"DRATINI": 147, "DRAGONAIR": 148, "DRAGONITE": 149})

    def test_render_embeds_view_safely(self):
        inv = Inventory(0, {}, {}, set(), [Specimen("DRATINI", label="</script>")])
        page = render_html(build_view(plan(SPECIES, TIERS, inv), inv, SPECIES))
        self.assertNotIn("/*DATA*/null", page)
        self.assertNotIn('"</script>"', page)


if __name__ == "__main__":
    unittest.main()
