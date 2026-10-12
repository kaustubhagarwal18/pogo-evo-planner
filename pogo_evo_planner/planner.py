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
from collections.abc import Callable
from dataclasses import dataclass, field

from .gamemaster import evolution_items
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
        return sum(step.candy for step in self.steps)

    @property
    def items(self) -> Counter:
        """Evolution items this path consumes, counted."""
        return Counter(step.item for step in self.steps if step.item)

    @property
    def targets(self) -> list[str]:
        """Species reached along this path, in order."""
        return [step.to for step in self.steps]


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
    no_candy_count: list[str] = field(default_factory=list)  # families that needed Rare Candy but have no count
    unknown_items: list[str] = field(default_factory=list)  # item keys no evolution needs, as written


@dataclass
class Settings:
    dex_bonus: float = 20.0  # km-equivalent value of a new Pokédex entry
    repeat_value: float = 0.0  # share of value kept by an evolution into a species you already own
    allow_trade_evolutions: bool = False  # spend candy on evolutions that are free via trade
    new_entries_only: bool = False  # only plan evolutions into species never caught (`evolvenew`)
    rare_candy_blocked_families: frozenset = frozenset()  # on top of every family with no candy count


def _items_key(counts: Counter) -> tuple:
    """Hashable, order-independent key for a counter of items, dropping zero counts."""
    return tuple(sorted((item, count) for item, count in counts.items() if count))


def _fits(items: Counter, have: dict[str, int]) -> bool:
    """True when the player owns enough of every item in the counter."""
    return all(have.get(item, 0) >= count for item, count in items.items())


def cost_per_candy(family: str, species: dict, tiers: dict) -> tuple[float, float]:
    """Return (buddy km, km-equivalent cost per candy) for a family, from its buddy distance and rarity tier."""
    base = species.get(family, {})
    buddy_km = float(base.get("buddy_km", 1))
    tier = tiers.get("families", {}).get(family)
    if tier is None:
        tier = "legendary" if base.get("rarity", "standard") != "standard" else "common"
    return buddy_km, buddy_km * tiers["multipliers"][tier]


def candy_from_base(species: dict) -> dict[str, int]:
    """Candy needed to evolve each species from the first stage of its family (cheapest route)."""
    cost: dict[str, int] = {}

    def walk(current: str, spent: int):
        """Record the candy spent to reach this species, then continue down its evolutions."""
        if current in cost and cost[current] <= spent:
            return
        cost[current] = spent
        for evolution in species.get(current, {}).get("evolutions", []):
            walk(evolution["to"], spent + int(evolution["candy"]))

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
    covered = set(have)
    for species_id in have:
        same = by_dex.get(species.get(species_id, {}).get("dex"), [])
        if not any(
            other_id != species_id and species_id.startswith(other_id + "_") for other_id in same
        ):  # species_id isn't itself a form of another key
            covered.update(
                other_id for other_id in same if other_id == species_id or other_id.startswith(species_id + "_")
            )
    return covered


def with_earlier_stages(have: set[str], species: dict) -> set[str]:
    """The given species plus every stage that evolves into them: owning Meowscarada covers Floragato."""
    earlier: dict[str, set[str]] = {}
    for key, data in species.items():
        for evolution in data.get("evolutions", []):
            earlier.setdefault(evolution["to"], set()).add(key)
    covered, todo = set(have), list(have)
    while todo:
        for prev in earlier.get(todo.pop(), ()):
            if prev not in covered:
                covered.add(prev)
                todo.append(prev)
    return covered


def owned_species(inventory: Inventory, species: dict) -> set[str]:
    """Species the player has, counting all forms of a plain name and every earlier stage.

    A silhouette on an EVOLVE button beats a storage reading: that species was never caught.
    """
    return with_earlier_stages(
        with_forms({specimen.species for specimen in inventory.pokemon} - inventory.not_caught, species), species
    )


def gives_new_species(inventory: Inventory, species: dict, settings: Settings) -> Callable[[str], bool]:
    """A test for whether evolving into a species gives the player one they don't have, which is what earns an
    evolution its full value: a species not owned, or with new_entries_only (evolvenew) one the Pokédex hasn't
    registered. There the game's own list of never-caught species decides, even where owning a later stage would
    suggest an earlier one was caught (Victreebel can be caught without ever having a Weepinbell)."""
    owned = owned_species(inventory, species)
    if not settings.new_entries_only:
        return lambda species_id: species_id not in owned
    registered = (inventory.pokedex | owned) - inventory.not_caught
    return lambda species_id: species_id not in registered


