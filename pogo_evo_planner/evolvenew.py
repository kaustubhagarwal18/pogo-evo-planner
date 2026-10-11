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

from .gamemaster import item_id, item_name
from .inventory import Inventory
from .planner import Plan


@dataclass
class Listing:
    """What the `evolvenew` Pokémon tell us, beyond the inventory itself."""
    mega_only: list[str] = field(default_factory=list)       # final forms: listed for their Mega
    already_caught: list[str] = field(default_factory=list)  # evolutions whose sprite shows caught
    already_held: list[str] = field(default_factory=list)    # evolutions you already have one of
    notes: list[str] = field(default_factory=list)


def apply_evolvenew(inv: dict, species: dict) -> tuple[dict, Listing]:
    """Mark the evolutions of every listed Pokémon as never caught, unless a sprite showed them caught.

    inv is an inventory dict as `scan` writes it; returns an updated copy and what else was learnt.
    """
    out = {**inv, "not_caught": list(inv.get("not_caught", [])), "pokedex": list(inv.get("pokedex", []))}
    held = {p["species"] for p in inv.get("pokemon", [])}
    caught, never = set(out["pokedex"]) | held, set(out["not_caught"]) - held   # you can't miss what you hold
    listing = Listing()
    for sp in sorted({p["species"] for p in inv.get("pokemon", []) if p["species"] in species}):
        targets = [e["to"] for e in species[sp].get("evolutions", [])]
        if not targets:
            listing.mega_only.append(sp)
            continue
        new = [t for t in targets if t not in caught]
        listing.already_held += [t for t in targets if t in held]
        listing.already_caught += [t for t in targets if t in caught and t not in held]
        if len(targets) > 1 and len(new) < len(targets):
            listing.notes.append(f"{_name(sp)}: only {', '.join(map(_name, new)) or 'none'} of its evolutions "
                                 "can be new")
        never |= set(new)
    out["not_caught"] = sorted(never)
    return out, listing


def _name(sp: str) -> str:
    """A species id as players write it: NACLSTACK -> Naclstack, MR_MIME -> Mr Mime."""
    return sp.replace("_", " ").title()


def _needs(steps: list[tuple[str | None, list[str]]]) -> str:
    """The extra requirements of an evolution's steps (item, notes), in words, or ''."""
    extras = [x for item, notes in steps for x in ([f"needs {item_name(item)}"] if item else []) + list(notes)]
    return f" ({'; '.join(dict.fromkeys(extras))})" if extras else ""


def _dex(sp: str, species: dict) -> int | str:
    """The Pokédex number a species or form counts toward (forms share one entry)."""
    return species.get(sp, {}).get("dex", sp)


def missing(inv: Inventory, species: dict) -> tuple[list[tuple[str, str, int]], list[str]]:
    """The species you're missing: (species, the Pokémon you own that evolves into it, candy) for each one
    you can evolve into, and the rest of the never-caught list to catch. One row per Pokédex entry, so a
    species with forms (Dudunsparce's two and three segments) is listed once, by its cheapest route."""
    held = {s.species for s in inv.pokemon}
    evolve: dict[int | str, tuple[str, str, int]] = {}
    for sp in sorted(held):
        for e in species.get(sp, {}).get("evolutions", []):
            to, cost = e["to"], int(e["candy"])
            key = _dex(to, species)
            if to in inv.not_caught and to not in held and (key not in evolve or cost < evolve[key][2]):
                evolve[key] = (to, sp, cost)
    catch = sorted({sp for sp in inv.not_caught if sp not in held and _dex(sp, species) not in evolve})
    return sorted(evolve.values()), catch


def _label(sp: str, inv: Inventory, species: dict) -> str:
    """A missing species as players know it: forms of one Pokédex entry are named once, with the forms."""
    base = next((k for k, v in species.items() if v.get("dex") == _dex(sp, species) and "_" not in k), None)
    # forms in the species table's order (Two before Three), leaving out the plain base name
    forms = [k for k in species if k != base and _dex(k, species) == _dex(sp, species)
             and (k == sp or k in inv.not_caught)]
    if len(forms) < 2 or base is None:
        return _name(sp)
    return f"{_name(base)} ({' or '.join(_name(f).removeprefix(_name(base)).strip() for f in forms)})"


def _evolution(frm: str, to: str, species: dict) -> dict:
    """The evolution step from one species to another, as the species table records it."""
    return next(e for e in species[frm]["evolutions"] if e["to"] == to)


def unknown_candy(inv: Inventory, species: dict) -> frozenset[str]:
    """Families with a missing species to evolve into but no candy count: never spend Rare Candy on them."""
    return frozenset(species[frm]["family"] for _, frm, _ in missing(inv, species)[0]
                     if species[frm]["family"] not in inv.candy)


