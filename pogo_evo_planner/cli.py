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
LATEST = Path("data/latest.json")  # written by `make gamemaster`, relative to where you run the tool


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
    ap.add_argument("--species", default=LATEST if LATEST.exists() else DATA / "species_sample.json",
                    help="PokeMiners latest.json or a normalized species table "
                         "(default: data/latest.json from `make gamemaster`, else a small bundled sample)")


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
                                                 allow_trade_evolutions=a.allow_trade_evos,
                                                 new_entries_only=getattr(a, "new_entries_only", False),
                                                 rare_candy_blocked_families=getattr(a, "blocked", frozenset())))
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


def cmd_evolvenew(a) -> None:
    """Handle `pogo-evo-planner evolvenew`: read the detail pages of the Pokémon the game's `evolvenew`
    search lists, then print what to evolve and where Rare Candy goes."""
    from .evolvenew import apply_evolvenew, ask_for_candy, candy_overrides, item_overrides, unknown_candy
    from .evolvenew import render as render_evolvenew
    from .scan.pipeline import scan

    species = load_species(a.species)
    try:
        candy = candy_overrides(a.candy, species)
        items = item_overrides(a.item, species)
    except ValueError as e:
        sys.exit(f"error: {e}")

    def progress(i, n, label, kind):
        """Overwrite one stderr line with the frame being read."""
        print(f"\r  [{i}/{n}] {label}: {kind:<8}", end="", file=sys.stderr, flush=True)

    try:
        inv, report = scan(a.files, species, backend=a.ocr, fps=a.fps, progress=None if a.quiet else progress)
    except ValueError as e:
        sys.exit(f"error: {e}")
    if not a.quiet:
        print(file=sys.stderr)
    inv["candy"].update(candy)   # counts typed on the command line win over what was read
    inv["items"].update(items)
    if a.rare_candy is not None:
        inv["rare_candy"] = a.rare_candy
    elif not report.screens["bag"]:
        sys.exit("error: no Rare Candy count: add --rare-candy N, or a screenshot of your item bag")
    inv, listing = apply_evolvenew(inv, species)
    Path(a.output).write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")

    shown = sorted({p["species"] for p in inv["pokemon"]})
    print(f"Read {len(shown)} Pokémon: {', '.join(s.replace('_', ' ').title() for s in shown)}", file=sys.stderr)
    for w in report.warnings:   # the plan below covers missing candy and Rare Candy itself
        if not w.startswith(("no candy count for", "no item bag screen")):
            print(f"  ! {w}", file=sys.stderr)
    print(f"Saved -> {a.output} (fix a misread count with --candy NAME=N)\n", file=sys.stderr)
    a.blocked = unknown_candy(load_inventory(a.output), species)   # never guess a count as 0 and fill it
    result, plan_inv = _make_plan(a, species, a.output)
    if not a.blocked or a.no_ask or not sys.stdin.isatty():
        print(render_evolvenew(result, plan_inv, species, listing), end="")
    else:
        # show what's missing, ask for the candy counts it needs, then plan with the answers
        print(render_evolvenew(result, plan_inv, species, listing, show_plan=False))

        def read_candy(paths: list[str]) -> dict[str, int]:
            """Read candy counts from detail-page screenshots."""
            return scan(paths, species, backend=a.ocr, fps=a.fps)[0]["candy"]

        answers = ask_for_candy(plan_inv, species, ask=input, read=read_candy, say=print)
        if answers:
            inv["candy"].update(answers)
            Path(a.output).write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
            a.blocked = unknown_candy(load_inventory(a.output), species)
            result, plan_inv = _make_plan(a, species, a.output)
            given = " ".join(f"--candy {f}={n}" for f, n in sorted(answers.items()))
            print(f"\nSaved your counts to {a.output}. To skip the questions next time: {given}\n")
        print(render_evolvenew(result, plan_inv, species, listing, show_missing=False), end="")
    if a.dashboard:
        _write_dashboard(a, species, a.output, a.dashboard, ", ".join(Path(f).name for f in a.files))


def cmd_scan(a) -> None:
    """Handle `pogo-evo-planner scan`: OCR the files, write the inventory, then plan or write a dashboard if asked."""
    from .scan.pipeline import scan  # OCR deps are only needed for this command

    species = load_species(a.species)

    def progress(i, n, label, kind):
        """Overwrite one stderr line with the frame being read."""
        print(f"\r  [{i}/{n}] {label}: {kind:<8}", end="", file=sys.stderr, flush=True)

    try:
        inv, report = scan(a.files, species, backend=a.ocr, fps=a.fps, progress=None if a.quiet else progress,
                           max_llm_frames=a.llm_max_frames, llm_model=a.llm_model)
    except ValueError as e:
        sys.exit(f"error: {e}")
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

    e = sub.add_parser("evolvenew", help="plan from the Pokémon the game's `evolvenew` search lists (quickest)",
                       description="In Pokémon GO, search `evolvenew` in your Pokémon storage, then screenshot "
                                   "each listed Pokémon's page (or record yourself swiping through them). Pass "
                                   "those files and your Rare Candy count.")
    e.add_argument("files", nargs="+", help="detail-page screenshots and/or a recording, optionally the item bag")
    e.add_argument("--rare-candy", type=int, help="your Rare Candy count (instead of a bag screenshot)")
    e.add_argument("--candy", action="append", default=[], metavar="NAME=N",
                   help="set or fix a candy count, e.g. --candy NACLI=72 or --candy Naclstack=72 (repeatable)")
    e.add_argument("--item", action="append", default=[], metavar="NAME=N",
                   help='evolution items you hold, e.g. --item "Tart Apple=26" --item "Metal Coat=1" (repeatable)')
    e.add_argument("-o", "--output", default="evolvenew.json", help="where to save what was read")
    e.add_argument("--no-ask", action="store_true",
                   help="don't ask for missing candy counts; just list them (asking only happens in a terminal)")
    e.add_argument("--ocr", choices=["auto", "rapidocr", "tesseract", "llm", "ollama", "claude"], default="auto",
                   help="screen reader (default: OCR)")
    e.add_argument("--fps", type=float, default=3.0, help="frames per second sampled from recordings")
    e.add_argument("--dashboard", metavar="HTML", help="also write the plan as an HTML dashboard")
    e.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    e.add_argument("-q", "--quiet", action="store_true")
    _add_data_args(e)
    _add_plan_args(e)
    e.set_defaults(func=cmd_evolvenew, new_entries_only=True)

    s = sub.add_parser("scan", help="OCR screenshots / screen recordings into an inventory JSON")
    s.add_argument("files", nargs="+", help="screenshots (.png/.jpg) and/or recordings (.mp4/.mov)")
    s.add_argument("-o", "--output", default="inventory.json")
    s.add_argument("--ocr", choices=["auto", "rapidocr", "tesseract", "llm", "ollama", "claude"], default="auto",
                   help="screen reader (default: OCR). LLMs need the llm extras: 'llm' is the default LLM, "
                        "'ollama' (a local model, free); 'claude' sends each frame to the Claude API (paid)")
    s.add_argument("--llm-model", help="model for --ocr llm/ollama/claude (default: qwen3-vl:4b / claude-opus-5-5)")
    s.add_argument("--llm-max-frames", type=int, default=60,
                   help="with --ocr claude, refuse to send more frames than this (default 60)")
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
    if argv and argv[0] not in ("plan", "scan", "dashboard", "evolvenew", "-h", "--help"):
        argv = ["plan", *argv]
    a = build_parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
