"""Load a player's inventory.

JSON format (see examples/inventory_sample.json):
{
  "rare_candy": 120,
  "candy": {"DRATINI": 60, ...},          # keyed by family (base species)
  "items": {"ITEM_METAL_COAT": 1, ...},
  "pokedex": ["DRATINI", ...],            # species already registered
  "pokemon": [{"species": "DRATINI", "iv": [15, 13, 14]}, ...]
}

The "pokemon" list can instead come from a CSV (e.g. exported by a scanner
app). Headers are matched loosely: a species/name column is required, and
attack/defense/stamina IV columns are used if present.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

_SPECIES_COLS = ("species", "name", "pokemon")
_IV_COLS = (("atk iv", "atk", "attack"), ("def iv", "def", "defense"), ("sta iv", "sta", "stamina", "hp iv"))


@dataclass
class Specimen:
    species: str
    iv: tuple[int, int, int] | None = None
    label: str = ""

    @property
    def iv_pct(self) -> float | None:
        return None if self.iv is None else round(sum(self.iv) / 45 * 100, 1)


@dataclass
class Inventory:
    rare_candy: int
    candy: dict[str, int]
    items: dict[str, int] = field(default_factory=dict)
    pokedex: set[str] = field(default_factory=set)
    pokemon: list[Specimen] = field(default_factory=list)


def _norm(name: str) -> str:
    return name.strip().upper().replace(" ", "_").replace("-", "_")


def _specimen(raw: dict, i: int) -> Specimen:
    iv = raw.get("iv")
    return Specimen(_norm(raw["species"]), tuple(iv) if iv else None, raw.get("label", f"#{i + 1}"))


def load_pokemon_csv(path: str | Path) -> list[Specimen]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    headers = {h.lower().strip(): h for h in rows[0]}
    sp_col = next((headers[c] for c in _SPECIES_COLS if c in headers), None)
    if sp_col is None:
        raise ValueError(f"No species/name column in {path}; headers: {list(rows[0])}")
    iv_cols = [next((headers[c] for c in opts if c in headers), None) for opts in _IV_COLS]
    out = []
    for i, row in enumerate(rows):
        iv = None
        if all(iv_cols):
            try:
                iv = tuple(int(row[c]) for c in iv_cols)
            except ValueError:
                iv = None
        out.append(Specimen(_norm(row[sp_col]), iv, f"#{i + 1}"))
    return out


def load_inventory(path: str | Path, pokemon_csv: str | Path | None = None) -> Inventory:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pokemon = load_pokemon_csv(pokemon_csv) if pokemon_csv else [
        _specimen(p, i) for i, p in enumerate(data.get("pokemon", []))
    ]
    return Inventory(
        rare_candy=int(data.get("rare_candy", 0)),
        candy={_norm(k): int(v) for k, v in data.get("candy", {}).items()},
        items={k: int(v) for k, v in data.get("items", {}).items()},
        pokedex={_norm(s) for s in data.get("pokedex", [])},
        pokemon=pokemon,
    )
