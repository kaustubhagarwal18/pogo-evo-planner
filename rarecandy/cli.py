"""Command line entry point.

    rarecandy scan bag.png recording.mp4 -o inventory.json      # OCR screens -> inventory
    rarecandy scan bag.png recording.mp4 -o inventory.json --plan
    rarecandy plan inventory.json                               # inventory -> recommendations
    rarecandy plan inventory.json --species latest.json --json

(`python -m rarecandy.cli ...` works too.)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .gamemaster import load_species
from .inventory import load_inventory
from .planner import Plan, Settings, plan

DATA = Path(__file__).resolve().parent / "data"


def _title(s: str) -> str:
    return s.replace("_", " ").title()


def render(p: Plan) -> str:
    out = [(f"Rare candy: using {p.rare_candy_used} of {p.rare_candy_available}"
           f"  (value score {p.total_value:,.0f})"), ""]
    spend = [f for f in p.families if f.rare_candy > 0]
    free = [f for f in p.families if f.rare_candy == 0]
    for heading, group in (("Spend rare candy on", spend), ("Already affordable with your own candy", free)):
        if not group:
            continue
        out.append(f"== {heading} ==")
        for f in group:
            line = f"{_title(f.family)} family: {f.candy_used} candy ({f.candy_owned} owned"
            if f.rare_candy:
                line += f" + {f.rare_candy} rare) - saves ~{f.km_saved:,.0f} km of buddy walking"
            else:
                line += ")"
            out.append(line)
            for path in f.paths:
                iv = f" {path.specimen.iv_pct}%" if path.specimen.iv_pct is not None else ""
                chain = " -> ".join([_title(path.steps[0].frm)] + [_title(s.to) for s in path.steps])
                out.append(f"   {chain}  [{path.specimen.label}{iv}]")
                for s in path.steps:
                    extras = ([f"needs {s.item.removeprefix('ITEM_').replace('_', ' ').title()}"] if s.item else []) + list(s.notes)
                    if extras:
                        out.append(f"      {_title(s.to)}: {'; '.join(extras)}")
        out.append("")
    if p.trade_instead:
        out.append("== Trade instead of spending candy ==")
        for spec, step in p.trade_instead:
            out.append(f"   {_title(step.frm)} -> {_title(step.to)} is free when traded (from {_title(spec.species)} {spec.label})")
        out.append("")
    if p.unknown_species:
        out.append("Not in species data (skipped): " + ", ".join(p.unknown_species))
    return "\n".join(out).rstrip() + "\n"


def _add_data_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--species", default=DATA / "species_sample.json",
                    help="PokeMiners latest.json or a normalized species table (default: bundled sample)")


def _add_plan_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--tiers", default=DATA / "rarity_tiers.json", help="rarity tier mapping")
    ap.add_argument("--dex-bonus", type=float, default=20.0, help="value of a new Pokédex entry (km-equivalent)")
    ap.add_argument("--allow-trade-evos", action="store_true", help="spend candy on evolutions that are free via trade")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")


def _run_plan(a, species: dict, inventory_path: str | Path) -> None:
    tiers = json.loads(Path(a.tiers).read_text(encoding="utf-8"))
    inv = load_inventory(inventory_path, getattr(a, "pokemon_csv", None))
    result = plan(species, tiers, inv, Settings(dex_bonus=a.dex_bonus, allow_trade_evolutions=a.allow_trade_evos))
    print(json.dumps(asdict(result), indent=2, default=str) if a.json else render(result), end="")


def cmd_plan(a) -> None:
    _run_plan(a, load_species(a.species), a.inventory)


def cmd_scan(a) -> None:
    from .scan.pipeline import scan  # OCR deps are only needed for this command

    species = load_species(a.species)

    def progress(i, n, label, kind):
        print(f"\r  [{i}/{n}] {label}: {kind:<8}", end="", file=sys.stderr, flush=True)

    inv, report = scan(a.files, species, backend=a.ocr, fps=a.fps, progress=None if a.quiet else progress)
    if not a.quiet:
        print(file=sys.stderr)
    Path(a.output).write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")

    summary = ", ".join(f"{k}: {v}" for k, v in sorted(report.screens.items()))
    print(f"Read {report.frames_read} frames ({summary})", file=sys.stderr)
    print(f"Found {len(inv['pokemon'])} Pokémon, candy for {len(inv['candy'])} families, "
          f"{len(inv['pokedex'])} Pokédex entries, {inv['rare_candy']} Rare Candy -> {a.output}", file=sys.stderr)
    for w in report.warnings:
        print(f"  ! {w}", file=sys.stderr)
    print("Review the file (OCR makes mistakes) and add evolution items under \"items\".", file=sys.stderr)
    if a.plan:
        print(file=sys.stderr)
        _run_plan(a, species, a.output)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rarecandy", description="Plan Pokémon GO evolutions and rare candy use.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="recommend evolutions from an inventory JSON")
    p.add_argument("inventory")
    p.add_argument("--pokemon-csv", help="CSV of owned Pokémon (overrides the inventory list)")
    _add_data_args(p)
    _add_plan_args(p)
    p.set_defaults(func=cmd_plan)

    s = sub.add_parser("scan", help="OCR screenshots / screen recordings into an inventory JSON")
    s.add_argument("files", nargs="+", help="screenshots (.png/.jpg) and/or recordings (.mp4/.mov)")
    s.add_argument("-o", "--output", default="inventory.json")
    s.add_argument("--ocr", choices=["auto", "rapidocr", "tesseract"], default="auto")
    s.add_argument("--fps", type=float, default=3.0, help="frames per second sampled from recordings")
    s.add_argument("--plan", action="store_true", help="run the planner on the result")
    s.add_argument("-q", "--quiet", action="store_true")
    _add_data_args(s)
    _add_plan_args(s)
    s.set_defaults(func=cmd_scan)
    return ap


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # backwards compatible: `rarecandy inventory.json` == `rarecandy plan inventory.json`
    if argv and argv[0] not in ("plan", "scan", "-h", "--help"):
        argv = ["plan", *argv]
    a = build_parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
