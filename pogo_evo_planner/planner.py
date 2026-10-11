"""Decide which evolutions to do and where to spend rare candy.

Model
-----
* Every family has a cost per candy, in "km-equivalent effort":
      cost_per_candy = buddy_km * rarity_multiplier
  Buddy km is how far you walk per candy; the rarity multiplier stands in for
  how hard the species is to catch (which the game data doesn't expose).
* An evolution is worth   candy_cost * cost_per_candy   (the effort it would
  take to farm that candy), plus a bonus if it registers a new Pokédex entry.
  That full value applies only when it gives you a species you don't own yet,
  and it is the same however the species is reached: the candy to evolve it
  from the first stage of its family. So evolving an owned middle stage beats
  starting again from the first stage. A repeat keeps `repeat_value` of the
  candy it spends, and only uses own candy left over after the new evolutions,
  so it never costs Rare Candy. Rare Candy is never spent just to use it up.
* Owning a species also covers every earlier stage of it (owning Meowscarada
  means Floragato isn't new), and all of those count as registered in the Pokédex.
  A plain name like MAUSHOLD covers all its forms, since the storage grid never shows forms.
* Rare candy converts 1:1 into any family's candy, and a family's candy is
  shared by all its members.

Optimisation
------------
1. Per family: keep the first specimen of each species (one per unowned final
   evolution when the line branches, like Eevee), enumerate their evolution paths
   (including multi-step paths and branches) and run a small DP over them. The
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
        """Total candy this path costs."""
        return sum(s.candy for s in self.steps)

    @property
    def items(self) -> Counter:
        """Evolution items this path consumes, counted."""
        return Counter(s.item for s in self.steps if s.item)

    @property
    def targets(self) -> list[str]:
        """Species reached along this path, in order."""
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
        """Buddy walking distance the Rare Candy in this family stands in for."""
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
    repeat_value: float = 0.0        # share of value kept by an evolution into a species you already own
    allow_trade_evolutions: bool = False  # spend candy on evolutions that are free via trade
    rare_candy_blocked_families: frozenset = frozenset()


def _items_key(c: Counter) -> tuple:
    """Hashable, order-independent key for a counter of items, dropping zero counts."""
    return tuple(sorted((k, v) for k, v in c.items() if v))


def _fits(items: Counter, have: dict[str, int]) -> bool:
    """True when the player owns enough of every item in the counter."""
    return all(have.get(k, 0) >= v for k, v in items.items())


def cost_per_candy(family: str, species: dict, tiers: dict) -> tuple[float, float]:
    """Return (buddy km, km-equivalent cost per candy) for a family, from its buddy distance and rarity tier."""
    base = species.get(family, {})
    km = float(base.get("buddy_km", 1))
    tier = tiers.get("families", {}).get(family)
    if tier is None:
        tier = "legendary" if base.get("rarity", "standard") != "standard" else "common"
    return km, km * tiers["multipliers"][tier]


def candy_from_base(species: dict) -> dict[str, int]:
    """Candy needed to evolve each species from the first stage of its family (cheapest route)."""
    cost: dict[str, int] = {}

    def walk(current: str, spent: int):
        """Record the candy spent to reach this species, then continue down its evolutions."""
        if current in cost and cost[current] <= spent:
            return
        cost[current] = spent
        for evo in species.get(current, {}).get("evolutions", []):
            walk(evo["to"], spent + int(evo["candy"]))

    for key, data in species.items():
        if data.get("family") == key:
            walk(key, 0)
    return cost


def with_forms(have: set[str], species: dict) -> set[str]:
    """Plain species names plus every form sharing their dex number: owning MAUSHOLD covers MAUSHOLD_FAMILY_OF_FOUR.

    The storage grid shows only the name, never the form, so a plain name stands for any form.
    A specific form (OINKOLOGNE_FEMALE) covers only itself.
    """
    by_dex: dict[int, list[str]] = {}
    for key, data in species.items():
        if "dex" in data:
            by_dex.setdefault(data["dex"], []).append(key)
    out = set(have)
    for s in have:
        same = by_dex.get(species.get(s, {}).get("dex"), [])
        if not any(o != s and s.startswith(o + "_") for o in same):  # s isn't itself a form of another key
            out.update(o for o in same if o == s or o.startswith(s + "_"))
    return out


def with_earlier_stages(have: set[str], species: dict) -> set[str]:
    """The given species plus every stage that evolves into them: owning Meowscarada covers Floragato."""
    earlier: dict[str, set[str]] = {}
    for key, data in species.items():
        for evo in data.get("evolutions", []):
            earlier.setdefault(evo["to"], set()).add(key)
    out, todo = set(have), list(have)
    while todo:
        for prev in earlier.get(todo.pop(), ()):
            if prev not in out:
                out.add(prev)
                todo.append(prev)
    return out


def owned_species(inv: Inventory, species: dict) -> set[str]:
    """Species the player has, counting all forms of a plain name and every earlier stage.

    A silhouette on an EVOLVE button beats a storage reading: that species was never caught.
    """
    return with_earlier_stages(with_forms({s.species for s in inv.pokemon} - inv.not_caught, species), species)


def families_missing_candy(inv: Inventory, species: dict) -> list[str]:
    """Families with no candy count where it matters: an owned member can still evolve into something new."""
    owned = owned_species(inv, species)

    def reaches_new(sp: str) -> bool:
        """True when some evolution from this species, at any depth, isn't owned yet."""
        return any(e["to"] not in owned or reaches_new(e["to"]) for e in species.get(sp, {}).get("evolutions", []))

    return sorted({species[s.species]["family"] for s in inv.pokemon
                   if s.species in species and species[s.species]["family"] not in inv.candy
                   and reaches_new(s.species)})