def families_missing_candy(inventory: Inventory, species: dict) -> list[str]:
    """Families with no candy count where it matters: an owned member can still evolve into something new."""
    owned = owned_species(inventory, species)

    def reaches_new(species_id: str) -> bool:
        """True when some evolution from this species, at any depth, isn't owned yet."""
        return any(
            evolution["to"] not in owned or reaches_new(evolution["to"])
            for evolution in species.get(species_id, {}).get("evolutions", [])
        )

    return sorted(
        {
            species[specimen.species]["family"]
            for specimen in inventory.pokemon
            if specimen.species in species
            and species[specimen.species]["family"] not in inventory.candy
            and reaches_new(specimen.species)
        }
    )


def end_stages(species_id: str, species: dict) -> set[str]:
    """Final evolutions reachable from a species: {GARDEVOIR, GALLADE} for Kirlia, {sp} when it doesn't evolve."""
    evolutions = species.get(species_id, {}).get("evolutions", [])
    return (
        set().union(*(end_stages(evolution["to"], species) for evolution in evolutions)) if evolutions else {species_id}
    )


def keep_specimens(
    specimens: list[Specimen], species: dict, owned: set[str] | frozenset[str] = frozenset()
) -> list[Specimen]:
    """The specimens worth planning for, in input order: the first of each species, and for a species
    whose line branches (Eevee, Kirlia) one per final evolution not owned yet, so each copy can take a
    different branch.
    """
    kept: dict[str, list[Specimen]] = {}
    for specimen in specimens:
        copies = kept.setdefault(specimen.species, [])
        if len(copies) < max(1, len(end_stages(specimen.species, species) - owned)):
            copies.append(specimen)
    keep = {id(specimen) for copies in kept.values() for specimen in copies}  # by identity: two "CP ?" Eevees are equal
    return [specimen for specimen in specimens if id(specimen) in keep]


def enumerate_paths(specimen: Specimen, species: dict, settings: Settings, trade_out: list) -> list[Path]:
    """All evolution paths from a specimen, including doing nothing."""
    paths: list[Path] = []

    def walk(current: str, steps: tuple[Step, ...]):
        """Record the path so far, then recurse into each evolution of the current species."""
        paths.append(Path(specimen, steps))
        for evolution in species.get(current, {}).get("evolutions", []):
            step = Step(
                current,
                evolution["to"],
                int(evolution["candy"]),
                evolution.get("item"),
                tuple(evolution.get("notes", [])),
            )
            if evolution.get("trade_free") and not settings.allow_trade_evolutions:
                trade_out.append((specimen, step))
                continue
            walk(evolution["to"], steps + (step,))

    walk(specimen.species, ())
    return paths


def family_options(
    family: str,
    specimens: list[Specimen],
    species: dict,
    inventory: Inventory,
    candy_value: float,
    settings: Settings,
    trade_out: list,
    from_base: dict[str, int] | None = None,
) -> list[tuple]:
    """Return options (candy, items_key, value, paths) for one family."""
    from_base = from_base if from_base is not None else candy_from_base(species)
    own_candy = inventory.candy.get(family, 0)
    max_candy = own_candy + inventory.rare_candy
    registered = (inventory.pokedex | owned_species(inventory, species)) - inventory.not_caught
    is_new = gives_new_species(inventory, species, settings)

    def entry(species_id: str):
        """What counts as one new species: with new_entries_only, a Pokédex entry, so the forms of one
        species (Dudunsparce's two and three segments, Urshifu's two styles) are claimed only once."""
        return species.get(species_id, {}).get("dex", species_id) if settings.new_entries_only else species_id

    # state key: (candy, repeat_candy, new_dex_targets, new_species_claimed, items_key) -> (value, paths)
    states: dict[tuple, tuple[float, list[Path]]] = {(0, 0, frozenset(), frozenset(), ()): (0.0, [])}
    for specimen in specimens:
        options = enumerate_paths(specimen, species, settings, trade_out)
        next_states: dict[tuple, tuple[float, list[Path]]] = {}
        for (candy, repeat_candy, dex, firsts, items_key), (state_value, chosen) in states.items():
            for path in options:
                total_candy = candy + path.candy
                if total_candy > max_candy:
                    continue
                items = Counter(dict(items_key)) + path.items
                if not _fits(items, inventory.items):
                    continue
                new = {entry(target) for target in path.targets if target not in registered} - dex
                base = state_value + settings.dex_bonus * len(new)
                final = path.targets[-1] if path.steps else None
                moves = []
                # a repeat (a species you already have) may only use your own candy, never Rare Candy
                if repeat_candy + path.candy <= own_candy:
                    moves.append(
                        (base + path.candy * candy_value * settings.repeat_value, repeat_candy + path.candy, firsts)
                    )
                if final and is_new(final) and entry(final) not in firsts:
                    # a new species is worth the same however it is reached, so the cheapest route wins
                    moves.append(
                        (base + from_base.get(final, path.candy) * candy_value, repeat_candy, firsts | {entry(final)})
                    )
                for value, total_repeat, claimed in moves:
                    key = (total_candy, total_repeat, dex | new, claimed, _items_key(items))
                    if key not in next_states or value > next_states[key][0]:
                        next_states[key] = (value, chosen + ([path] if path.steps else []))
        states = next_states
    # collapse repeat candy and dex and species sets: keep best value per (candy, items)
    best: dict[tuple, tuple[float, list[Path]]] = {}
    for (total_candy, repeat_candy, _dex, _firsts, items_key), (value, chosen) in states.items():
        if repeat_candy > max(0, own_candy - (total_candy - repeat_candy)):
            continue  # repeats may only use own candy the new evolutions leave over, so they cost no Rare Candy
        if (total_candy, items_key) not in best or value > best[(total_candy, items_key)][0]:
            best[(total_candy, items_key)] = (value, chosen)
    return [(total_candy, items_key, value, chosen) for (total_candy, items_key), (value, chosen) in best.items()]