def render(p: Plan, inv: Inventory, species: dict, listing: Listing,
           show_missing: bool = True, show_plan: bool = True) -> str:
    """Which species you're missing, then the plan in plain words: evolve now, use Rare Candy, not yet.

    The command asks for missing candy counts in between, so it prints the two parts separately;
    the closing notes go with the plan, at the end.
    """
    out = _render_missing(inv, species, listing) if show_missing else []
    if show_plan:
        out += _render_plan(p, inv, species) + _render_notes(listing)
    return "\n".join(out).rstrip() + "\n"


def _render_missing(inv: Inventory, species: dict, listing: Listing) -> list[str]:
    """What you're missing: species to evolve into, species to catch, and Mega-only Pokémon."""
    evolve, catch = missing(inv, species)
    out = [f"You're missing {len(evolve) + len(catch)} species.", ""]
    if evolve:
        out.append("Evolve into these (from Pokémon you own):")
        width = max(len(_label(to, inv, species)) for to, _, _ in evolve) + 2
        for to, frm, cost in evolve:
            have = inv.candy.get(species[frm]["family"])
            candy = "candy ?" if have is None else f"have {have}"
            e = _evolution(frm, to, species)
            needs = _needs([(e.get("item"), e.get("notes", []))])
            out.append(f"  {_label(to, inv, species):{width}}from {_name(frm):14} {cost:4} candy, {candy}{needs}")
        out.append("")
    if catch:
        out += ["Catch these (seen, but nothing you own evolves into them):",
                "  " + ", ".join(map(_name, catch)), ""]
    if listing.mega_only:
        out += [f"Mega Evolution only ({len(listing.mega_only)}, need Mega Energy, not planned):",
                "  " + ", ".join(map(_name, listing.mega_only)), ""]
    return out


def _render_plan(p: Plan, inv: Inventory, species: dict) -> list[str]:
    """The Rare Candy plan: evolve now, use Rare Candy, not yet, and the candy counts still needed."""
    planned: set[str] = set()
    now, rare = [], []
    for f in sorted(p.families, key=lambda f: -f.value):
        lines = []
        for path in f.paths:
            if not path.steps:
                continue
            planned.update(s.to for s in path.steps)
            chain = " → ".join([_name(path.steps[0].frm)] + [_name(s.to) for s in path.steps])
            lines.append(f"  {chain}{_needs([(s.item, list(s.notes)) for s in path.steps])}")
        if not f.rare_candy:
            now += lines
        elif len(lines) == 1:   # Rare Candy is per family: inline for one evolution, a total for several
            rare.append(lines[0].replace(" (", f"  +{f.rare_candy} Rare Candy (", 1) if " (" in lines[0]
                        else f"{lines[0]}  +{f.rare_candy} Rare Candy")
        else:
            rare += lines + [f"    +{f.rare_candy} Rare Candy for the {_name(f.family)} family"]
    later = _not_yet(inv, species, planned, p)
    out = [f"Rare Candy: {p.rare_candy_used} of {p.rare_candy_available} used", ""] if now or rare or later else []
    for title, rows in (("Evolve now with your own candy:", now), ("Use Rare Candy:", rare), ("Not yet:", later)):
        if rows:
            out += [title, *rows, ""]
    if p.trade_instead:
        out += ["Free by trading:",
                *sorted({f"  {_name(s.frm)} → {_name(s.to)}" for _, s in p.trade_instead}), ""]
    unknown = sorted({(species[frm]["family"], frm) for _, frm, _ in missing(inv, species)[0]
                      if species[frm]["family"] not in inv.candy})
    if unknown:
        out += [(f"To plan your {p.rare_candy_available} Rare Candy for the rest, add each family's candy count: "
                 "screenshot one Pokémon's page per family, or use --candy NAME=N:"),
                "  " + ", ".join(f"{_name(fam)} (e.g. {_name(frm)})" if fam != frm else _name(fam)
                                 for fam, frm in unknown), ""]
    return out


def _render_notes(listing: Listing) -> list[str]:
    """Closing notes: evolutions listed but already held or caught, and branch notes."""
    out = []
    if listing.already_held:
        out += ["Listed by evolvenew, but you already have one (maybe a different form or costume):",
                "  " + ", ".join(map(_name, sorted(set(listing.already_held)))), ""]
    if listing.already_caught:
        out += ["Already caught according to the Evolve button (not new):",
                "  " + ", ".join(map(_name, sorted(set(listing.already_caught)))), ""]
    return out + [f"Note: {n}" for n in listing.notes]


