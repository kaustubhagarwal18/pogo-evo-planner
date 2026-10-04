import itertools
import json
import random
import unittest
from collections import Counter
from pathlib import Path

from rarecandy.gamemaster import load_species, parse_game_master
from rarecandy.inventory import Inventory, Specimen
from rarecandy.planner import Settings, cost_per_candy, enumerate_paths, plan

ROOT = Path(__file__).resolve().parent.parent
SPECIES = load_species(ROOT / "rarecandy/data/species_sample.json")
TIERS = json.loads((ROOT / "rarecandy/data/rarity_tiers.json").read_text())


def brute_force(inv: Inventory, settings: Settings) -> float:
    """Try every combination of paths for every specimen."""
    per_spec = [enumerate_paths(s, SPECIES, settings, []) for s in inv.pokemon]
    best = 0.0
    for combo in itertools.product(*per_spec):
        candy, items, targets = Counter(), Counter(), {}
        value = 0.0
        for p in combo:
            fam = SPECIES[p.specimen.species]["family"]
            candy[fam] += p.candy
            items += p.items
            value += p.candy * cost_per_candy(fam, SPECIES, TIERS)[1]
            for t in p.targets:
                if t not in inv.pokedex:
                    targets[t] = True
        rare = sum(max(0, c - inv.candy.get(f, 0)) for f, c in candy.items())
        if rare > inv.rare_candy or any(inv.items.get(k, 0) < v for k, v in items.items()):
            continue
        best = max(best, value + settings.dex_bonus * len(targets))
    return best


class PlannerTests(unittest.TestCase):
    def test_matches_brute_force_on_random_inventories(self):
        rng = random.Random(7)
        pool = ["DRATINI", "DRAGONAIR", "BELDUM", "FEEBAS", "EEVEE", "ONIX", "SCYTHER",
                "KIRLIA", "MAGIKARP", "GASTLY", "PIDGEY", "CHANSEY"]
        for _ in range(60):
            mons = [Specimen(s, None, f"#{i}") for i, s in enumerate(rng.choices(pool, k=rng.randint(1, 4)))]
            fams = {SPECIES[m.species]["family"] for m in mons}
            inv = Inventory(
                rare_candy=rng.choice([0, 10, 40, 75, 120, 200]),
                candy={f: rng.choice([0, 12, 25, 50, 90, 130, 380]) for f in fams},
                items={"ITEM_METAL_COAT": rng.randint(0, 1), "ITEM_SINNOH_STONE": rng.randint(0, 1)},
                pokedex=set(rng.sample(sorted(SPECIES), 15)),
                pokemon=mons,
            )
            settings = Settings(dex_bonus=rng.choice([0, 20, 500]))
            got = plan(SPECIES, TIERS, inv, settings)
            self.assertAlmostEqual(got.total_value, brute_force(inv, settings), places=6, msg=str(inv))
            self.assertLessEqual(got.rare_candy_used, inv.rare_candy)

    def test_prefers_high_effort_family(self):
        # 70 rare candy can finish Feebas (20 km buddy) or Magikarp (1 km) but not both.
        inv = Inventory(70, {"FEEBAS": 30, "MAGIKARP": 330}, {}, set(),
                        [Specimen("FEEBAS"), Specimen("MAGIKARP")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual([f.family for f in p.families if f.rare_candy], ["FEEBAS"])

    def test_partial_top_up_is_not_spent(self):
        inv = Inventory(10, {"DRATINI": 0}, {}, set(), [Specimen("DRATINI")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual(p.rare_candy_used, 0)
        self.assertEqual(p.families, [])

    def test_shared_item_used_once(self):
        inv = Inventory(0, {"ONIX": 50, "SCYTHER": 50}, {"ITEM_METAL_COAT": 1}, set(),
                        [Specimen("ONIX"), Specimen("SCYTHER")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual(len(p.families), 1)

    def test_trade_evolutions_suggested_not_paid(self):
        inv = Inventory(100, {"GASTLY": 0}, {}, set(), [Specimen("HAUNTER")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual(p.rare_candy_used, 0)
        self.assertEqual([s.to for _, s in p.trade_instead], ["GENGAR"])

    def test_parse_raw_game_master(self):
        raw = [
            {"templateId": "V0147_POKEMON_DRATINI", "data": {"pokemonSettings": {
                "pokemonId": "DRATINI", "familyId": "FAMILY_DRATINI", "kmBuddyDistance": 5,
                "evolutionBranch": [{"evolution": "DRAGONAIR", "candyCost": 25}]}}},
            {"templateId": "V0094_POKEMON_HAUNTER", "data": {"pokemonSettings": {
                "pokemonId": "HAUNTER", "familyId": "FAMILY_GASTLY", "kmBuddyDistance": 3,
                "candyToEvolve": 100,
                "evolutionBranch": [{"evolution": "GENGAR", "noCandyCostViaTrade": True},
                                    {"temporaryEvolution": "TEMP_EVOLUTION_MEGA"}]}}},
            {"templateId": "V0019_POKEMON_RATTATA_ALOLA", "data": {"pokemonSettings": {
                "pokemonId": "RATTATA", "form": "RATTATA_ALOLA", "familyId": "FAMILY_RATTATA",
                "evolutionBranch": [{"evolution": "RATICATE", "form": "RATICATE_ALOLA", "candyCost": 25}]}}},
            {"templateId": "ITEM_POTION", "data": {}},
        ]
        sp = parse_game_master(raw)
        self.assertEqual(sp["DRATINI"]["evolutions"][0], {"to": "DRAGONAIR", "candy": 25, "item": None,
                                                         "trade_free": False, "notes": []})
        self.assertEqual(sp["HAUNTER"]["family"], "GASTLY")
        self.assertEqual(len(sp["HAUNTER"]["evolutions"]), 1)
        self.assertTrue(sp["HAUNTER"]["evolutions"][0]["trade_free"])
        self.assertEqual(sp["HAUNTER"]["evolutions"][0]["candy"], 100)
        self.assertEqual(sp["RATTATA_ALOLA"]["evolutions"][0]["to"], "RATICATE_ALOLA")


if __name__ == "__main__":
    unittest.main()
