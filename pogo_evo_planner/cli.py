"""Command line entry point.

    pogo-evo-planner scan bag.png recording.mp4 -o inventory.json      # OCR screens -> inventory
    pogo-evo-planner scan bag.png recording.mp4 -o inventory.json --plan
    pogo-evo-planner plan inventory.json                               # inventory -> recommendations
    pogo-evo-planner plan inventory.json --species latest.json --json
    pogo-evo-planner dashboard inventory.json -o dashboard.html --open  # inventory -> HTML dashboard

(`python -m pogo_evo_planner.cli ...` works too.)
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path

from .gamemaster import load_species
from .inventory import Inventory, load_inventory
from .planner import Plan, Settings, plan

DATA = Path(__file__).resolve().parent / "data"


def _title(s: str) -> str:
    """Turn an id like DRATINI or ITEM_METAL_COAT into display text."""
    return s.replace("_", " ").title()


def render(p: Plan) -> str:
    """Format a plan as the plain-text report printed by `pogo-evo-planner plan`."""
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
                chain = " -> ".join([_title(path.steps[0].frm)] + [_title(s.to) for s in path.steps])
                out.append(f"   {chain}")
                for s in path.steps:
                    item = [f"needs {s.item.removeprefix('ITEM_').replace('_', ' ').title()}"] if s.item else []
                    extras = item + list(s.notes)
                    if extras:
                        out.append(f"      {_title(s.to)}: {'; '.join(extras)}")
        out.append("")
    if p.trade_instead:
        out.append("== Trade instead of spending candy ==")
        for spec, step in p.trade_instead:
            via = f" (from {_title(spec.species)})" if spec.species != step.frm else ""
            out.append(f"   {_title(step.frm)} -> {_title(step.to)} is free when traded{via}")
        out.append("")
    if p.unknown_species:
        out.append("Not in species data (skipped): " + ", ".join(p.unknown_species))
    return "\n".join(out).rstrip() + "\n"


def _add_data_args(ap: argparse.ArgumentParser) -> None:
    """Add the --species option shared by every subcommand."""
    ap.add_argument("--species", default=DATA / "species_sample.json",
                    help="PokeMiners latest.json or a normalized species table (default: bundled sample)")


def _add_plan_args(ap: argparse.ArgumentParser) -> None:
    """Add the planner tuning and output options."""
    ap.add_argument("--tiers", default=DATA / "rarity_tiers.json", help="rarity tier mapping")
    ap.add_argument("--dex-bonus", type=float, default=20.0, help="value of a new Pokédex entry (km-equivalent)")
    ap.add_argument("--repeat-value", type=float, default=0.0,
                    help="share of value kept by evolving into a species you already own (1 = no discount)")
    ap.add_argument("--allow-trade-evos", action="store_true", help="spend candy on evolutions that are free via trade")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")


def _make_plan(a, species: dict, inventory_path: str | Path) -> tuple[Plan, Inventory]:
    """Load the tiers and inventory and run the planner."""
    tiers = json.loads(Path(a.tiers).read_text(encoding="utf-8"))
    inv = load_inventory(inventory_path, getattr(a, "pokemon_csv", None))
    result = plan(species, tiers, inv, Settings(dex_bonus=a.dex_bonus, repeat_value=a.repeat_value,
                                                 allow_trade_evolutions=a.allow_trade_evos))
    return result, inv


def _run_plan(a, species: dict, inventory_path: str | Path) -> None:
    """Plan an inventory and print the result as text or JSON."""
    result, _ = _make_plan(a, species, inventory_path)
    print(json.dumps(asdict(result), indent=2, default=str) if a.json else render(result), end="")


def _write_dashboard(a, species: dict, inventory_path: str | Path, output: str | Path, source: str) -> None:
    """Plan an inventory, write it as an HTML dashboard, and optionally open it in a browser."""
    from .dashboard import build_view, render_html

    result, inv = _make_plan(a, species, inventory_path)
    Path(output).write_text(render_html(build_view(result, inv, species, source)), encoding="utf-8")
    print(f"Dashboard -> {output}", file=sys.stderr)
    if getattr(a, "open", False):
        webbrowser.open(Path(output).resolve().as_uri())


def cmd_plan(a) -> None:
    """Handle `pogo-evo-planner plan`."""
    _run_plan(a, load_species(a.species), a.inventory)


def cmd_dashboard(a) -> None:
    """Handle `pogo-evo-planner dashboard`."""
    _write_dashboard(a, load_species(a.species), a.inventory, a.output, str(a.inventory))


def cmd_scan(a) -> None:
    """Handle `pogo-evo-planner scan`: OCR the files, write the inventory, then plan or write a dashboard if asked."""
    from .scan.pipeline import scan  # OCR deps are only needed for this command

    species = load_species(a.species)

    def progress(i, n, label, kind):
        """Overwrite one stderr line with the frame being read."""
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
    if a.dashboard:
        _write_dashboard(a, species, a.output, a.dashboard, ", ".join(Path(f).name for f in a.files))


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the plan, dashboard and scan subcommands."""
    ap = argparse.ArgumentParser(prog="pogo-evo-planner", description="Plan Pokémon GO evolutions and rare candy use.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="recommend evolutions from an inventory JSON")
    p.add_argument("inventory")
    p.add_argument("--pokemon-csv", help="CSV of owned Pokémon (overrides the inventory list)")
    _add_data_args(p)
    _add_plan_args(p)
    p.set_defaults(func=cmd_plan)

    d = sub.add_parser("dashboard", help="write the plan for an inventory JSON as an HTML dashboard")
    d.add_argument("inventory")
    d.add_argument("-o", "--output", default="dashboard.html")
    d.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    d.add_argument("--pokemon-csv", help="CSV of owned Pokémon (overrides the inventory list)")
    _add_data_args(d)
    _add_plan_args(d)
    d.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("scan", help="OCR screenshots / screen recordings into an inventory JSON")
    s.add_argument("files", nargs="+", help="screenshots (.png/.jpg) and/or recordings (.mp4/.mov)")
    s.add_argument("-o", "--output", default="inventory.json")
    s.add_argument("--ocr", choices=["auto", "rapidocr", "tesseract"], default="auto")
    s.add_argument("--fps", type=float, default=3.0, help="frames per second sampled from recordings")
    s.add_argument("--plan", action="store_true", help="run the planner on the result")
    s.add_argument("--dashboard", metavar="HTML", help="also write the plan as an HTML dashboard")
    s.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    s.add_argument("-q", "--quiet", action="store_true")
    _add_data_args(s)
    _add_plan_args(s)
    s.set_defaults(func=cmd_scan)
    return ap


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the chosen subcommand."""
    argv = sys.argv[1:] if argv is None else argv
    # backwards compatible: `pogo-evo-planner inventory.json` == `pogo-evo-planner plan inventory.json`
    if argv and argv[0] not in ("plan", "scan", "dashboard", "-h", "--help"):
        argv = ["plan", *argv]
    a = build_parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