def plan(species: dict, tiers: dict, inventory: Inventory, settings: Settings | None = None) -> Plan:
    """Choose evolutions and Rare Candy spending that maximize total value across all families."""
    settings = settings or Settings()
    trade_out: list[tuple[Specimen, Step]] = []
    unknown = sorted({specimen.species for specimen in inventory.pokemon if specimen.species not in species})

    by_family: dict[str, list[Specimen]] = {}
    for specimen in inventory.pokemon:
        if specimen.species in species:
            by_family.setdefault(species[specimen.species]["family"], []).append(specimen)

    from_base = candy_from_base(species)
    have = owned_species(inventory, species)
    family_data = {}
    held_back = []
    for family, specimens in by_family.items():
        specimens = keep_specimens(specimens, species, have)
        buddy_km, candy_value = cost_per_candy(family, species, tiers)
        owned = inventory.candy.get(family, 0)
        options = family_options(family, specimens, species, inventory, candy_value, settings, trade_out, from_base)
        # a missing candy count is unknown, not 0: Rare Candy would be guessed to cover the whole cost
        blocked = family in settings.rare_candy_blocked_families or family not in inventory.candy
        if blocked and any(candy > owned for candy, *_ in options):
            held_back.append(family)
        # option -> rare candy needed
        options = [
            (max(0, candy - owned), candy, items_key, value, paths)
            for candy, items_key, value, paths in options
            if not (blocked and candy > owned)
        ]
        family_data[family] = (buddy_km, candy_value, owned, options)

    # grouped knapsack across families: key (rare_used, items_key) -> (value, picks)
    states: dict[tuple, tuple[float, dict]] = {(0, ()): (0.0, {})}
    for family, (_buddy_km, _candy_value, _owned, options) in family_data.items():
        next_states: dict[tuple, tuple[float, dict]] = {}
        for (rare_spent, items_key), (state_value, picks) in states.items():
            for option in options:
                total_rare = rare_spent + option[0]
                if total_rare > inventory.rare_candy:
                    continue
                items = Counter(dict(items_key)) + Counter(dict(option[2]))
                if not _fits(items, inventory.items):
                    continue
                key = (total_rare, _items_key(items))
                value = state_value + option[3]
                if key not in next_states or value > next_states[key][0]:
                    next_states[key] = (value, {**picks, family: option})
        states = next_states

    (rare_used, _), (total, picks) = max(states.items(), key=lambda state: (state[1][0], -state[0][0]))

    families = []
    for family, (rare, candy, _option_items, value, paths) in picks.items():
        if not paths:
            continue
        buddy_km, candy_value, owned, _ = family_data[family]
        families.append(FamilyPlan(family, buddy_km, candy_value, owned, candy, rare, value, paths))
    families.sort(key=lambda family_plan: (-family_plan.rare_candy, -family_plan.value))

    # de-duplicate trade suggestions per specimen/step
    seen, trades = set(), []
    for specimen, step in trade_out:
        trade_key = (id(specimen), step.to)
        if trade_key not in seen:
            seen.add(trade_key)
            trades.append((specimen, step))
    needed = evolution_items(species)
    return Plan(
        inventory.rare_candy,
        rare_used,
        total,
        families,
        trades,
        unknown,
        sorted(held_back),
        sorted(trade_key for trade_key in inventory.items if trade_key not in needed),
    )
