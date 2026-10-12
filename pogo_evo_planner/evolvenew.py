"""Plan from the game's own `evolvenew` search.

Searching `evolvenew` in Pokémon storage lists every Pokémon you own whose evolution would be a new
Pokédex entry, including ones you can't afford yet. So instead of scanning a whole collection, the
player screenshots (or records a swipe through) just those Pokémon's detail pages, adds their Rare
Candy count, and gets the plan:

- each detail page shows a Pokémon you own and its family's candy;
- its evolution is new (that's what `evolvenew` means), unless the Evolve button's sprite clearly
  shows it was caught;
- a Pokémon with no candy evolution is only listed for its Mega Evolution, which uses Mega Energy and
  isn't planned.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

from .gamemaster import evolution_items, item_id, item_name
from .inventory import Inventory
from .planner import Plan


@dataclass
class Listing:
    """What the `evolvenew` Pokémon tell us, beyond the inventory itself."""

    mega_only: list[str] = field(default_factory=list)  # final forms: listed for their Mega
    already_caught: list[str] = field(default_factory=list)  # evolutions whose sprite shows caught
    already_held: list[str] = field(default_factory=list)  # evolutions you already have one of
    notes: list[str] = field(default_factory=list)


def apply_evolvenew(inventory: dict, species: dict) -> tuple[dict, Listing]:
    """Mark the evolutions of every listed Pokémon as never caught, unless a sprite showed them caught.

    inventory is an inventory dict as `scan` writes it; returns an updated copy and what else was learnt.
    """
    updated = {
        **inventory,
        "not_caught": list(inventory.get("not_caught", [])),
        "pokedex": list(inventory.get("pokedex", [])),
    }
    held = {pokemon["species"] for pokemon in inventory.get("pokemon", [])}
    caught, never = set(updated["pokedex"]) | held, set(updated["not_caught"]) - held  # you can't miss what you hold
    listing = Listing()
    for species_id in sorted(
        {pokemon["species"] for pokemon in inventory.get("pokemon", []) if pokemon["species"] in species}
    ):
        targets = [evolution["to"] for evolution in species[species_id].get("evolutions", [])]
        if not targets:
            listing.mega_only.append(species_id)
            continue
        new = [target for target in targets if target not in caught]
        listing.already_held += [target for target in targets if target in held]
        listing.already_caught += [target for target in targets if target in caught and target not in held]
        if len(targets) > 1 and len(new) < len(targets):
            listing.notes.append(
                f"{_name(species_id)}: only {', '.join(map(_name, new)) or 'none'} of its evolutions can be new"
            )
        never |= set(new)
    updated["not_caught"] = sorted(never)
    return updated, listing


def _name(species_id: str) -> str:
    """A species id as players write it: NACLSTACK -> Naclstack, MR_MIME -> Mr Mime."""
    return species_id.replace("_", " ").title()


def _needs(steps: list[tuple[str | None, list[str]]]) -> str:
    """The extra requirements of an evolution's steps (item, notes), in words, or ''."""
    extras = [extra for item, notes in steps for extra in ([f"needs {item_name(item)}"] if item else []) + list(notes)]
    return f" ({'; '.join(dict.fromkeys(extras))})" if extras else ""


def _dex(species_id: str, species: dict) -> int | str:
    """The Pokédex number a species or form counts toward (forms share one entry)."""
    return species.get(species_id, {}).get("dex", species_id)


def missing(inventory: Inventory, species: dict) -> tuple[list[tuple[str, str, int]], list[str]]:
    """The species you're missing: (species, the Pokémon you own that evolves into it, candy) for each one
    you can evolve into, and the rest of the never-caught list to catch. One row per Pokédex entry, so a
    species with forms (Dudunsparce's two and three segments) is listed once, by its cheapest route."""
    held = {specimen.species for specimen in inventory.pokemon}
    evolve: dict[int | str, tuple[str, str, int]] = {}
    for species_id in sorted(held):
        for evolution in species.get(species_id, {}).get("evolutions", []):
            target, cost = evolution["to"], int(evolution["candy"])
            key = _dex(target, species)
            if target in inventory.not_caught and target not in held and (key not in evolve or cost < evolve[key][2]):
                evolve[key] = (target, species_id, cost)
    catch = sorted(
        {
            species_id
            for species_id in inventory.not_caught
            if species_id not in held and _dex(species_id, species) not in evolve
        }
    )
    return sorted(evolve.values()), catch


