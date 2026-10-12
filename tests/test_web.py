"""The browser app: the Python it calls (webapp.py) and the site scripts/build_web.py assembles."""
import contextlib
import io
import json
import re
import runpy
import subprocess  # runs this same Python on the built package  # nosec B404
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from pogo_evo_planner.cli import render
from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.inventory import inventory_from_dict
from pogo_evo_planner.planner import Settings, plan
from pogo_evo_planner.webapp import WebPlanner, settings_from

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "pogo_evo_planner/data/species_sample.json"
SPECIES = load_species(SAMPLE)
TIERS = json.loads((ROOT / "pogo_evo_planner/data/rarity_tiers.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "examples/inventory_sample.json").read_text(encoding="utf-8"))
BUILD = runpy.run_path(str(ROOT / "scripts/build_web.py"), run_name="build_web")


def ask(planner: WebPlanner, inventory: dict, **options) -> dict:
    """Plan an inventory through the JSON interface the page uses."""
    return json.loads(planner.plan_json(json.dumps({"inventory": inventory, "options": options, "source": "test"})))


def build(out: Path) -> str:
    """Build the site from the sample species into out; return what the script printed."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        BUILD["main"](["--species", str(SAMPLE), "-o", str(out)])
    return printed.getvalue()


class WebPlannerTests(unittest.TestCase):
    planner = WebPlanner(SAMPLE.read_text(encoding="utf-8"))

    def test_plans_like_the_command_line(self):
        """The page gets the same plan as `pogo-evo-planner plan`, as text and as a dashboard."""
        reply = ask(self.planner, EXAMPLE)
        expected = plan(SPECIES, TIERS, inventory_from_dict(EXAMPLE))
        self.assertEqual(reply["report"], render(expected))
        self.assertEqual((reply["rare_candy_used"], reply["rare_candy_available"], reply["families"]),
                         (expected.rare_candy_used, 150, len(expected.families)))
        self.assertIn('"source": "test"', reply["dashboard"])   # the dashboard says what the plan was built from
        self.assertEqual(reply["unknown"], ["SNORLAX"])

    def test_options_reach_the_planner(self):
        """Settings come from the page's options; one left out keeps the planner's default."""
        self.assertEqual(settings_from({}), Settings())
        self.assertEqual(settings_from({"new_entries_only": True, "dex_bonus": 5, "allow_trade_evolutions": True}),
                         Settings(dex_bonus=5.0, new_entries_only=True, allow_trade_evolutions=True))
        lots, none = ask(self.planner, EXAMPLE, dex_bonus=1000), ask(self.planner, EXAMPLE, dex_bonus=0)
        self.assertNotEqual(lots["report"], none["report"])

    def test_bad_inventory_is_an_error_not_a_crash(self):
        """A count that isn't a number or a Pokémon with no species comes back as one readable line."""
        for inventory, words in (({"candy": {"DRATINI": "lots"}}, "isn't a valid inventory"),
                                 ({"pokemon": [{"iv": [1, 2, 3]}]}, "no 'species' field"),
                                 ({"pokemon": "Dratini"}, "isn't a valid inventory")):
            reply = ask(self.planner, inventory)
            self.assertEqual(list(reply), ["error"])
            self.assertIn(words, reply["error"])

    def test_asks_for_counts_the_plan_is_missing(self):
        """A family without a candy count and an unlisted item an evolution needs are asked for; 0 is an answer."""
        reply = ask(self.planner, {"rare_candy": 100, "pokemon": [{"species": "ONIX"}]})
        self.assertEqual(reply["missing_candy"], ["ONIX"])
        self.assertEqual(reply["missing_items"], {"ITEM_METAL_COAT": [["ONIX", "STEELIX"]]})
        answered = ask(self.planner, {"rare_candy": 100, "candy": {"ONIX": 0}, "items": {"ITEM_METAL_COAT": 0},
                                      "pokemon": [{"species": "ONIX"}]})
        self.assertEqual((answered["missing_candy"], answered["missing_items"]), ([], {}))

    def test_asks_for_items_further_down_the_line(self):
        """An item a later stage needs is asked for too, but not one for a species already owned."""
        reply = ask(self.planner, {"candy": {"RALTS": 0}, "pokemon": [{"species": "RALTS"}]})
        self.assertEqual(reply["missing_items"], {"ITEM_GEN4_EVOLUTION_STONE": [["KIRLIA", "GALLADE"]]})
        owned = ask(self.planner, {"candy": {"RALTS": 0}, "pokemon": [{"species": "RALTS"}, {"species": "GALLADE"}]})
        self.assertEqual(owned["missing_items"], {})

    def test_items_by_the_names_players_use(self):
        """An item named as players know it counts, and the same item under two names is an error, not a guess."""
        kirlia = {"candy": {"RALTS": 300}, "pokemon": [{"species": "KIRLIA"}]}
        self.assertEqual(ask(self.planner, {**kirlia, "items": {"Sinnoh Stone": 1}})["missing_items"], {})
        reply = ask(self.planner, {**kirlia, "items": {"Sinnoh Stone": 1, "ITEM_SINNOH_STONE": 1}})
        self.assertIn("lists Sinnoh Stone twice", reply["error"])

    def test_catalog_has_families_and_item_names(self):
        """The editor gets each species' family and dex number, and items by the names players use."""
        catalog = json.loads(self.planner.catalog_json())
        self.assertEqual(catalog["species"]["DRAGONITE"], {"family": "DRATINI", "dex": 149})
        self.assertEqual(catalog["items"]["ITEM_METAL_COAT"], "Metal Coat")


class SiteBuildTests(unittest.TestCase):
    def test_site_has_the_page_package_and_species(self):
        """The site holds the page stamped with a build id, the package without caches, and the species table."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            self.assertIn("65 species", build(out))
            page = (out / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("{{BUILD}}", page)
            self.assertRegex(page, r'app\.js\?v=[0-9a-f]{12}"')
            self.assertEqual(json.loads((out / "species.json").read_text(encoding="utf-8")), SPECIES)
            with zipfile.ZipFile(out / "planner.zip") as z:
                names = z.namelist()
            self.assertIn("pogo_evo_planner/webapp.py", names)
            self.assertIn("pogo_evo_planner/data/dashboard.html", names)
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
            for f in ("app.js", "worker.js", "style.css", "inventory_sample.json"):
                self.assertTrue((out / f).is_file(), f)

    def test_same_files_give_the_same_build(self):
        """Building twice gives the same files, so browsers only fetch again when something changed."""
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp) / "a")
            build(Path(tmp) / "b")
            for f in ("index.html", "planner.zip"):
                self.assertEqual((Path(tmp) / "a" / f).read_bytes(), (Path(tmp) / "b" / f).read_bytes(), f)

    def test_package_plans_with_the_standard_library_only(self):
        """The zipped package plans the example in a Python with no installed packages, as it must in Pyodide."""
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp) / "site")
            with zipfile.ZipFile(Path(tmp) / "site" / "planner.zip") as z:
                z.extractall(Path(tmp) / "py")
            code = ("import json, sys\n"
                    "sys.path.insert(0, '.')\n"
                    "from pogo_evo_planner.webapp import WebPlanner\n"
                    f"w = WebPlanner(open({str(Path(tmp) / 'site' / 'species.json')!r}).read())\n"
                    f"print(json.loads(w.plan_json(json.dumps({{'inventory': {EXAMPLE!r}}})))['rare_candy_used'])\n"
                    "print(sorted(m for m in sys.modules if m.split('.')[0] in ('cv2', 'numpy', 'rapidocr')))\n")
            # -S: no site-packages, so an import of anything outside the standard library fails here
            done = subprocess.run([sys.executable, "-S", "-c", code], cwd=Path(tmp) / "py",  # nosec B603
                                  capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            used, extras = done.stdout.splitlines()
            self.assertEqual(int(used), plan(SPECIES, TIERS, inventory_from_dict(EXAMPLE)).rare_candy_used)
            self.assertEqual(extras, "[]")

    def test_refuses_to_replace_a_folder_it_didnt_build(self):
        """An output folder holding anything other than an earlier build is left alone, with one line saying why."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("mine", encoding="utf-8")
            with self.assertRaises(SystemExit) as stop:
                build(Path(tmp))
            self.assertTrue(re.match(r"error: .* isn't a site this script built", str(stop.exception.code)))
            self.assertEqual((Path(tmp) / "notes.txt").read_text(encoding="utf-8"), "mine")


if __name__ == "__main__":
    unittest.main()
