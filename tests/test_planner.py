import itertools
import json
import random
import unittest
from collections import Counter
from pathlib import Path

from pogo_evo_planner.gamemaster import load_species, parse_game_master
from pogo_evo_planner.inventory import Inventory, Specimen
from pogo_evo_planner.planner import (
    Settings,
    candy_from_base,
    cost_per_candy,
    enumerate_paths,
    families_missing_candy,
    keep_specimens,
    owned_species,
    plan,
    with_earlier_stages,
    with_forms,
)

ROOT = Path(__file__).resolve().parent.parent
SPECIES = load_species(ROOT / "pogo_evo_planner/data/species_sample.json")
TIERS = json.loads((ROOT / "pogo_evo_planner/data/rarity_tiers.json").read_text())


def brute_force(inv: Inventory, settings: Settings) -> float:
    """Try every combination of paths for the specimens the planner keeps (duplicates dropped)."""
    per_spec = [enumerate_paths(s, SPECIES, settings, []) for s in keep_specimens(inv.pokemon, SPECIES, owned_species(inv, SPECIES))]
    owned = with_earlier_stages({s.species for s in inv.pokemon}, SPECIES)
    from_base = candy_from_base(SPECIES)
    best = 0.0
    for combo in itertools.product(*per_spec):
        candy, items, targets = Counter(), Counter(), {}
        for p in combo:
            candy[SPECIES[p.specimen.species]["family"]] += p.candy
            items += p.items
            for t in p.targets:
                if t not in inv.pokedex and t not in owned:
                    targets[t] = True
        rare = sum(max(0, c - inv.candy.get(f, 0)) for f, c in candy.items())
        if rare > inv.rare_candy or any(inv.items.get(k, 0) < v for k, v in items.items()):
            continue
        # paths into a new species: at most one per species is the first copy, every other path is a repeat
        groups: dict[str, list[int]] = {}
        for i, p in enumerate(combo):
            if p.steps and p.targets[-1] not in owned:
                groups.setdefault(p.targets[-1], []).append(i)
        for pick in itertools.product(*[[None, *idx] for idx in groups.values()]):
            first = {i for i in pick if i is not None}
            value, repeat_candy = 0.0, Counter()
            for i, p in enumerate(combo):
                fam = SPECIES[p.specimen.species]["family"]
                cpc = cost_per_candy(fam, SPECIES, TIERS)[1]
                if i in first:
                    value += from_base[p.targets[-1]] * cpc
                else:
                    value += p.candy * cpc * settings.repeat_value
                    repeat_candy[fam] += p.candy
            # repeats may only use own candy left over after the family's new evolutions
            if any(c > max(0, inv.candy.get(f, 0) - (candy[f] - c)) for f, c in repeat_candy.items()):
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
            settings = Settings(dex_bonus=rng.choice([0, 20, 500]), repeat_value=rng.choice([0.0, 0.1, 1.0]))
            got = plan(SPECIES, TIERS, inv, settings)
            self.assertAlmostEqual(got.total_value, brute_force(inv, settings), places=6, msg=str(inv))
            self.assertLessEqual(got.rare_candy_used, inv.rare_candy)

    def test_prefers_high_effort_family(self):
        # 70 rare candy can finish Feebas (20 km buddy) or Magikarp (1 km) but not both.
        inv = Inventory(70, {"FEEBAS": 30, "MAGIKARP": 330}, {}, set(),
                        [Specimen("FEEBAS"), Specimen("MAGIKARP")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual([f.family for f in p.families if f.rare_candy], ["FEEBAS"])

    def test_evolving_into_owned_species_is_a_discounted_repeat(self):
        cpc = cost_per_candy("DRATINI", SPECIES, TIERS)[1]
        settings = Settings(dex_bonus=20, repeat_value=0.1)
        new = plan(SPECIES, TIERS, Inventory(100, {"DRATINI": 0}, {}, set(), [Specimen("DRAGONAIR")]), settings)
        # a new Dragonite is worth the candy from Dratini (25 + 100), whatever stage it is evolved from
        self.assertAlmostEqual(new.total_value, 125 * cpc + 20)
        # already owning a Dragonite: the evolution is a repeat, registers nothing new,
        # and Rare Candy can't pay for it...
        rep_inv = Inventory(100, {"DRATINI": 0}, {}, set(), [Specimen("DRAGONAIR"), Specimen("DRAGONITE")])
        self.assertEqual(plan(SPECIES, TIERS, rep_inv, settings).total_value, 0)
        # ...but your own candy can
        rep_inv.candy["DRATINI"] = 100
        self.assertAlmostEqual(plan(SPECIES, TIERS, rep_inv, settings).total_value, 100 * cpc * 0.1)

    def test_rare_candy_prefers_a_new_species_over_a_repeat(self):
        # 100 Rare Candy finishes one evolution. Feebas (20 km buddy) is worth more per candy,
        # but Milotic is already owned while Dragonite is not.
        inv = Inventory(100, {"DRATINI": 0, "FEEBAS": 0}, {}, set(),
                        [Specimen("FEEBAS"), Specimen("MILOTIC"), Specimen("DRAGONAIR")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual([f.family for f in p.families], ["DRATINI"])
        # even with no repeat discount, Rare Candy never goes to the species you already have
        p = plan(SPECIES, TIERS, inv, Settings(repeat_value=1.0))
        self.assertEqual([f.family for f in p.families if f.rare_candy], ["DRATINI"])

    def test_new_species_reached_from_owned_middle_stage(self):
        # Dragonite is new. Dragonair -> Dragonite (100) and Dratini -> Dragonair -> Dragonite (125)
        # both get it; the cheaper route from the owned Dragonair should win.
        inv = Inventory(125, {"DRATINI": 0}, {}, set(), [Specimen("DRATINI"), Specimen("DRAGONAIR")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual(p.rare_candy_used, 100)
        self.assertEqual([path.specimen.species for f in p.families for path in f.paths], ["DRAGONAIR"])

    def test_rare_candy_never_spent_on_a_species_you_have(self):
        # Milotic is owned. Even when repeats keep their full value, Rare Candy can't pay for one...
        inv = Inventory(100, {"FEEBAS": 0}, {}, set(), [Specimen("FEEBAS"), Specimen("MILOTIC")])
        p = plan(SPECIES, TIERS, inv, Settings(repeat_value=1.0))
        self.assertEqual((p.rare_candy_used, p.families), (0, []))
        # ...but your own Feebas candy can
        inv.candy["FEEBAS"] = 100
        p = plan(SPECIES, TIERS, inv, Settings(repeat_value=1.0))
        self.assertEqual((p.rare_candy_used, [f.family for f in p.families]), (0, ["FEEBAS"]))

    def test_repeat_does_not_push_rare_candy_onto_a_new_evolution(self):
        # Dragonite is new (100 from Dragonair); Dratini -> Dragonair is a repeat (25).
        # With 90 own candy, doing both would need 35 Rare Candy, 25 of them only because of the repeat.
        inv = Inventory(100, {"DRATINI": 90}, {}, set(),
                        [Specimen("DRATINI"), Specimen("DRAGONAIR"), Specimen("DRAGONAIR")])
        p = plan(SPECIES, TIERS, inv, Settings(repeat_value=1.0))
        self.assertEqual(p.rare_candy_used, 10)

    def test_never_caught_beats_a_storage_reading(self):
        # storage claims a Dragonite, but an Evolve button showed it as a silhouette: it's a misread
        inv = Inventory(100, {"DRATINI": 0}, {}, set(), [Specimen("DRAGONAIR"), Specimen("DRAGONITE")],
                        not_caught={"DRAGONITE"})
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual([path.targets for f in p.families for path in f.paths], [["DRAGONITE"]])

    def test_plain_name_covers_its_forms(self):
        species = {
            "TANDEMAUS": {"dex": 924, "family": "TANDEMAUS", "buddy_km": 1, "evolutions": [
                {"to": "MAUSHOLD_FAMILY_OF_FOUR", "candy": 50}, {"to": "MAUSHOLD_FAMILY_OF_THREE", "candy": 50}]},
            "MAUSHOLD": {"dex": 925, "family": "TANDEMAUS", "buddy_km": 1},
            "MAUSHOLD_FAMILY_OF_FOUR": {"dex": 925, "family": "TANDEMAUS", "buddy_km": 1},
            "MAUSHOLD_FAMILY_OF_THREE": {"dex": 925, "family": "TANDEMAUS", "buddy_km": 1},
            "PORYGON": {"dex": 137, "family": "PORYGON", "buddy_km": 3},
            "PORYGON_Z": {"dex": 474, "family": "PORYGON", "buddy_km": 3},
        }
        self.assertEqual(with_forms({"MAUSHOLD"}, species),
                         {"MAUSHOLD", "MAUSHOLD_FAMILY_OF_FOUR", "MAUSHOLD_FAMILY_OF_THREE"})
        self.assertEqual(with_forms({"MAUSHOLD_FAMILY_OF_FOUR"}, species), {"MAUSHOLD_FAMILY_OF_FOUR"})
        self.assertEqual(with_forms({"PORYGON"}, species), {"PORYGON"})  # different dex number
        inv = Inventory(50, {"TANDEMAUS": 0}, {}, set(), [Specimen("TANDEMAUS"), Specimen("MAUSHOLD")])
        self.assertEqual(plan(species, TIERS, inv).families, [])

    def test_candy_count_only_missing_where_it_matters(self):
        inv = Inventory(0, {}, {}, set(), [Specimen("DRATINI"), Specimen("DRAGONITE"),  # nothing new left
                                           Specimen("FEEBAS"),                           # Milotic is new
                                           Specimen("SCYTHER")])
        inv.candy["SCYTHER"] = 50                                                       # count known
        self.assertEqual(families_missing_candy(inv, SPECIES), ["FEEBAS"])

    def test_owning_final_stage_covers_middle_stage(self):
        # Dragonite is owned, so Dratini -> Dragonair gives nothing new
        inv = Inventory(25, {"DRATINI": 0}, {}, set(), [Specimen("DRATINI"), Specimen("DRAGONITE")])
        p = plan(SPECIES, TIERS, inv)
        self.assertEqual(p.families, [])
        self.assertEqual(with_earlier_stages({"DRAGONITE"}, SPECIES), {"DRATINI", "DRAGONAIR", "DRAGONITE"})

    def test_duplicates_dropped_so_a_later_middle_stage_is_kept(self):
        # seven Dratini listed before the only Dragonair: Dragonair -> Dragonite (100) fits, Dratini's route (125) doesn't
        inv = Inventory(0, {"DRATINI": 100}, {}, set(),
                        [Specimen("DRATINI", label=f"#{i}") for i in range(7)] +
                        [Specimen("DRAGONAIR", label="a"), Specimen("DRAGONAIR", label="b")])
        paths = [path for f in plan(SPECIES, TIERS, inv).families for path in f.paths]
        self.assertEqual([(p.specimen.label, p.targets) for p in paths], [("a", ["DRAGONITE"])])

    def test_branching_species_keep_one_copy_per_branch(self):
        mons = [Specimen(s, label=f"#{i}") for i, s in enumerate(["EEVEE"] * 10 + ["KIRLIA"] * 3 + ["DRATINI"] * 3)]
        kept = Counter(s.species for s in keep_specimens(mons, SPECIES))
        self.assertEqual(kept, {"EEVEE": 8, "KIRLIA": 2, "DRATINI": 1})
        # branches you already own don't need a copy
        kept = Counter(s.species for s in keep_specimens(mons, SPECIES, {"VAPOREON", "JOLTEON", "GALLADE"}))
        self.assertEqual(kept, {"EEVEE": 6, "KIRLIA": 1, "DRATINI": 1})
        # two Kirlia can become Gardevoir and Gallade
        inv = Inventory(0, {"RALTS": 200}, {"ITEM_SINNOH_STONE": 1}, set(),
                        [Specimen("KIRLIA", label="a"), Specimen("KIRLIA", label="b"), Specimen("KIRLIA", label="c")])
        targets = sorted(path.targets[-1] for f in plan(SPECIES, TIERS, inv).families for path in f.paths)
        self.assertEqual(targets, ["GALLADE", "GARDEVOIR"])

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