def _label(species_id: str, inventory: Inventory, species: dict) -> str:
    """A missing species as players know it: forms of one Pokédex entry are named once, with the forms."""
    base = next(
        (
            other_id
            for other_id, other_entry in species.items()
            if other_entry.get("dex") == _dex(species_id, species) and "_" not in other_id
        ),
        None,
    )
    # forms in the species table's order (Two before Three), leaving out the plain base name
    forms = [
        form_id
        for form_id in species
        if form_id != base
        and _dex(form_id, species) == _dex(species_id, species)
        and (form_id == species_id or form_id in inventory.not_caught)
    ]
    if len(forms) < 2 or base is None:
        return _name(species_id)
    return f"{_name(base)} ({' or '.join(_name(form_id).removeprefix(_name(base)).strip() for form_id in forms)})"


def _evolution(source: str, target: str, species: dict) -> dict:
    """The evolution step from one species to another, as the species table records it."""
    return next(evolution for evolution in species[source]["evolutions"] if evolution["to"] == target)


def unknown_candy(inventory: Inventory, species: dict) -> frozenset[str]:
    """Families with a missing species to evolve into but no candy count: never spend Rare Candy on them."""
    return frozenset(
        species[source]["family"]
        for _, source, _ in missing(inventory, species)[0]
        if species[source]["family"] not in inventory.candy
    )


def render(
    evolution_plan: Plan,
    inventory: Inventory,
    species: dict,
    listing: Listing,
    show_missing: bool = True,
    show_plan: bool = True,
) -> str:
    """Which species you're missing, then the plan in plain words: evolve now, use Rare Candy, not yet.

    The command asks for missing candy counts in between, so it prints the two parts separately;
    the closing notes go with the plan, at the end.
    """
    output = _render_missing(inventory, species, listing) if show_missing else []
    if show_plan:
        output += _render_plan(evolution_plan, inventory, species) + _render_notes(listing)
    return "\n".join(output).rstrip() + "\n"


def _render_missing(inventory: Inventory, species: dict, listing: Listing) -> list[str]:
    """What you're missing: species to evolve into, species to catch, and Mega-only Pokémon."""
    evolve, catch = missing(inventory, species)
    output = [f"You're missing {len(evolve) + len(catch)} species.", ""]
    if evolve:
        output.append("Evolve into these (from Pokémon you own):")
        width = max(len(_label(target, inventory, species)) for target, _, _ in evolve) + 2
        for target, source, cost in evolve:
            have = inventory.candy.get(species[source]["family"])
            candy = "candy ?" if have is None else f"have {have}"
            evolution = _evolution(source, target, species)
            needs = _needs([(evolution.get("item"), evolution.get("notes", []))])
            output.append(
                f"  {_label(target, inventory, species):{width}}from {_name(source):14} {cost:4} candy, {candy}{needs}"
            )
        output.append("")
    if catch:
        output += [
            "Catch these (seen, but nothing you own evolves into them):",
            "  " + ", ".join(map(_name, catch)),
            "",
        ]
    if listing.mega_only:
        output += [
            f"Mega Evolution only ({len(listing.mega_only)}, need Mega Energy, not planned):",
            "  " + ", ".join(map(_name, listing.mega_only)),
            "",
        ]
    return output


