"""Decide which evolutions to do and where to spend rare candy.

Model
-----
* Every family has a cost per candy, in "km-equivalent effort":
      cost_per_candy = buddy_km * rarity_multiplier
  Buddy km is how far you walk per candy; the rarity multiplier stands in for
  how hard the species is to catch (which the game data doesn't expose).
* An evolution is worth   candy_cost * cost_per_candy   (the effort it would
  take to farm that candy), plus a bonus if it registers a new Pokédex entry.
* Rare candy converts 1:1 into any family's candy, and a family's candy is
  shared by all its members.

Optimisation
------------
1. Per family: enumerate evolution paths for each owned specimen (including
   multi-step paths and branches) and run a small DP over specimens. The
   result is the family's options: (candy needed, items used, value).
2. Across families: a grouped knapsack picks one option per family so that
   total rare candy <= what you hold and shared items (Metal Coat, Sinnoh
   Stone, lures) aren't over-used, maximising total value.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .inventory import Inventory, Specimen


@dataclass(frozen=True)
class Step:
    frm: str
    to: str
    candy: int
    item: str | None
    notes: tuple[str, ...]


@dataclass
class Path:
    specimen: Specimen
    steps: tuple[Step, ...]

    @property
    def candy(self) -> int:
        return sum(s.candy for s in self.steps)

    @property
    def items(self) -> Counter:
        return Counter(s.item for s in self.steps if s.item)

    @property
    def targets(self) -> list[str]:
        return [s.to for s in self.steps]


@dataclass
class FamilyPlan:
    family: str
    buddy_km: float
    cost_per_candy: float
    candy_owned: int
    candy_used: int
    rare_candy: int
    value: float
    paths: list[Path]

    @property
    def km_saved(self) -> float:
        return self.rare_candy * self.buddy_km


@dataclass
class Plan:
    rare_candy_available: int
    rare_candy_used: int
    total_value: float
    families: list[FamilyPlan]
    trade_instead: list[tuple[Specimen, Step]] = field(default_factory=list)
    unknown_species: list[str] = field(default_factory=list)


@dataclass
class Settings:
    dex_bonus: float = 20.0          # km-equivalent value of a new Pokédex entry
    allow_trade_evolutions: bool = False  # spend candy on evolutions that are free via trade
    max_specimens_per_family: int = 6
    rare_candy_blocked_families: frozenset = frozenset()


def _items_key(c: Counter) -> tuple:
    return tuple(sorted((k, v) for k, v in c.items() if v))


def _fits(items: Counter, have: dict[str, int]) -> bool:
    return all(have.get(k, 0) >= v for k, v in items.items())


def cost_per_candy(family: str, species: dict, tiers: dict) -> tuple[float, float]:
    base = species.get(family, {})
    km = float(base.get("buddy_km", 1))
    tier = tiers.get("families", {}).get(family)
    if tier is None:
        tier = "legendary" if base.get("rarity", "standard") != "standard" else "common"
    return km, km * tiers["multipliers"][tier]


def enumerate_paths(spec: Specimen, species: dict, settings: Settings,
                    trade_out: list) -> list[Path]:
    """All evolution paths from a specimen, including doing nothing."""
    paths: list[Path] = []

    def walk(current: str, steps: tuple[Step, ...]):
        paths.append(Path(spec, steps))
        for evo in species.get(current, {}).get("evolutions", []):
            step = Step(current, evo["to"], int(evo["candy"]), evo.get("item"), tuple(evo.get("notes", [])))
            if evo.get("trade_free") and not settings.allow_trade_evolutions:
                trade_out.append((spec, step))
                continue
            walk(evo["to"], steps + (step,))

    walk(spec.species, ())
    return paths


def family_options(family: str, specimens: list[Specimen], species: dict, inv: Inventory,
                   cpc: float, settings: Settings, trade_out: list) -> list[tuple]:
    """Return options (candy, items_key, value, paths) for one family."""
    max_candy = inv.candy.get(family, 0) + inv.rare_candy
    # state key: (candy, new_dex_targets, items_key) -> (value, paths)
    states: dict[tuple, tuple[float, list[Path]]] = {(0, frozenset(), ()): (0.0, [])}
    for spec in specimens:
        options = enumerate_paths(spec, species, settings, trade_out)
        nxt: dict[tuple, tuple[float, list[Path]]] = {}
        for (candy, dex, ikey), (val, chosen) in states.items():
            for p in options:
                c = candy + p.candy
                if c > max_candy:
                    continue
                items = Counter(dict(ikey)) + p.items
                if not _fits(items, inv.items):
                    continue
                new = {t for t in p.targets if t not in inv.pokedex} - dex
                v = val + p.candy * cpc + settings.dex_bonus * len(new)
                key = (c, dex | new, _items_key(items))
                if key not in nxt or v > nxt[key][0]:
                    nxt[key] = (v, chosen + ([p] if p.steps else []))
        states = nxt
    # collapse dex sets: keep best value per (candy, items)
    best: dict[tuple, tuple[float, list[Path]]] = {}
    for (c, _dex, ikey), (v, chosen) in states.items():
        if (c, ikey) not in best or v > best[(c, ikey)][0]:
            best[(c, ikey)] = (v, chosen)
    return [(c, ikey, v, chosen) for (c, ikey), (v, chosen) in best.items()]


def plan(species: dict, tiers: dict, inv: Inventory, settings: Settings | None = None) -> Plan:
    settings = settings or Settings()
    trade_out: list[tuple[Specimen, Step]] = []
    unknown = sorted({s.species for s in inv.pokemon if s.species not in species})

    by_family: dict[str, list[Specimen]] = {}
    for s in inv.pokemon:
        if s.species in species:
            by_family.setdefault(species[s.species]["family"], []).append(s)

    family_data = {}
    for fam, specs in by_family.items():
        # best specimens first (IV, then input order); cap to keep the DP small
        specs = sorted(specs, key=lambda s: -(s.iv_pct or 0))[: settings.max_specimens_per_family]
        km, cpc = cost_per_candy(fam, species, tiers)
        owned = inv.candy.get(fam, 0)
        opts = family_options(fam, specs, species, inv, cpc, settings, trade_out)
        blocked = fam in settings.rare_candy_blocked_families
        # option -> rare candy needed
        opts = [(max(0, c - owned), c, ikey, v, paths) for c, ikey, v, paths in opts
                if not (blocked and c > owned)]
        family_data[fam] = (km, cpc, owned, opts)

    # grouped knapsack across families: key (rare_used, items_key) -> (value, picks)
    dp: dict[tuple, tuple[float, dict]] = {(0, ()): (0.0, {})}
    for fam, (_km, _cpc, _owned, opts) in family_data.items():
        nxt: dict[tuple, tuple[float, dict]] = {}
        for (r, ikey), (val, picks) in dp.items():
            for opt in opts:
                rr = r + opt[0]
                if rr > inv.rare_candy:
                    continue
                items = Counter(dict(ikey)) + Counter(dict(opt[2]))
                if not _fits(items, inv.items):
                    continue
                key = (rr, _items_key(items))
                v = val + opt[3]
                if key not in nxt or v > nxt[key][0]:
                    nxt[key] = (v, {**picks, fam: opt})
        dp = nxt

    (r_used, _), (total, picks) = max(dp.items(), key=lambda kv: (kv[1][0], -kv[0][0]))

    families = []
    for fam, (rare, candy, _ikey, value, paths) in picks.items():
        if not paths:
            continue
        km, cpc, owned, _ = family_data[fam]
        families.append(FamilyPlan(fam, km, cpc, owned, candy, rare, value, paths))
    families.sort(key=lambda f: (-f.rare_candy, -f.value))

    # de-duplicate trade suggestions per specimen/step
    seen, trades = set(), []
    for spec, step in trade_out:
        k = (id(spec), step.to)
        if k not in seen:
            seen.add(k)
            trades.append((spec, step))
    return Plan(inv.rare_candy, r_used, total, families, trades, unknown)
