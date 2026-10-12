"""The planner and screen reader as the browser app calls them.

WebPlanner takes an inventory and gives the plan as text and as a dashboard page; WebScan turns
screenshots into an inventory. The page in web/ runs this module in Pyodide (Python compiled to
WebAssembly). Planning only uses the standard library; WebScan also needs NumPy and OpenCV, which
the page loads only when screenshots are read, so it imports them lazily. Requests and replies are
JSON strings, which cross between JavaScript and Python without any conversion.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .cli import render
from .dashboard import build_view, render_html
from .gamemaster import evolution_items, item_name
from .inventory import Inventory, inventory_from_dict
from .planner import Settings, families_missing_candy, gives_new_species, plan

TIERS = Path(__file__).resolve().parent / "data" / "rarity_tiers.json"


def settings_from(options: dict) -> Settings:
    """Planner settings from the page's options; any option left out keeps its default."""
    defaults = Settings()
    return Settings(
        dex_bonus=float(options.get("dex_bonus", defaults.dex_bonus)),
        repeat_value=float(options.get("repeat_value", defaults.repeat_value)),
        allow_trade_evolutions=bool(options.get("allow_trade_evolutions", defaults.allow_trade_evolutions)),
        new_entries_only=bool(options.get("new_entries_only", defaults.new_entries_only)),
    )


def items_to_ask(inventory: Inventory, species: dict, settings: Settings) -> dict[str, list[list[str]]]:
    """Evolution items the inventory doesn't list but an owned Pokémon needs, at any depth, to evolve into a species
    the plan counts as new (planner.gives_new_species): {item id: [[from, to], ...]}. An unlisted item is unknown,
    so the page asks for it."""
    is_new = gives_new_species(inventory, species, settings)
    wanted: dict[str, list[list[str]]] = {}
    seen: set[str] = set()
    todo = sorted({specimen.species for specimen in inventory.pokemon})
    while todo:
        species_id = todo.pop()
        if species_id in seen:
            continue
        seen.add(species_id)
        for evolution in species.get(species_id, {}).get("evolutions", []):
            todo.append(evolution["to"])
            if evolution.get("item") and evolution["item"] not in inventory.items and is_new(evolution["to"]):
                wanted.setdefault(evolution["item"], []).append([species_id, evolution["to"]])
    return {item: sorted(steps) for item, steps in sorted(wanted.items())}


class WebPlanner:
    def __init__(self, species_json: str):
        """Hold the species table (normalized, as the site's species.json has it) and the bundled rarity tiers."""
        self.species: dict = json.loads(species_json)
        self.species.pop("_comment", None)
        self.tiers: dict = json.loads(TIERS.read_text(encoding="utf-8"))

    def catalog_json(self) -> str:
        """What the inventory editor needs from the species table, as JSON: each species' family and dex number,
        and the evolution items with the names players know them by."""
        species = {
            species_id: {"family": entry["family"], "dex": entry.get("dex")}
            for species_id, entry in self.species.items()
        }
        items = {item: item_name(item) for item in sorted(evolution_items(self.species))}
        return json.dumps({"species": species, "items": items})

    def plan_json(self, request: str) -> str:
        """Plan a JSON request {"inventory", "options", "source"} and reply in JSON with the text report, the dashboard
        page and what the plan still needs (candy and item counts), or with {"error": message} for a bad inventory."""
        fields = json.loads(request)
        try:
            inventory = inventory_from_dict(fields["inventory"], species=self.species)
            settings = settings_from(fields.get("options") or {})
        except KeyError as error:
            return json.dumps({"error": f"This isn't a valid inventory: an entry has no {error} field."})
        except (ValueError, TypeError, AttributeError) as error:  # a count that isn't a number, a list that isn't one
            return json.dumps({"error": f"This isn't a valid inventory: {error}"})
        result = plan(self.species, self.tiers, inventory, settings)
        return json.dumps(
            {
                "report": render(result),
                "dashboard": render_html(build_view(result, inventory, self.species, fields.get("source", ""))),
                "rare_candy_used": result.rare_candy_used,
                "rare_candy_available": result.rare_candy_available,
                "families": len(result.families),
                "missing_candy": families_missing_candy(inventory, self.species),
                "missing_items": items_to_ask(inventory, self.species, settings),
                "unknown": result.unknown_species,
            }
        )

    def scanner(self, folder: str) -> WebScan:
        """A new scan of the screenshots the page writes into folder, with this species table."""
        return WebScan(self.species, folder)


