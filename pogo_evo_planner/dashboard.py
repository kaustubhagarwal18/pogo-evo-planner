"""Render a plan as a single self-contained HTML dashboard."""
from __future__ import annotations

import json
from pathlib import Path

from .inventory import Inventory
from .planner import Plan, families_missing_candy, owned_species

TEMPLATE = Path(__file__).resolve().parent / "data" / "dashboard.html"


def build_view(p: Plan, inv: Inventory, species: dict, source: str = "") -> dict:
    """Flatten a plan and its inventory into the JSON the dashboard template renders."""
    planned: dict[tuple[str, str], str] = {}
    registered = (inv.pokedex | owned_species(inv, species)) - inv.not_caught
    families = []
    for f in p.families:
        paths = []
        for path in f.paths:
            if path.steps:
                planned[(path.specimen.species, path.specimen.label)] = "Evolve to " + path.steps[-1].to.replace("_", " ").title()
            paths.append({
                "species": path.specimen.species, "label": path.specimen.label, "candy": path.candy,
                "steps": [{"frm": s.frm, "to": s.to, "candy": s.candy, "item": s.item, "notes": list(s.notes),
                           "new_dex": s.to not in registered} for s in path.steps],
            })
        families.append({
            "family": f.family, "candy_used": f.candy_used, "candy_owned": f.candy_owned,
            "rare_candy": f.rare_candy, "value": f.value, "km_saved": f.km_saved,
            "value_per_candy": f.value / f.candy_used if f.candy_used else 0.0, "paths": paths,
        })
    trade = []
    for spec, step in p.trade_instead:
        planned.setdefault((spec.species, spec.label), "Trade for " + step.to.replace("_", " ").title())
        trade.append({"species": spec.species, "label": spec.label, "frm": step.frm, "to": step.to, "candy": step.candy})

    def status(sp) -> str:
        """Describe what the plan does with one owned Pokémon."""
        if (sp.species, sp.label) in planned:
            return planned[(sp.species, sp.label)]
        if sp.species in inv.not_caught:
            return "Probably misread: an Evolve button shows it as never caught"
        if sp.species not in species:
            return "Not in species data"
        return "Not planned" if species[sp.species].get("evolutions") else "Fully evolved"

    shown = {sp.species for sp in inv.pokemon} | {s for f in families for path in f["paths"]
                                                  for s in [path["species"]] + [st["to"] for st in path["steps"]]}
    shown |= {s for t in trade for s in (t["frm"], t["to"])}
    return {
        "source": source,
        "available": p.rare_candy_available, "used": p.rare_candy_used, "total_value": p.total_value,
        "families": families, "trade": trade, "unknown": p.unknown_species,
        "missing_candy": families_missing_candy(inv, species),
        "pokemon": [{"species": s.species, "label": s.label, "status": status(s)} for s in inv.pokemon],
        # national dex numbers pick each species' sprite
        "dex": {s: species[s]["dex"] for s in sorted(shown) if "dex" in species.get(s, {})},
    }


def render_html(view: dict) -> str:
    """Embed the view in the dashboard template and return a self-contained HTML page."""
    data = json.dumps(view).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", data, 1)