def _not_yet(inv: Inventory, species: dict, planned: set[str], p: Plan) -> list[str]:
    """Why a missing species you could evolve into isn't in the plan: candy short, an item, or every copy
    already planned. Unknown candy counts are listed separately, as the next thing to add."""
    rows = []
    trade = {s.to for _, s in p.trade_instead}
    planned_entries = {_dex(t, species) for t in planned}   # one form of a Pokédex entry is enough
    copies = {sp: sum(s.species == sp for s in inv.pokemon) for sp in {s.species for s in inv.pokemon}}
    taken = {sp: sum(path.specimen.species == sp for f in p.families for path in f.paths if path.steps)
             for sp in copies}
    for sp in sorted(copies):
        fam = species.get(sp, {}).get("family")
        for e in species.get(sp, {}).get("evolutions", []):
            to = e["to"]
            if to in planned or _dex(to, species) in planned_entries or to in trade or to not in inv.not_caught \
                    or to in copies:
                continue
            have, cost = inv.candy.get(fam), int(e["candy"])
            if have is None:
                continue
            if e.get("item") and inv.items.get(e["item"], 0) < 1:
                why = f"needs {item_name(e['item'])}: if you have one, add --item \"{item_name(e['item'])}=N\""
            elif have < cost:
                why = f"{cost - have} candy short (have {have} of {cost})"
            elif taken[sp] >= copies[sp]:
                n = copies[sp]
                why = (f"your {n} {_name(sp)} {'is' if n == 1 else 'are'} planned above; if you have another, "
                       f"add a screenshot of its page")
            else:
                why = "its candy is used by the picks above"
            rows.append(f"  {_name(sp)} → {_name(to)}: {why}{_needs([(None, e.get('notes', []))])}")
    return rows


def ask_for_candy(inv: Inventory, species: dict, ask: Callable[[str], str],
                  read: Callable[[list[str]], dict[str, int]], say: Callable[[str], None]) -> dict[str, int]:
    """Ask for each missing candy count: a number, a screenshot of that Pokémon's page, or Enter to skip.

    ask prompts and returns the answer (input), read OCRs screenshots into {family: candy}, say shows a
    message. Returns the counts given, {family: count}. "q", or the end of input (Ctrl-D), stops asking.
    """
    wanted: dict[str, list[str]] = {}
    for to, frm, cost in missing(inv, species)[0]:
        fam = species[frm]["family"]
        if fam not in inv.candy:
            wanted.setdefault(fam, []).append(f"{_name(frm)} → {_name(to)} needs {cost}")
    if not wanted:
        return {}
    say(f"{len(wanted)} families need a candy count. For each, type the number, or the path of a screenshot "
        "of that Pokémon's page (you can drag the file in), or press Enter to skip; q stops.")
    answers: dict[str, int] = {}
    for fam, uses in wanted.items():
        if fam in answers:   # already read from an earlier screenshot
            continue
        for _ in range(3):
            try:
                reply = ask(f"{_name(fam)} candy ({'; '.join(uses)}): ").strip()
            except EOFError:   # Ctrl-D, or input ran out: plan with what was given
                return answers
            if not reply:
                break
            if reply.lower() in ("q", "quit"):
                return answers
            if reply.isdigit():
                answers[fam] = int(reply)
                break
            try:
                read_counts = read(shlex.split(reply))
            except (ValueError, OSError) as e:
                say(f"  couldn't read that: {e}")
                continue
            answers.update({f: n for f, n in read_counts.items() if f in wanted})   # any family it shows
            if fam in read_counts:
                say(f"  read {read_counts[fam]}")
                break
            say(f"  no {_name(fam)} candy count on that screenshot; type the number instead, or another screenshot")
    return answers


def candy_overrides(pairs: list[str], species: dict) -> dict[str, int]:
    """Parse --candy values like NACLI=72 or Naclstack=72 into {family: count}.

    A Pokémon's own name works too: it is mapped to its family, whose candy it uses.
    """
    out = {}
    for pair in pairs:
        name, sep, count = pair.partition("=")
        key = name.strip().upper().replace(" ", "_").replace("-", "_")
        if not sep or not count.strip().isdigit() or key not in species:
            raise ValueError(f"--candy {pair!r}: expected a Pokémon or family name and a count, like NACLI=72")
        out[species[key]["family"]] = int(count)
    return out


def item_overrides(pairs: list[str], species: dict) -> dict[str, int]:
    """Parse --item values like "Tart Apple=26" or ITEM_METAL_COAT=1 into {item id: count}."""
    known = {e["item"] for s in species.values() for e in s.get("evolutions", []) if e.get("item")}
    out = {}
    for pair in pairs:
        name, sep, count = pair.rpartition("=")
        item = item_id(name, known) if sep else None
        if item is None or not count.strip().isdigit():
            raise ValueError(f"--item {pair!r}: expected an evolution item and a count, like \"Tart Apple=26\"")
        out[item] = int(count)
    return out
