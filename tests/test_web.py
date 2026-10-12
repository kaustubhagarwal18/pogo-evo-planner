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

import cv2
import numpy as np

from pogo_evo_planner.cli import render
from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.inventory import inventory_from_dict
from pogo_evo_planner.planner import Settings, plan
from pogo_evo_planner.scan.ocr import Word
from pogo_evo_planner.webapp import InputError, WebPlanner, json_api, settings_from

from . import mockscreens

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "pogo_evo_planner/data/species_sample.json"
SPECIES = load_species(SAMPLE)
TIERS = json.loads((ROOT / "pogo_evo_planner/data/rarity_tiers.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "examples/inventory_sample.json").read_text(encoding="utf-8"))
BUILD = runpy.run_path(str(ROOT / "scripts/build_web.py"), run_name="build_web")


def page_call(method, **args):
    """Call a @json_api method as the page does: the arguments as one JSON object, the reply parsed."""
    return json.loads(method(json.dumps(args)))


def ask(planner: WebPlanner, inventory: dict, **options) -> dict:
    """Plan an inventory through the JSON interface the page uses."""
    return json.loads(planner.plan_json(json.dumps({"inventory": inventory, "options": options, "source": "test"})))


def tsv(words: list[Word], scale: float = 1.0) -> str:
    """Words as tesseract.js writes them: TSV rows with no header, boxes in the pixels of the image it read."""
    return "".join(
        f"5\t1\t1\t1\t1\t{index}\t{round(word.x * scale)}\t{round(word.y * scale)}\t{round(word.w * scale)}\t"
        f"{round(word.h * scale)}\t{word.conf * 100:.0f}\t{word.text}\n"
        for index, word in enumerate(words)
    )


BAG = [Word("Rare", 300, 900, 88, 40, 0.9), Word("Candy", 420, 900, 110, 40, 0.9), Word("x150", 860, 900, 88, 40, 0.9)]
DRAGONAIR = [
    Word("Dragonair", 400, 900, 198, 80, 0.9),
    Word("60", 760, 1240, 44, 40, 0.9),
    Word("DRATINI", 640, 1320, 154, 40, 0.9),
    Word("CANDY", 820, 1320, 110, 40, 0.9),
    Word("EVOLVE", 450, 1760, 132, 40, 0.9),
]


def build(folder: Path) -> str:
    """Build the site from the sample species into folder; return what the script printed."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        BUILD["main"](["--species", str(SAMPLE), "-o", str(folder)])
    return printed.getvalue()


class WebPlannerTests(unittest.TestCase):
    planner = WebPlanner(SAMPLE.read_text(encoding="utf-8"))

    def test_plans_like_the_command_line(self):
        """The page gets the same plan as `pogo-evo-planner plan`, as text and as a dashboard."""
        reply = ask(self.planner, EXAMPLE)
        expected = plan(SPECIES, TIERS, inventory_from_dict(EXAMPLE))
        self.assertEqual(reply["report"], render(expected))
        self.assertEqual(
            (reply["rare_candy_used"], reply["rare_candy_available"], reply["families"]),
            (expected.rare_candy_used, 150, len(expected.families)),
        )
        self.assertIn('"source": "test"', reply["dashboard"])  # the dashboard says what the plan was built from
        self.assertEqual(reply["unknown"], ["SNORLAX"])

    def test_options_reach_the_planner(self):
        """Settings come from the page's options; one left out keeps the planner's default."""
        self.assertEqual(settings_from({}), Settings())
        self.assertEqual(
            settings_from({"new_entries_only": True, "dex_bonus": 5, "allow_trade_evolutions": True}),
            Settings(dex_bonus=5.0, new_entries_only=True, allow_trade_evolutions=True),
        )
        lots, none = ask(self.planner, EXAMPLE, dex_bonus=1000), ask(self.planner, EXAMPLE, dex_bonus=0)
        self.assertNotEqual(lots["report"], none["report"])

    def test_bad_inventory_is_an_error_not_a_crash(self):
        """A count that isn't a number or a Pokémon with no species comes back as one readable line."""
        for inventory, words in (
            ({"candy": {"DRATINI": "lots"}}, "isn't a valid inventory"),
            ({"pokemon": [{"iv": [1, 2, 3]}]}, "no 'species' field"),
            ({"pokemon": "Dratini"}, "isn't a valid inventory"),
        ):
            reply = ask(self.planner, inventory)
            self.assertEqual(list(reply), ["error"])
            self.assertIn(words, reply["error"])

    def test_asks_for_counts_the_plan_is_missing(self):
        """A family without a candy count and an unlisted item an evolution needs are asked for; 0 is an answer."""
        reply = ask(self.planner, {"rare_candy": 100, "pokemon": [{"species": "ONIX"}]})
        self.assertEqual(reply["missing_candy"], ["ONIX"])
        self.assertEqual(reply["missing_items"], {"ITEM_METAL_COAT": [["ONIX", "STEELIX"]]})
        answered = ask(
            self.planner,
            {
                "rare_candy": 100,
                "candy": {"ONIX": 0},
                "items": {"ITEM_METAL_COAT": 0},
                "pokemon": [{"species": "ONIX"}],
            },
        )
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

    def test_items_asked_only_for_evolutions_the_plan_counts(self):
        """With new entries only (evolvenew), an item for a species already registered isn't asked for; otherwise it is,
        since evolving into a species you don't hold still counts."""
        inventory = {"candy": {"RALTS": 300}, "pokedex": ["GALLADE"], "pokemon": [{"species": "KIRLIA"}]}
        self.assertEqual(
            ask(self.planner, inventory)["missing_items"], {"ITEM_GEN4_EVOLUTION_STONE": [["KIRLIA", "GALLADE"]]}
        )
        self.assertEqual(ask(self.planner, inventory, new_entries_only=True)["missing_items"], {})

    def test_catalog_has_families_and_item_names(self):
        """The editor gets each species' family and dex number, and items by the names players use."""
        catalog = json.loads(self.planner.catalog_json())
        self.assertEqual(catalog["species"]["DRAGONITE"], {"family": "DRATINI", "dex": 149})
        self.assertEqual(catalog["items"]["ITEM_METAL_COAT"], "Metal Coat")


class WebScanTests(unittest.TestCase):
    planner = WebPlanner(SAMPLE.read_text(encoding="utf-8"))

    def scan_shots(self, shots: dict[str, tuple], evolvenew: bool = False) -> dict:
        """Read screenshots {name: (image, words)} through WebScan as the page does, with each pass's TSV made from
        the words; return finish_json's reply."""
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.planner.scanner(str(Path(folder) / "scan"))
            for name, (image, words) in shots.items():
                cv2.imwrite(str(scanner.folder / name), image)
                passes = page_call(scanner.passes_json, name=name)
                self.assertEqual([Path(read_pass["path"]).is_file() for read_pass in passes], [True] * 3)
                tsvs = [{"tsv": tsv(words, read_pass["scale"]), "scale": read_pass["scale"]} for read_pass in passes]
                kind = page_call(scanner.read_json, name=name, tsvs=tsvs)["kind"]
                self.assertIn(kind, ("bag", "detail"))
                self.assertFalse(any(scanner.folder.glob(".passes/*")))  # read passes are removed
            reply = page_call(scanner.finish_json, evolvenew=evolvenew)
            self.assertFalse(scanner.folder.exists())  # the scan's folder goes when it finishes
        return reply

    def test_screenshots_become_an_inventory(self):
        """A bag and a Pokémon page read as `scan` reads them: Rare Candy, the family's candy and the Pokémon."""
        blank = np.full((2400, 1080, 3), 250, np.uint8)
        reply = self.scan_shots({"bag.png": (mockscreens.bag_screen(), BAG), "dragonair.png": (blank, DRAGONAIR)})
        inventory = reply["inventory"]
        self.assertEqual((inventory["rare_candy"], inventory["candy"]), (150, {"DRATINI": 60}))
        self.assertEqual([entry["species"] for entry in inventory["pokemon"]], ["DRAGONAIR"])
        self.assertEqual((reply["screens"], reply["bag"]), ({"bag": 1, "detail": 1}, True))
        self.assertNotIn("DRAGONITE", inventory["not_caught"])

    def test_evolvenew_marks_the_listed_evolutions_never_caught(self):
        """In the evolvenew way, a listed Pokémon's evolution is never caught, and the page asks for Rare Candy
        rather than warn when no bag was read."""
        blank = np.full((2400, 1080, 3), 250, np.uint8)
        reply = self.scan_shots({"dragonair.png": (blank, DRAGONAIR)}, evolvenew=True)
        self.assertEqual(reply["inventory"]["not_caught"], ["DRAGONITE"])
        self.assertFalse(reply["bag"])
        self.assertFalse([warning for warning in reply["warnings"] if "bag" in warning or "candy count" in warning])
        self.assertEqual(reply["mega_only"], [])

    def test_a_file_that_isnt_an_image_is_named(self):
        """A file that isn't a PNG or JPEG gets one line naming it, not a crash."""
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.planner.scanner(str(Path(folder) / "scan"))
            (scanner.folder / "notes.png").write_text("not a picture", encoding="utf-8")
            self.assertEqual(
                page_call(scanner.passes_json, name="notes.png"), {"error": "notes.png isn't a PNG or JPEG screenshot"}
            )

    def test_the_folder_goes_even_when_finishing_fails(self):
        """A scan that fails while merging still removes its folder of screenshots."""
        with tempfile.TemporaryDirectory() as folder:
            scanner = self.planner.scanner(str(Path(folder) / "scan"))
            scanner.words["gone.png"] = BAG  # read, but the file has since disappeared
            with self.assertRaises(ValueError):
                page_call(scanner.finish_json, evolvenew=False)
            self.assertFalse(scanner.folder.exists())


class JsonApiTests(unittest.TestCase):
    class Echo:
        """A stand-in for a class the page calls."""

        @json_api
        def echo(self, word: str = "none") -> dict:
            """Reply with the word, or refuse one the player could fix, or fail like a bug would."""
            if word == "bad":
                raise InputError("that word won't do")
            if word == "bug":
                raise KeyError("oops")
            return {"word": word}

    def test_arguments_come_from_one_json_object_and_the_reply_is_json(self):
        """The page's JSON object becomes keyword arguments, an empty call uses the defaults, the reply is JSON."""
        self.assertEqual(self.Echo().echo(json.dumps({"word": "candy"})), '{"word": "candy"}')
        self.assertEqual(self.Echo().echo(), '{"word": "none"}')

    def test_input_errors_become_a_message_and_bugs_are_raised(self):
        """A mistake the player can fix comes back as {"error"}; anything else is a bug and isn't hidden."""
        self.assertEqual(page_call(self.Echo().echo, word="bad"), {"error": "that word won't do"})
        with self.assertRaises(KeyError):
            self.Echo().echo(json.dumps({"word": "bug"}))


class SiteBuildTests(unittest.TestCase):
    def test_site_has_the_page_package_and_species(self):
        """The site holds the page stamped with a build id, the package without caches, and the species table."""
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder) / "site"
            self.assertIn("65 species", build(site))
            page = (site / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("{{BUILD}}", page)
            self.assertRegex(page, r'app\.js\?v=[0-9a-f]{12}"')
            self.assertEqual(json.loads((site / "species.json").read_text(encoding="utf-8")), SPECIES)
            with zipfile.ZipFile(site / "planner.zip") as archive:
                names = archive.namelist()
            self.assertIn("pogo_evo_planner/webapp.py", names)
            self.assertIn("pogo_evo_planner/data/dashboard.html", names)
            self.assertFalse([name for name in names if "__pycache__" in name or name.endswith(".pyc")])
            for file_name in ("app.js", "worker.js", "style.css", "inventory_sample.json"):
                self.assertTrue((site / file_name).is_file(), file_name)

    def test_same_files_give_the_same_build(self):
        """Building twice gives the same files, so browsers only fetch again when something changed."""
        with tempfile.TemporaryDirectory() as folder:
            build(Path(folder) / "a")
            build(Path(folder) / "b")
            for file_name in ("index.html", "planner.zip"):
                self.assertEqual(
                    (Path(folder) / "a" / file_name).read_bytes(),
                    (Path(folder) / "b" / file_name).read_bytes(),
                    file_name,
                )

    def test_package_plans_with_the_standard_library_only(self):
        """The zipped package plans the example in a Python with no installed packages, as it must in Pyodide."""
        with tempfile.TemporaryDirectory() as folder:
            build(Path(folder) / "site")
            with zipfile.ZipFile(Path(folder) / "site" / "planner.zip") as archive:
                archive.extractall(Path(folder) / "py")
            code = (
                "import json, sys\n"
                "sys.path.insert(0, '.')\n"
                "from pogo_evo_planner.webapp import WebPlanner\n"
                f"w = WebPlanner(open({str(Path(folder) / 'site' / 'species.json')!r}).read())\n"
                f"print(json.loads(w.plan_json(json.dumps({{'inventory': {EXAMPLE!r}}})))['rare_candy_used'])\n"
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('cv2', 'numpy', 'rapidocr')))\n"
            )
            # -S: no site-packages, so an import of anything outside the standard library fails here
            done = subprocess.run(
                [sys.executable, "-S", "-c", code],
                cwd=Path(folder) / "py",  # nosec B603
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            used, extras = done.stdout.splitlines()
            self.assertEqual(int(used), plan(SPECIES, TIERS, inventory_from_dict(EXAMPLE)).rare_candy_used)
            self.assertEqual(extras, "[]")

    def test_a_broken_species_file_is_named(self):
        """A species file that isn't JSON stops the build with one line naming the file."""
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "species.json"
            broken.write_text("{not json", encoding="utf-8")
            with self.assertRaises(SystemExit) as stop:
                BUILD["main"](["--species", str(broken), "-o", str(Path(folder) / "site")])
            self.assertTrue(str(stop.exception.code).startswith(f"error: {broken} isn't a valid species table: "))

    def test_refuses_to_replace_a_folder_it_didnt_build(self):
        """An output folder holding anything other than an earlier build is left alone, with one line saying why."""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "notes.txt").write_text("mine", encoding="utf-8")
            with self.assertRaises(SystemExit) as stop:
                build(Path(folder))
            self.assertTrue(re.match(r"error: .* isn't a site this script built", str(stop.exception.code)))
            self.assertEqual((Path(folder) / "notes.txt").read_text(encoding="utf-8"), "mine")


if __name__ == "__main__":
    unittest.main()