def _render_plan(evolution_plan: Plan, inventory: Inventory, species: dict) -> list[str]:
    """The Rare Candy plan: evolve now, use Rare Candy, not yet, and the candy counts still needed."""
    planned: set[str] = set()
    now, rare = [], []
    # best use of Rare Candy first: value per Rare Candy; families on their own candy by value
    for family_plan in sorted(
        evolution_plan.families,
        key=lambda family_plan: (
            -(family_plan.value / family_plan.rare_candy if family_plan.rare_candy else family_plan.value)
        ),
    ):
        lines = []
        for path in family_plan.paths:
            if not path.steps:
                continue
            planned.update(step.to for step in path.steps)
            chain = " → ".join([_name(path.steps[0].frm)] + [_name(step.to) for step in path.steps])
            lines.append(f"  {chain}{_needs([(step.item, list(step.notes)) for step in path.steps])}")
        if not family_plan.rare_candy:
            now += lines
        elif len(lines) == 1:  # Rare Candy is per family: inline for one evolution, a total for several
            rare.append(
                lines[0].replace(" (", f"  +{family_plan.rare_candy} Rare Candy (", 1)
                if " (" in lines[0]
                else f"{lines[0]}  +{family_plan.rare_candy} Rare Candy"
            )
        else:
            rare += lines + [f"    +{family_plan.rare_candy} Rare Candy for the {_name(family_plan.family)} family"]
    later = _not_yet(inventory, species, planned, evolution_plan)
    output = (
        [f"Rare Candy: {evolution_plan.rare_candy_used} of {evolution_plan.rare_candy_available} used", ""]
        if now or rare or later
        else []
    )
    for title, rows in (("Evolve now with your own candy:", now), ("Use Rare Candy:", rare), ("Not yet:", later)):
        if rows:
            output += [title, *rows, ""]
    if evolution_plan.trade_instead:
        output += [
            "Free by trading:",
            *sorted({f"  {_name(step.frm)} → {_name(step.to)}" for _, step in evolution_plan.trade_instead}),
            "",
        ]
    unknown = sorted(
        {
            (species[source]["family"], source)
            for _, source, _ in missing(inventory, species)[0]
            if species[source]["family"] not in inventory.candy
        }
    )
    if unknown:
        output += [
            (
                f"To plan your {evolution_plan.rare_candy_available} Rare Candy for the rest, "
                "add each family's candy count: "
                "screenshot one Pokémon's page per family, or use --candy NAME=N:"
            ),
            "  "
            + ", ".join(
                f"{_name(family)} (e.g. {_name(source)})" if family != source else _name(family)
                for family, source in unknown
            ),
            "",
        ]
    return output


def _render_notes(listing: Listing) -> list[str]:
    """Closing notes: evolutions listed but already held or caught, and branch notes."""
    output = []
    if listing.already_held:
        output += [
            "Listed by evolvenew, but you already have one (maybe a different form or costume):",
            "  " + ", ".join(map(_name, sorted(set(listing.already_held)))),
            "",
        ]
    if listing.already_caught:
        output += [
            "Already caught according to the Evolve button (not new):",
            "  " + ", ".join(map(_name, sorted(set(listing.already_caught)))),
            "",
        ]
    return output + [f"Note: {note}" for note in listing.notes]


def _not_yet(inventory: Inventory, species: dict, planned: set[str], evolution_plan: Plan) -> list[str]:
    """Why a missing species you could evolve into isn't in the plan: candy short, an item, or every copy
    already planned. Unknown candy counts are listed separately, as the next thing to add."""
    rows = []
    trade = {step.to for _, step in evolution_plan.trade_instead}
    planned_entries = {_dex(planned_id, species) for planned_id in planned}  # one form of a Pokédex entry is enough
    copies = {
        species_id: sum(specimen.species == species_id for specimen in inventory.pokemon)
        for species_id in {specimen.species for specimen in inventory.pokemon}
    }
    taken = {
        species_id: sum(
            path.specimen.species == species_id
            for family_plan in evolution_plan.families
            for path in family_plan.paths
            if path.steps
        )
        for species_id in copies
    }
    for species_id in sorted(copies):
        family = species.get(species_id, {}).get("family")
        for evolution in species.get(species_id, {}).get("evolutions", []):
            target = evolution["to"]
            if (
                target in planned
                or _dex(target, species) in planned_entries
                or target in trade
                or target not in inventory.not_caught
                or target in copies
            ):
                continue
            have, cost = inventory.candy.get(family), int(evolution["candy"])
            if have is None:
                continue
            if evolution.get("item") and inventory.items.get(evolution["item"], 0) < 1:
                why = (
                    f"needs {item_name(evolution['item'])}: "
                    f'if you have one, add --item "{item_name(evolution["item"])}=N"'
                )
            elif have < cost:
                why = f"{cost - have} candy short (have {have} of {cost})"
            elif taken[species_id] >= copies[species_id]:
                count = copies[species_id]
                why = (
                    f"your {count} {_name(species_id)} {'is' if count == 1 else 'are'} planned above; "
                    "if you have another, "
                    f"add a screenshot of its page"
                )
            else:
                why = "its candy is used by the picks above"
            rows.append(f"  {_name(species_id)} → {_name(target)}: {why}{_needs([(None, evolution.get('notes', []))])}")
    return rows


