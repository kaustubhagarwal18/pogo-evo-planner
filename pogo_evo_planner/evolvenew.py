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

from dataclasses import dataclass, field

from .gamemaster import item_id, item_name
from .inventory import Inventory
from .planner import Plan


@dataclass
class Listing:
    """What the `evolvenew` Pokémon tell us, beyond the inventory itself."""
    mega_only: list[str] = field(default_factory=list)       # final forms: listed for their Mega
    already_caught: list[str] = field(default_factory=list)  # evolutions whose sprite shows caught
    notes: list[str] = field(default_factory=list)


def apply_evolvenew(inv: dict, species: dict) -> tuple[dict, Listing]:
    """Mark the evolutions of every listed Pokémon as never caught, unless a sprite showed them caught.

    inv is an inventory dict as `scan` writes it; returns an updated copy and what else was learnt.
    """
    out = {**inv, "not_caught": list(inv.get("not_caught", [])), "pokedex": list(inv.get("pokedex", []))}
    caught, never = set(out["pokedex"]), set(out["not_caught"])
    listing = Listing()
    for sp in sorted({p["species"] for p in inv.get("pokemon", []) if p["species"] in species}):
        targets = [e["to"] for e in species[sp].get("evolutions", [])]
        if not targets:
            listing.mega_only.append(sp)
            continue
        new = [t for t in targets if t not in caught]
        listing.already_caught += [t for t in targets if t in caught]
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


def render(p: Plan, inv: Inventory, species: dict, listing: Listing) -> str:
    """The plan in plain words: evolve now, use Rare Candy, not yet, Mega only."""
    planned: set[str] = set()
    now, rare = [], []
    for f in sorted(p.families, key=lambda f: -f.value):
        for path in f.paths:
            if not path.steps:
                continue
            planned.update(s.to for s in path.steps)
            chain = " → ".join([_name(path.steps[0].frm)] + [_name(s.to) for s in path.steps])
            needs = _needs([(s.item, list(s.notes)) for s in path.steps])
            if f.rare_candy:
                rare.append(f"  {chain}  +{f.rare_candy} Rare Candy{needs}")
            else:
                now.append(f"  {chain}{needs}")
    out = [f"Rare Candy: {p.rare_candy_used} of {p.rare_candy_available} used", ""]
    if now:
        out += ["Evolve now with your own candy:", *now, ""]
    if rare:
        out += ["Use Rare Candy:", *rare, ""]
    later = _not_yet(inv, species, planned, p)
    if later:
        out += ["Not yet:", *later, ""]
    if p.trade_instead:
        out += ["Free by trading:",
                *sorted({f"  {_name(s.frm)} → {_name(s.to)}" for _, s in p.trade_instead}), ""]
    if listing.mega_only:
        out += ["Mega Evolution only (needs Mega Energy, not planned):",
                "  " + ", ".join(map(_name, listing.mega_only)), ""]
    if listing.already_caught:
        out += ["Already caught according to the Evolve button (not new):",
                "  " + ", ".join(map(_name, sorted(set(listing.already_caught)))), ""]
    out += [f"Note: {n}" for n in listing.notes]
    return "\n".join(out).rstrip() + "\n"


def _not_yet(inv: Inventory, species: dict, planned: set[str], p: Plan) -> list[str]:
    """Why each new evolution of a listed Pokémon isn't in the plan: candy short, unknown, or an item."""
    rows = []
    trade = {s.to for _, s in p.trade_instead}
    copies = {sp: sum(s.species == sp for s in inv.pokemon) for sp in {s.species for s in inv.pokemon}}
    used = {path.specimen.species for f in p.families for path in f.paths if path.steps}
    for sp in sorted(copies):
        fam = species.get(sp, {}).get("family")
        for e in species.get(sp, {}).get("evolutions", []):
            to = e["to"]
            if to in planned or to in trade or to not in inv.not_caught:
                continue
            have, cost = inv.candy.get(fam), int(e["candy"])
            if have is None:
                why = (f"candy count unknown: add a screenshot of its page, or --candy {fam}=N "
                       f"(needs {cost})")
            elif e.get("item") and inv.items.get(e["item"], 0) < 1:
                why = f"needs {item_name(e['item'])}: if you have one, add --item \"{item_name(e['item'])}=N\""
            elif have < cost:
                why = f"{cost - have} candy short (have {have} of {cost})"
            elif sp in used and copies[sp] <= sum(path.specimen.species == sp for f in p.families
                                                  for path in f.paths if path.steps):
                n = copies[sp]
                why = (f"your {n} {_name(sp)} {'is' if n == 1 else 'are'} planned above; if you have another, "
                       f"add a screenshot of its page")
            else:
                why = "its candy is used by the picks above"
            rows.append(f"  {_name(sp)} → {_name(to)}: {why}{_needs([(None, e.get('notes', []))])}")
    return rows


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