class WebScan:
    """Screenshots read in the browser, the way `scan` and `evolvenew` read them.

    The page writes each screenshot into the scan's folder, with the file's own modification time; this makes the
    images Tesseract reads (passes_json); tesseract.js reads them in the page (read_json); then this parses every
    screen and merges them as `scan` does (finish_json).
    """

    def __init__(self, species: dict, folder: str):
        """Start a scan in folder, which is the scan's own: emptied now and removed when the scan finishes."""
        self.species = species
        self.folder = Path(folder)
        shutil.rmtree(self.folder, ignore_errors=True)
        (self.folder / ".passes").mkdir(parents=True)
        self.words: dict[str, list] = {}

    def passes_json(self, name: str) -> str:
        """Write the images Tesseract reads for one screenshot, made as the command line makes them, and return
        [{"path", "scale"}, ...] as JSON, or {"error": message} when the file isn't an image."""
        import cv2

        from .scan.ocr import tesseract_passes

        screenshot = cv2.imread(str(self.folder / name))
        if screenshot is None:
            return json.dumps({"error": f"{name} isn't a PNG or JPEG screenshot"})
        passes = []
        for index, (image, scale) in enumerate(tesseract_passes(screenshot)):
            path = self.folder / ".passes" / f"{name}.{index}.png"
            cv2.imwrite(str(path), image)
            passes.append({"path": str(path), "scale": scale})
        return json.dumps(passes)

    def read_json(self, name: str, tsvs: str) -> str:
        """Take what Tesseract read in each pass of one screenshot ([{"tsv", "scale"}, ...] as JSON) and return which
        kind of screen it is, as {"kind": "bag" | "detail" | "pokedex" | "storage" | "unknown"}."""
        from .scan.ocr import merge_passes, words_from_tsv
        from .scan.screens import classify

        self.words[name] = merge_passes(
            [words_from_tsv(read_pass["tsv"], read_pass["scale"]) for read_pass in json.loads(tsvs)]
        )
        for path in (self.folder / ".passes").glob(f"{name}.*.png"):
            path.unlink()
        return json.dumps({"kind": classify(self.words[name])})

    def finish_json(self, evolvenew: bool) -> str:
        """Parse every screenshot read and merge them as `scan` does, then mark what's missing as `evolvenew` does
        when asked. Replies in JSON with the inventory, how many screens of each kind were read, whether the bag was
        among them, the warnings, how many Pokémon a storage search listed and how many were read, and for
        `evolvenew` what the listing showed. The candy counts and the bag's Rare Candy are left to the page's
        questions, so their warnings are left out."""
        from .evolvenew import apply_evolvenew
        from .scan.pipeline import scan

        inventory, report = scan(
            [self.folder / name for name in self.words], self.species, read_text=lambda label, _img: self.words[label]
        )
        shutil.rmtree(self.folder, ignore_errors=True)
        reply = {
            "screens": dict(report.screens),
            "bag": report.screens["bag"] > 0,
            "listed": report.listed,
            "grid": report.grid,
            "warnings": [
                warning for warning in report.warnings if not warning.startswith(("no candy count for", "no item bag"))
            ],
        }
        if evolvenew:
            inventory, listing = apply_evolvenew(inventory, self.species)
            reply.update(mega_only=listing.mega_only, already_caught=listing.already_caught, notes=listing.notes)
        reply["inventory"] = inventory
        return json.dumps(reply)
