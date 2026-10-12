"""Load a player's inventory.

JSON format (see examples/inventory_sample.json):
{
  "rare_candy": 120,
  "candy": {"DRATINI": 60, ...},          # keyed by family (base species)
  "items": {"ITEM_METAL_COAT": 1, "Sinnoh Stone": 1, ...},   # by id or by the name players know
  "pokedex": ["DRATINI", ...],            # species already registered
  "not_caught": ["DRAGONITE", ...],       # never caught (silhouette on an EVOLVE button)
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

from .gamemaster import evolution_items, item_id, item_name

_SPECIES_COLS = ("species", "name", "pokemon")
_IV_COLS = (("atk iv", "atk", "attack"), ("def iv", "def", "defense"), ("sta iv", "sta", "stamina", "hp iv"))


@dataclass
class Specimen:
    species: str
    iv: tuple[int, int, int] | None = None
    label: str = ""

    @property
    def iv_pct(self) -> float | None:
        """IV total as a percentage of the 45-point maximum, or None when IVs are unknown."""
        return None if self.iv is None else round(sum(self.iv) / 45 * 100, 1)


@dataclass
class Inventory:
    rare_candy: int
    candy: dict[str, int]
    items: dict[str, int] = field(default_factory=dict)
    pokedex: set[str] = field(default_factory=set)
    pokemon: list[Specimen] = field(default_factory=list)
    not_caught: set[str] = field(default_factory=set)  # overrides a storage reading of the same species


def _norm(name: str) -> str:
    """Normalize a species or family name to the game-master id style (DRATINI, MR_MIME)."""
    return name.strip().upper().replace(" ", "_").replace("-", "_")


def _specimen(raw: dict, position: int) -> Specimen:
    """Build a Specimen from one inventory JSON entry, defaulting the label to its position."""
    ivs = raw.get("iv")
    return Specimen(_norm(raw["species"]), tuple(ivs) if ivs else None, raw.get("label", f"#{position + 1}"))


def load_pokemon_csv(path: str | Path) -> list[Specimen]:
    """Read owned Pokémon from a CSV, matching species and IV columns loosely by header."""
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        rows = list(csv.DictReader(csv_file))
    if not rows:
        return []
    headers = {header.lower().strip(): header for header in rows[0]}
    species_column = next((headers[column] for column in _SPECIES_COLS if column in headers), None)
    if species_column is None:
        raise ValueError(f"No species/name column in {path}; headers: {list(rows[0])}")
    iv_columns = [next((headers[column] for column in names if column in headers), None) for names in _IV_COLS]
    specimens = []
    for position, row in enumerate(rows):
        ivs: tuple[int, int, int] | None = None
        if all(iv_columns):
            try:
                attack, defense, stamina = (int(row[column]) for column in iv_columns)
                ivs = (attack, defense, stamina)
            except ValueError:
                ivs = None
        specimens.append(Specimen(_norm(row[species_column]), ivs, f"#{position + 1}"))
    return specimens


def load_inventory(path: str | Path, pokemon_csv: str | Path | None = None, species: dict | None = None) -> Inventory:
    """Load an inventory JSON, optionally taking the Pokémon list from a CSV instead. With a species table, items
    may be named as players know them."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return inventory_from_dict(data, load_pokemon_csv(pokemon_csv) if pokemon_csv else None, species)


def _items(raw: dict, species: dict | None) -> dict[str, int]:
    """Item counts keyed by the game's ids. With a species table, the names players use ("Sinnoh Stone",
    ITEM_SINNOH_STONE) become ids too; an item no evolution needs keeps its key, and the plan reports it.
    Raises ValueError when one item is listed under two names, rather than guess which count is right."""
    items = {name: int(count) for name, count in raw.items()}
    if species is None:
        return items
    known = evolution_items(species)
    by_id: dict[str, int] = {}
    written: dict[str, str] = {}
    for key, count in items.items():
        item = item_id(key, known) or key
        if item in by_id:
            raise ValueError(f'"items" lists {item_name(item)} twice, as {written[item]} and {key}; keep one')
        by_id[item], written[item] = count, key
    return by_id


def inventory_from_dict(data: dict, pokemon: list[Specimen] | None = None, species: dict | None = None) -> Inventory:
    """Build an Inventory from the inventory JSON structure (as `scan` writes it). With a species table, items
    may be named as players know them ("Sinnoh Stone")."""
    if pokemon is None:
        pokemon = [_specimen(entry, position) for position, entry in enumerate(data.get("pokemon", []))]
    return Inventory(
        rare_candy=int(data.get("rare_candy", 0)),
        candy={_norm(family): int(count) for family, count in data.get("candy", {}).items()},
        items=_items(data.get("items", {}), species),
        pokedex={_norm(species_name) for species_name in data.get("pokedex", [])},
        not_caught={_norm(species_name) for species_name in data.get("not_caught", [])},
        pokemon=pokemon,
    )
