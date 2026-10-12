"""Render a plan as a single self-contained HTML dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .gamemaster import item_name
from .inventory import Inventory
from .planner import Plan, families_missing_candy, owned_species

TEMPLATE = Path(__file__).resolve().parent / "data" / "dashboard.html"


def build_view(evolution_plan: Plan, inventory: Inventory, species: dict, source: str = "") -> dict:
    """Flatten a plan and its inventory into the JSON the dashboard template renders."""
    planned: dict[tuple[str, str], str] = {}
    registered = (inventory.pokedex | owned_species(inventory, species)) - inventory.not_caught
    families: list[dict[str, Any]] = []
    for family_plan in evolution_plan.families:
        paths = []
        for path in family_plan.paths:
            if path.steps:
                planned[(path.specimen.species, path.specimen.label)] = (
                    "Evolve to " + path.steps[-1].to.replace("_", " ").title()
                )
            paths.append(
                {
                    "species": path.specimen.species,
                    "label": path.specimen.label,
                    "candy": path.candy,
                    "steps": [
                        {
                            "frm": step.frm,
                            "to": step.to,
                            "candy": step.candy,
                            "item": item_name(step.item) if step.item else None,
                            "notes": list(step.notes),
                            "new_dex": step.to not in registered,
                        }
                        for step in path.steps
                    ],
                }
            )
        families.append(
            {
                "family": family_plan.family,
                "candy_used": family_plan.candy_used,
                "candy_owned": family_plan.candy_owned,
                "rare_candy": family_plan.rare_candy,
                "value": family_plan.value,
                "km_saved": family_plan.km_saved,
                "value_per_candy": family_plan.value / family_plan.candy_used if family_plan.candy_used else 0.0,
                "paths": paths,
            }
        )
    trade = []
    for specimen, step in evolution_plan.trade_instead:
        planned.setdefault((specimen.species, specimen.label), "Trade for " + step.to.replace("_", " ").title())
        trade.append(
            {"species": specimen.species, "label": specimen.label, "frm": step.frm, "to": step.to, "candy": step.candy}
        )

    evolving = {species for species, _ in planned}

    def status(specimen) -> str:
        """Describe what the plan does with one owned Pokémon."""
        if (specimen.species, specimen.label) in planned:
            return planned[(specimen.species, specimen.label)]
        if specimen.species in evolving:
            return "Another one is evolved"  # an extra copy, not something the plan left out
        if specimen.species in inventory.not_caught:
            return "Probably misread: an Evolve button shows it as never caught"
        if specimen.species not in species:
            return "Not in species data"
        return "Not planned" if species[specimen.species].get("evolutions") else "Fully evolved"

    shown = {specimen.species for specimen in inventory.pokemon} | {
        species_id
        for family in families
        for path in family["paths"]
        for species_id in [path["species"]] + [step["to"] for step in path["steps"]]
    }
    shown |= {species_id for trade_entry in trade for species_id in (trade_entry["frm"], trade_entry["to"])}
    return {
        "source": source,
        "available": evolution_plan.rare_candy_available,
        "used": evolution_plan.rare_candy_used,
        "total_value": evolution_plan.total_value,
        "families": families,
        "trade": trade,
        "unknown": evolution_plan.unknown_species,
        "unknown_items": evolution_plan.unknown_items,
        "missing_candy": families_missing_candy(inventory, species),
        "pokemon": [
            {"species": specimen.species, "label": specimen.label, "status": status(specimen)}
            for specimen in inventory.pokemon
        ],
        # national dex numbers pick each species' sprite
        "dex": {
            species_id: species[species_id]["dex"]
            for species_id in sorted(shown)
            if "dex" in species.get(species_id, {})
        },
    }


def render_html(view: dict) -> str:
    """Embed the view in the dashboard template and return a self-contained HTML page."""
    data = json.dumps(view).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", data, 1)
