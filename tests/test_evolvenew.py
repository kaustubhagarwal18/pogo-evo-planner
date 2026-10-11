"""The `evolvenew` flow: detail pages of the Pokémon the game's `evolvenew` search lists, then a plan."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pogo_evo_planner import cli
from pogo_evo_planner.evolvenew import apply_evolvenew, candy_overrides, item_overrides, render
from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.inventory import Inventory, Specimen, inventory_from_dict
from pogo_evo_planner.planner import Settings, plan
from pogo_evo_planner.scan.ocr import Word
from pogo_evo_planner.scan.pipeline import ScanReport, merge
from pogo_evo_planner.scan.screens import ScreenResult, SpeciesMatcher, parse_detail

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "pogo_evo_planner/data/species_sample.json"
SPECIES = load_species(SAMPLE)
TIERS = json.loads((ROOT / "pogo_evo_planner/data/rarity_tiers.json").read_text())


def scanned(*pokemon, candy=None, rare=0, pokedex=()):
    """An inventory dict as `scan` writes it, from detail pages of the given species."""
    return {"rare_candy": rare, "candy": candy or {}, "items": {}, "pokedex": list(pokedex), "not_caught": [],
            "pokemon": [{"species": sp, "cp": None, "label": "detail page"} for sp in pokemon]}


class EvolvenewTests(unittest.TestCase):
    def test_listed_evolutions_are_new_and_final_forms_are_mega_only(self):
        inv, listing = apply_evolvenew(scanned("DRAGONAIR", "GENGAR", "KIRLIA", pokedex=["GALLADE"]), SPECIES)
        self.assertEqual(inv["not_caught"], ["DRAGONITE", "GARDEVOIR"])
        self.assertEqual(listing.mega_only, ["GENGAR"])           # no candy evolution: listed for its Mega
        self.assertEqual(listing.already_caught, ["GALLADE"])     # its sprite showed it caught
        self.assertIn("Kirlia: only Gardevoir", listing.notes[0])

    def test_only_new_entries_are_planned(self):
        # Dragonite was caught before but isn't owned now: a normal plan would get it back, evolvenew doesn't
        inv = Inventory(0, {"DRATINI": 200}, {}, {"DRAGONITE"}, [Specimen("DRAGONAIR")])
        self.assertEqual(len(plan(SPECIES, TIERS, inv).families), 1)
        self.assertEqual(plan(SPECIES, TIERS, inv, Settings(new_entries_only=True)).families, [])

    def test_render_groups_the_plan_in_plain_words(self):
        data, listing = apply_evolvenew(scanned("DRAGONAIR", "FEEBAS", "LARVITAR", "GENGAR",
                                                candy={"DRATINI": 60, "FEEBAS": 400, "LARVITAR": 5}, rare=40),
                                        SPECIES)
        inv = inventory_from_dict(data)
        text = render(plan(SPECIES, TIERS, inv, Settings(new_entries_only=True)), inv, SPECIES, listing)
        self.assertIn("Rare Candy: 40 of 40 used", text)
        self.assertIn("Evolve now with your own candy:\n  Feebas → Milotic (walk 20 km as buddy first)", text)
        self.assertIn("Use Rare Candy:\n  Dragonair → Dragonite  +40 Rare Candy", text)
        self.assertIn("Larvitar → Pupitar: 20 candy short (have 5 of 25)", text)
        self.assertIn("Mega Evolution only (needs Mega Energy, not planned):\n  Gengar", text)

    def test_unknown_candy_and_missing_item_say_what_to_do(self):
        data, listing = apply_evolvenew(scanned("ONIX", "DRATINI"), SPECIES)
        inv = inventory_from_dict(data)
        text = render(plan(SPECIES, TIERS, inv, Settings(new_entries_only=True)), inv, SPECIES, listing)
        self.assertIn("Dratini → Dragonair: candy count unknown: add a screenshot of its page, or --candy DRATINI=N",
                      text)
        inv.candy["ONIX"] = 100
        text = render(plan(SPECIES, TIERS, inv, Settings(new_entries_only=True)), inv, SPECIES, listing)
        self.assertIn('Onix → Steelix: needs Metal Coat: if you have one, add --item "Metal Coat=N"', text)

    def test_one_copy_of_a_branching_pokemon_takes_one_branch(self):
        data, listing = apply_evolvenew(scanned("KIRLIA", candy={"RALTS": 400}), SPECIES)
        data["items"] = {"ITEM_SINNOH_STONE": 1}
        inv = inventory_from_dict(data)
        text = render(plan(SPECIES, TIERS, inv, Settings(new_entries_only=True)), inv, SPECIES, listing)
        self.assertIn("your 1 Kirlia is planned above; if you have another, add a screenshot of its page", text)

    def test_candy_and_item_arguments(self):
        self.assertEqual(candy_overrides(["Dragonair=60"], SPECIES), {"DRATINI": 60})   # name -> its family
        self.assertEqual(item_overrides(["sinnoh stone=2", "ITEM_METAL_COAT=1"], SPECIES),
                         {"ITEM_SINNOH_STONE": 2, "ITEM_METAL_COAT": 1})
        for bad in (["Dragonair"], ["Dragonair=lots"], ["Notamon=3"]):
            with self.assertRaises(ValueError):
                candy_overrides(bad, SPECIES)
        with self.assertRaises(ValueError):
            item_overrides(["Rare Candy=3"], SPECIES)

    def test_detail_pages_count_as_owned_one_copy_per_cp(self):
        words = [Word("CP291", 330, 120, 180, 94, 0.9), Word("Dratini", 300, 790, 250, 80, 0.9),
                 Word("60", 300, 1240, 60, 50, 0.9), Word("DRATINI", 250, 1300, 160, 34, 0.9),
                 Word("CANDY", 420, 1300, 120, 34, 0.9)]
        r = parse_detail(words, SpeciesMatcher(SPECIES), SPECIES)
        self.assertEqual((r.species, r.specimens, r.candy), ("DRATINI", [("DRATINI", 291)], {"DRATINI": 60}))
        frames = [r, r, ScreenResult("detail", species="DRATINI", specimens=[("DRATINI", 412)]),
                  ScreenResult("detail", species="GENGAR")]          # scrolled down: no CP on screen
        inv = merge(frames, ScanReport())
        self.assertEqual(sorted((p["species"], p["cp"]) for p in inv["pokemon"]),
                         [("DRATINI", 291), ("DRATINI", 412), ("GENGAR", None)])

    def test_command(self):
        read = scanned("DRAGONAIR", "GENGAR", candy={"DRATINI": 60})
        report = ScanReport()
        report.screens["detail"] = 2
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("pogo_evo_planner.scan.pipeline.scan", return_value=(read, report)):
            out = Path(d) / "evolvenew.json"
            args = ["evolvenew", "a.png", "--species", str(SAMPLE), "-o", str(out), "-q"]
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                    self.assertRaises(SystemExit) as no_rare:
                cli.main(args)
            self.assertIn("no Rare Candy count", str(no_rare.exception))
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                cli.main(args + ["--rare-candy", "50", "--candy", "Dragonair=70"])
            self.assertIn("Dragonair → Dragonite  +30 Rare Candy", stdout.getvalue())
            self.assertIn("Gengar", stdout.getvalue())
            self.assertEqual(json.loads(out.read_text())["candy"], {"DRATINI": 70})   # typed count wins


if __name__ == "__main__":
    unittest.main()