def ask_for_candy(
    inventory: Inventory,
    species: dict,
    ask: Callable[[str], str],
    read: Callable[[list[str]], dict[str, int]],
    say: Callable[[str], None],
) -> dict[str, int]:
    """Ask for each missing candy count: a number, a screenshot of that Pokémon's page, or Enter to skip.

    ask prompts and returns the answer (input), read OCRs screenshots into {family: candy}, say shows a
    message. Returns the counts given, {family: count}. "q", or the end of input (Ctrl-D), stops asking.
    """
    wanted: dict[str, list[str]] = {}
    for target, source, cost in missing(inventory, species)[0]:
        family = species[source]["family"]
        if family not in inventory.candy:
            wanted.setdefault(family, []).append(f"{_name(source)} → {_name(target)} needs {cost}")
    if not wanted:
        return {}
    needed = len(wanted)
    say(
        f"{needed} {'family needs' if needed == 1 else 'families need'} a candy count. "
        "For each, type the number, or the "
        "path of a screenshot "
        "of that Pokémon's page (you can drag the file in), or press Enter to skip; q stops."
    )
    answers: dict[str, int] = {}
    for family, uses in wanted.items():
        if family in answers:  # already read from an earlier screenshot
            continue
        for _ in range(3):
            try:
                reply = ask(f"{_name(family)} candy ({'; '.join(uses)}): ").strip()
            except EOFError:  # Ctrl-D, or input ran out: plan with what was given
                return answers
            if not reply:
                break
            if reply.lower() in ("q", "quit"):
                return answers
            if reply.isdigit():
                answers[family] = int(reply)
                break
            try:
                read_counts = read(shlex.split(reply))
            except (ValueError, OSError) as error:
                say(f"  couldn't read that: {error}")
                continue
            answers.update(
                {family: count for family, count in read_counts.items() if family in wanted}
            )  # any family it shows
            if family in read_counts:
                say(f"  read {read_counts[family]}")
                break
            say(f"  no {_name(family)} candy count on that screenshot; type the number instead, or another screenshot")
    return answers


def items_needed(inventory: Inventory, species: dict) -> dict[str, list[str]]:
    """Evolution items the missing species need and that haven't been given: {item id: [evolutions]}."""
    wanted: dict[str, list[str]] = {}
    for target, source, _ in missing(inventory, species)[0]:
        item = _evolution(source, target, species).get("item")
        if item and item not in inventory.items:
            wanted.setdefault(item, []).append(f"{_name(source)} → {_name(target)}")
    return wanted


def ask_for_items(
    inventory: Inventory, species: dict, ask: Callable[[str], str], say: Callable[[str], None]
) -> dict[str, int]:
    """Ask how many of each needed evolution item (apples, Sinnoh Stone, lures...) the player has.

    Enter means none; "q" or the end of input (Ctrl-D) stops asking. Returns {item id: count}.
    """
    wanted = items_needed(inventory, species)
    if not wanted:
        return {}
    needed = len(wanted)
    say(
        f"{needed} evolution {'item is' if needed == 1 else 'items are'} needed. For each, type how many you have "
        "(Enter if none; q stops)."
    )
    answers: dict[str, int] = {}
    for item, uses in wanted.items():
        for _ in range(3):
            try:
                reply = ask(f"{item_name(item)} ({'; '.join(uses)}): how many? ").strip()
            except EOFError:
                return answers
            if reply.lower() in ("q", "quit"):
                return answers
            if not reply or reply.isdigit():
                answers[item] = int(reply or 0)
                break
            say("  type a number, or press Enter if you have none")
    return answers


def candy_overrides(pairs: list[str], species: dict) -> dict[str, int]:
    """Parse --candy values like NACLI=72 or Naclstack=72 into {family: count}.

    A Pokémon's own name works too: it is mapped to its family, whose candy it uses.
    """
    counts = {}
    for pair in pairs:
        name, separator, count = pair.partition("=")
        key = name.strip().upper().replace(" ", "_").replace("-", "_")
        if not separator or not count.strip().isdigit() or key not in species:
            raise ValueError(f"--candy {pair!r}: expected a Pokémon or family name and a count, like NACLI=72")
        counts[species[key]["family"]] = int(count)
    return counts


def item_overrides(pairs: list[str], species: dict) -> dict[str, int]:
    """Parse --item values like "Tart Apple=26" or ITEM_METAL_COAT=1 into {item id: count}."""
    known = evolution_items(species)
    counts = {}
    for pair in pairs:
        name, separator, count = pair.rpartition("=")
        item = item_id(name, known) if separator else None
        if item is None or not count.strip().isdigit():
            raise ValueError(f'--item {pair!r}: expected an evolution item and a count, like "Tart Apple=26"')
        counts[item] = int(count)
    return counts