def end_stages(sp: str, species: dict) -> set[str]:
    """Final evolutions reachable from a species: {GARDEVOIR, GALLADE} for Kirlia, {sp} when it doesn't evolve."""
    evos = species.get(sp, {}).get("evolutions", [])
    return set().union(*(end_stages(e["to"], species) for e in evos)) if evos else {sp}


def keep_specimens(specs: list[Specimen], species: dict,
                   owned: set[str] | frozenset[str] = frozenset()) -> list[Specimen]:
    """The specimens worth planning for, in input order: the first of each species, and for a species
    whose line branches (Eevee, Kirlia) one per final evolution not owned yet, so each copy can take a
    different branch.
    """
    kept: dict[str, list[Specimen]] = {}
    for s in specs:
        copies = kept.setdefault(s.species, [])
        if len(copies) < max(1, len(end_stages(s.species, species) - owned)):
            copies.append(s)
    keep = {id(s) for copies in kept.values() for s in copies}  # by identity: two "CP ?" Eevees are equal
    return [s for s in specs if id(s) in keep]


def enumerate_paths(spec: Specimen, species: dict, settings: Settings,
                    trade_out: list) -> list[Path]:
    """All evolution paths from a specimen, including doing nothing."""
    paths: list[Path] = []

    def walk(current: str, steps: tuple[Step, ...]):
        """Record the path so far, then recurse into each evolution of the current species."""
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
                   cpc: float, settings: Settings, trade_out: list,
                   from_base: dict[str, int] | None = None) -> list[tuple]:
    """Return options (candy, items_key, value, paths) for one family."""
    from_base = from_base if from_base is not None else candy_from_base(species)
    own_candy = inv.candy.get(family, 0)
    max_candy = own_candy + inv.rare_candy
    owned = owned_species(inv, species)
    registered = (inv.pokedex | owned) - inv.not_caught
    # state key: (candy, repeat_candy, new_dex_targets, new_species_claimed, items_key) -> (value, paths)
    states: dict[tuple, tuple[float, list[Path]]] = {(0, 0, frozenset(), frozenset(), ()): (0.0, [])}
    for spec in specimens:
        options = enumerate_paths(spec, species, settings, trade_out)
        nxt: dict[tuple, tuple[float, list[Path]]] = {}
        for (candy, rep, dex, firsts, ikey), (val, chosen) in states.items():
            for p in options:
                c = candy + p.candy
                if c > max_candy:
                    continue
                items = Counter(dict(ikey)) + p.items
                if not _fits(items, inv.items):
                    continue
                new = {t for t in p.targets if t not in registered} - dex
                base = val + settings.dex_bonus * len(new)
                final = p.targets[-1] if p.steps else None
                moves = []
                # a repeat (a species you already have) may only use your own candy, never Rare Candy
                if rep + p.candy <= own_candy:
                    moves.append((base + p.candy * cpc * settings.repeat_value, rep + p.candy, firsts))
                if final and final not in owned and final not in firsts:
                    # a new species is worth the same however it is reached, so the cheapest route wins
                    moves.append((base + from_base.get(final, p.candy) * cpc, rep, firsts | {final}))
                for v, r, f in moves:
                    key = (c, r, dex | new, f, _items_key(items))
                    if key not in nxt or v > nxt[key][0]:
                        nxt[key] = (v, chosen + ([p] if p.steps else []))
        states = nxt
    # collapse repeat candy and dex and species sets: keep best value per (candy, items)
    best: dict[tuple, tuple[float, list[Path]]] = {}
    for (c, rep, _dex, _firsts, ikey), (v, chosen) in states.items():
        if rep > max(0, own_candy - (c - rep)):
            continue  # repeats may only use own candy the new evolutions leave over, so they cost no Rare Candy
        if (c, ikey) not in best or v > best[(c, ikey)][0]:
            best[(c, ikey)] = (v, chosen)
    return [(c, ikey, v, chosen) for (c, ikey), (v, chosen) in best.items()]


def plan(species: dict, tiers: dict, inv: Inventory, settings: Settings | None = None) -> Plan:
    """Choose evolutions and Rare Candy spending that maximize total value across all families."""
    settings = settings or Settings()
    trade_out: list[tuple[Specimen, Step]] = []
    unknown = sorted({s.species for s in inv.pokemon if s.species not in species})

    by_family: dict[str, list[Specimen]] = {}
    for s in inv.pokemon:
        if s.species in species:
            by_family.setdefault(species[s.species]["family"], []).append(s)

    from_base = candy_from_base(species)
    have = owned_species(inv, species)
    family_data = {}
    for fam, specs in by_family.items():
        specs = keep_specimens(specs, species, have)
        km, cpc = cost_per_candy(fam, species, tiers)
        owned = inv.candy.get(fam, 0)
        opts = family_options(fam, specs, species, inv, cpc, settings, trade_out, from_base)
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
