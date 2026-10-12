"""The planner as the browser app calls it: an inventory in, the plan as text and as a dashboard page out.

The page in web/ runs this module in Pyodide (Python compiled to WebAssembly), so like the planner it
only uses the standard library. Requests and replies are JSON strings, which cross between JavaScript
and Python without any conversion.
"""
from __future__ import annotations

import json
from pathlib import Path

from .cli import render
from .dashboard import build_view, render_html
from .gamemaster import item_name
from .inventory import Inventory, inventory_from_dict
from .planner import Settings, families_missing_candy, owned_species, plan

TIERS = Path(__file__).resolve().parent / "data" / "rarity_tiers.json"


def settings_from(options: dict) -> Settings:
    """Planner settings from the page's options; any option left out keeps its default."""
    d = Settings()
    return Settings(dex_bonus=float(options.get("dex_bonus", d.dex_bonus)),
                    repeat_value=float(options.get("repeat_value", d.repeat_value)),
                    allow_trade_evolutions=bool(options.get("allow_trade_evolutions", d.allow_trade_evolutions)),
                    new_entries_only=bool(options.get("new_entries_only", d.new_entries_only)))


def items_to_ask(inv: Inventory, species: dict) -> dict[str, list[list[str]]]:
    """Evolution items the inventory doesn't list but an owned Pokémon needs, at any depth, to evolve into a
    species not owned yet: {item id: [[from, to], ...]}. An unlisted item is unknown, so the page asks for it."""
    owned = owned_species(inv, species)
    wanted: dict[str, list[list[str]]] = {}
    seen: set[str] = set()
    todo = sorted({s.species for s in inv.pokemon})
    while todo:
        sp = todo.pop()
        if sp in seen:
            continue
        seen.add(sp)
        for e in species.get(sp, {}).get("evolutions", []):
            todo.append(e["to"])
            if e.get("item") and e["item"] not in inv.items and e["to"] not in owned:
                wanted.setdefault(e["item"], []).append([sp, e["to"]])
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
        items = sorted({e["item"] for v in self.species.values() for e in v.get("evolutions", []) if e.get("item")})
        species = {k: {"family": v["family"], "dex": v.get("dex")} for k, v in self.species.items()}
        return json.dumps({"species": species, "items": {i: item_name(i) for i in items}})

    def plan_json(self, request: str) -> str:
        """Plan a JSON request {"inventory", "options", "source"} and reply in JSON with the text report, the dashboard
        page and what the plan still needs (candy and item counts), or with {"error": message} for a bad inventory."""
        req = json.loads(request)
        try:
            inv = inventory_from_dict(req["inventory"], species=self.species)
            settings = settings_from(req.get("options") or {})
        except KeyError as e:
            return json.dumps({"error": f"This isn't a valid inventory: an entry has no {e} field."})
        except (ValueError, TypeError, AttributeError) as e:   # a count that isn't a number, a list that isn't one
            return json.dumps({"error": f"This isn't a valid inventory: {e}"})
        result = plan(self.species, self.tiers, inv, settings)
        return json.dumps({
            "report": render(result),
            "dashboard": render_html(build_view(result, inv, self.species, req.get("source", ""))),
            "rare_candy_used": result.rare_candy_used,
            "rare_candy_available": result.rare_candy_available,
            "families": len(result.families),
            "missing_candy": families_missing_candy(inv, self.species),
            "missing_items": items_to_ask(inv, self.species),
            "unknown": result.unknown_species,
        })
