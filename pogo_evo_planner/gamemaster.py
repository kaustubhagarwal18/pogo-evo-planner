"""Load species/evolution data.

Two input formats are supported:

1. The datamined game master published by PokeMiners
   (github.com/PokeMiners/game_masters, file latest/latest.json): a JSON list of
   {"templateId": ..., "data": {...}} entries.
2. A normalized species table (what this module produces), e.g.
   data/species_sample.json:

   {"DRATINI": {"family": "DRATINI", "buddy_km": 5, "rarity": "standard",
                "evolutions": [{"to": "DRAGONAIR", "candy": 25, "item": null,
                                "trade_free": false, "notes": []}]}}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_POKEMON_TEMPLATE = re.compile(r"^V\d{4}_POKEMON_")

_RARITY_MAP = {
    "POKEMON_RARITY_LEGENDARY": "legendary",
    "POKEMON_RARITY_MYTHIC": "mythical",
    "POKEMON_RARITY_ULTRA_BEAST": "ultra_beast",
}

# evolutionBranch keys that describe an extra condition we surface as a note.
_CONDITION_NOTES = {
    "kmBuddyDistanceRequirement": "walk {v} km as buddy first",
    "mustBeBuddy": "must be your buddy",
    "onlyDaytime": "evolve during the day",
    "onlyNighttime": "evolve at night",
    "genderRequirement": "gender: {v}",
    "buddyAffectionRequirement": "needs buddy affection",
    "onlyUpsideDown": "phone upside down",
    "questDisplay": "needs an evolution quest",
}


def _species_key(ps: dict) -> str:
    """Species id for a game master entry: the form name for regional/alternate forms, else the Pokémon id."""
    form = ps.get("form")
    if form and not str(form).endswith("_NORMAL"):
        return str(form)
    return str(ps["pokemonId"])


def _target_key(branch: dict) -> str:
    """Species id an evolution branch leads to, using the form name when it isn't the normal form."""
    form = branch.get("form")
    if form and not str(form).endswith("_NORMAL"):
        return str(form)
    return str(branch["evolution"])


def parse_game_master(entries: list) -> dict:
    """Normalize the raw PokeMiners game master into a species table."""
    species: dict[str, dict] = {}
    for entry in entries:
        tid = entry.get("templateId", "")
        ps = entry.get("data", {}).get("pokemonSettings")
        if not ps or not _POKEMON_TEMPLATE.match(tid):
            continue
        key = _species_key(ps)
        if key in species:  # first entry wins (base form appears first)
            continue
        family = str(ps.get("familyId", ps["pokemonId"])).removeprefix("FAMILY_")
        evolutions = []
        for br in ps.get("evolutionBranch", []) or []:
            if "evolution" not in br or "temporaryEvolution" in br:
                continue  # mega evolutions use energy, not candy
            notes = []
            for k, tmpl in _CONDITION_NOTES.items():
                if br.get(k):
                    notes.append(tmpl.format(v=br[k]))
            item = br.get("evolutionItemRequirement") or br.get("lureItemRequirement")
            evolutions.append({
                "to": _target_key(br),
                "candy": int(br.get("candyCost", ps.get("candyToEvolve", 0)) or 0),
                "item": item,
                "trade_free": bool(br.get("noCandyCostViaTrade", False)),
                "notes": notes,
            })
        species[key] = {
            "dex": int(tid[1:5]),
            "family": family,
            "buddy_km": float(ps.get("kmBuddyDistance", 1) or 1),
            "rarity": _RARITY_MAP.get(ps.get("rarity", ""), "standard"),
            "evolutions": evolutions,
        }
    return species


def load_species(path: str | Path) -> dict:
    """Load either a raw game master or an already-normalized species table."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return parse_game_master(data)
    data.pop("_comment", None)
    return data
