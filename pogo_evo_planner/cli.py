"""Command line entry point.

    pogo-evo-planner evolvenew results.mp4 seen.png bag.png            # the game's evolvenew search -> plan
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
from typing import Any

from .gamemaster import item_name, load_species
from .inventory import Inventory, load_inventory, load_pokemon_csv
from .planner import Plan, Settings, plan
from .scan import LLM_READERS, READERS

DATA = Path(__file__).resolve().parent / "data"
LATEST = Path("data/latest.json")  # written by `make gamemaster`, relative to where you run the tool


def _title(game_id: str) -> str:
    """Turn an id like DRATINI or ITEM_METAL_COAT into display text."""
    return game_id.replace("_", " ").title()


def render(evolution_plan: Plan) -> str:
    """Format a plan as the plain-text report printed by `pogo-evo-planner plan`."""
    lines = [
        (
            f"Rare candy: using {evolution_plan.rare_candy_used} of {evolution_plan.rare_candy_available}"
            f"  (value score {evolution_plan.total_value:,.0f})"
        ),
        "",
    ]
    spend = [family_plan for family_plan in evolution_plan.families if family_plan.rare_candy > 0]
    free = [family_plan for family_plan in evolution_plan.families if family_plan.rare_candy == 0]
    for heading, group in (("Spend rare candy on", spend), ("Already affordable with your own candy", free)):
        if not group:
            continue
        lines.append(f"== {heading} ==")
        for family_plan in group:
            line = (
                f"{_title(family_plan.family)} family: {family_plan.candy_used} candy ({family_plan.candy_owned} owned"
            )
            if family_plan.rare_candy:
                line += f" + {family_plan.rare_candy} rare) - saves ~{family_plan.km_saved:,.0f} km of buddy walking"
            else:
                line += ")"
            lines.append(line)
            for path in family_plan.paths:
                chain = " -> ".join([_title(path.steps[0].frm)] + [_title(step.to) for step in path.steps])
                lines.append(f"   {chain}")
                for step in path.steps:
                    item = [f"needs {item_name(step.item)}"] if step.item else []
                    extras = item + list(step.notes)
                    if extras:
                        lines.append(f"      {_title(step.to)}: {'; '.join(extras)}")
        lines.append("")
    if evolution_plan.trade_instead:
        lines.append("== Trade instead of spending candy ==")
        for specimen, step in evolution_plan.trade_instead:
            via = f" (from {_title(specimen.species)})" if specimen.species != step.frm else ""
            lines.append(f"   {_title(step.frm)} -> {_title(step.to)} is free when traded{via}")
        lines.append("")
    if evolution_plan.no_candy_count:
        lines.append(
            "No candy count, so no Rare Candy planned for: "
            + ", ".join(_title(family) for family in evolution_plan.no_candy_count)
            + ' (add their counts under "candy")'
        )
    if evolution_plan.unknown_species:
        lines.append("Not in species data (skipped): " + ", ".join(evolution_plan.unknown_species))
    if evolution_plan.unknown_items:
        lines.append(
            "No evolution uses these items, so the plan ignores them: "
            + ", ".join(evolution_plan.unknown_items)
            + ' (check their names under "items")'
        )
    return "\n".join(lines).rstrip() + "\n"


def _add_data_args(parser: argparse.ArgumentParser) -> None:
    """Add the --species option shared by every subcommand."""
    parser.add_argument(
        "--species",
        default=LATEST if LATEST.exists() else DATA / "species_sample.json",
        help="PokeMiners latest.json or a normalized species table "
        "(default: data/latest.json from `make gamemaster`, else a small bundled sample)",
    )


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    """Add the planner tuning and output options."""
    parser.add_argument("--tiers", default=DATA / "rarity_tiers.json", help="rarity tier mapping")
    parser.add_argument("--dex-bonus", type=float, default=20.0, help="value of a new Pokédex entry (km-equivalent)")
    parser.add_argument(
        "--repeat-value",
        type=float,
        default=0.0,
        help="share of value kept by evolving into a species you already own (1 = no discount)",
    )
    parser.add_argument(
        "--allow-trade-evos", action="store_true", help="spend candy on evolutions that are free via trade"
    )
    parser.add_argument("--json", action="store_true", help="print the plan as JSON")


def _add_scan_args(parser: argparse.ArgumentParser) -> None:
    """Add the options shared by the commands that read screens: the reader, frame sampling and the dashboard."""
    parser.add_argument(
        "--ocr",
        choices=READERS,
        default="auto",
        help="screen reader (default: OCR). LLMs need the llm extras: 'llm' is the default LLM, "
        "'ollama' (a local model, free); 'claude' sends each frame to the Claude API (paid)",
    )
    parser.add_argument(
        "--llm-model", help="model for --ocr llm/ollama/claude (default: qwen3-vl:4b / claude-opus-5-5)"
    )
    parser.add_argument(
        "--llm-max-frames",
        type=int,
        default=60,
        help="with --ocr claude, refuse to send more frames than this (default 60)",
    )
    parser.add_argument("--fps", type=float, default=3.0, help="frames per second sampled from recordings")
    parser.add_argument("--dashboard", metavar="HTML", help="also write the plan as an HTML dashboard")
    parser.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    parser.add_argument("-q", "--quiet", action="store_true")


def _read_json(path: str | Path):
    """Parse a JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load(what: str, load, path: str | Path) -> Any:
    """Load a file with load(path); exit with one readable line when it's missing or isn't valid."""
    try:
        return load(path)
    except OSError as error:
        sys.exit(f"error: can't read the {what} {path}: {error.strerror or error}")
    except KeyError as error:
        sys.exit(f"error: {path} isn't a valid {what} file: an entry has no {error} field")
    except (ValueError, TypeError, AttributeError) as error:  # bad JSON, or JSON that isn't shaped like one
        sys.exit(f"error: {path} isn't a valid {what} file: {error}")


def _species(args) -> dict:
    """The species table --species names."""
    return _load("species table", load_species, args.species)


def _save(path: str | Path, inventory: dict) -> None:
    """Write an inventory dict as indented JSON."""
    Path(path).write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")


def _make_plan(args, species: dict, inventory_path: str | Path) -> tuple[Plan, Inventory]:
    """Load the tiers and inventory and run the planner."""
    tiers = _load("rarity tiers", _read_json, args.tiers)
    inventory = _load("inventory", lambda path: load_inventory(path, species=species), inventory_path)
    if getattr(args, "pokemon_csv", None):
        inventory.pokemon = _load("Pokémon CSV", load_pokemon_csv, args.pokemon_csv)
    result = plan(
        species,
        tiers,
        inventory,
        Settings(
            dex_bonus=args.dex_bonus,
            repeat_value=args.repeat_value,
            allow_trade_evolutions=args.allow_trade_evos,
            new_entries_only=getattr(args, "new_entries_only", False),
        ),
    )
    return result, inventory


def _run_plan(args, species: dict, inventory_path: str | Path) -> None:
    """Plan an inventory and print the result as text or JSON."""
    result, _ = _make_plan(args, species, inventory_path)
    print(json.dumps(asdict(result), indent=2, default=str) if args.json else render(result), end="")


def _write_dashboard(args, species: dict, inventory_path: str | Path, output: str | Path, source: str) -> None:
    """Plan an inventory, write it as an HTML dashboard, and optionally open it in a browser."""
    from .dashboard import build_view, render_html

    result, inventory = _make_plan(args, species, inventory_path)
    Path(output).write_text(render_html(build_view(result, inventory, species, source)), encoding="utf-8")
    print(f"Dashboard -> {output}", file=sys.stderr)
    if getattr(args, "open", False):
        webbrowser.open(Path(output).resolve().as_uri())


def cmd_plan(args) -> None:
    """Handle `pogo-evo-planner plan`."""
    _run_plan(args, _species(args), args.inventory)


def cmd_dashboard(args) -> None:
    """Handle `pogo-evo-planner dashboard`."""
    _write_dashboard(args, _species(args), args.inventory, args.output, str(args.inventory))


def _read_summary(inventory: dict, report) -> str:
    """One line on what was read: the species, and "all 210 Pokémon your search found" once every one was."""
    shown = sorted({pokemon["species"] for pokemon in inventory["pokemon"]})
    every = f"all {report.listed} Pokémon your search found, " if report.listed and report.grid >= report.listed else ""
    return (
        f"Read {every}{len(shown)} species: {', '.join(species_id.replace('_', ' ').title() for species_id in shown)}"
    )


def _progress(args):
    """A callback that overwrites one stderr line with the frame being read; None with --quiet."""
    if args.quiet:
        return None

    def show(index, total, label, kind):
        """Overwrite one stderr line with the frame being read."""
        print(f"\r  [{index}/{total}] {label}: {kind:<8}", end="", file=sys.stderr, flush=True)

    return show


def _read_screens(args, files: list[str], species: dict, **scan_options) -> tuple[dict, Any]:
    """Scan the files with the reader --ocr picks. Raises on a file it can't read."""
    from .scan.pipeline import scan  # OpenCV and the OCR or LLM packages are only needed here

    return scan(
        files,
        species,
        backend=args.ocr,
        fps=args.fps,
        max_llm_frames=args.llm_max_frames,
        llm_model=args.llm_model,
        **scan_options,
    )


def _scan(args, files: list[str], species: dict) -> tuple[dict, Any]:
    """Scan the files, showing progress unless --quiet. Exits with a readable error instead of a traceback when a file
    can't be read, no OCR engine is installed, or the packages for reading screens aren't installed."""
    try:
        result = _read_screens(args, files, species, progress=_progress(args))
    except (ValueError, RuntimeError) as error:
        sys.exit(f"error: {error}")
    except ModuleNotFoundError as error:
        extra = "llm" if args.ocr in LLM_READERS else "ocr"
        what = f"--ocr {args.ocr}" if extra == "llm" else "reading screens"
        sys.exit(
            f"error: {what} needs the {error.name} module, which isn't installed. "
            f"In the project folder, run: pip install -e '.[{extra}]'"
        )
    if not args.quiet:
        print(file=sys.stderr)  # end the progress line
    return result


def _read_evolvenew(args, species: dict) -> tuple[dict, Any]:
    """Read the files and apply --candy, --item and --rare-candy, work out what's missing, save it to --output and
    say what was read. Returns the inventory dict and the evolvenew listing."""
    from .evolvenew import apply_evolvenew, candy_overrides, item_overrides

    try:
        candy = candy_overrides(args.candy, species)
        items = item_overrides(args.item, species)
    except ValueError as error:
        sys.exit(f"error: {error}")
    inventory, report = _scan(args, args.files, species)
    inventory["candy"].update(candy)  # counts typed on the command line win over what was read
    inventory["items"].update(items)
    if args.rare_candy is not None:
        inventory["rare_candy"] = args.rare_candy
    elif not report.screens["bag"]:
        sys.exit("error: no Rare Candy count: add --rare-candy N, or a screenshot of your item bag")
    inventory, listing = apply_evolvenew(inventory, species)
    _save(args.output, inventory)

    print(_read_summary(inventory, report), file=sys.stderr)
    for warning in report.warnings:  # the plan covers missing candy and Rare Candy itself
        if not warning.startswith(("no candy count for", "no item bag screen")):
            print(f"  ! {warning}", file=sys.stderr)
    print(f"Saved -> {args.output} (fix a misread count with --candy NAME=N)\n", file=sys.stderr)
    return inventory, listing


def _ask_and_replan(args, species: dict, inventory: dict, plan_inv: Inventory) -> tuple[Plan, Inventory] | None:
    """Ask for the missing candy counts and evolution items, save the answers and plan again with them.
    None when nothing was given."""
    from .evolvenew import ask_for_candy, ask_for_items

    def read_candy(paths: list[str]) -> dict[str, int]:
        """Read candy counts from detail-page screenshots; raises, so a bad path is asked again."""
        return _read_screens(args, paths, species)[0]["candy"]

    candy_given = ask_for_candy(plan_inv, species, ask=input, read=read_candy, say=print)
    items_given = ask_for_items(plan_inv, species, ask=input, say=print)
    if not (candy_given or items_given):
        return None
    inventory["candy"].update(candy_given)
    inventory["items"].update(items_given)
    _save(args.output, inventory)
    given = [f"--candy {family}={count}" for family, count in sorted(candy_given.items())] + [
        f'--item "{item_name(item)}={count}"' for item, count in sorted(items_given.items())
    ]
    print(f"\nSaved your answers to {args.output}. To skip the questions next time: {' '.join(given)}\n")
    return _make_plan(args, species, args.output)


def cmd_evolvenew(args) -> None:
    """Handle `pogo-evo-planner evolvenew`: read the game's `evolvenew` results, the Pokédex "Seen" list and the item
    bag, then print the species you're missing and where Rare Candy goes, asking for any counts it needs."""
    from .evolvenew import items_needed, unknown_candy
    from .evolvenew import render as render_evolvenew

    species = _species(args)
    inventory, listing = _read_evolvenew(args, species)
    result, plan_inv = _make_plan(args, species, args.output)
    needs_answers = unknown_candy(plan_inv, species) or items_needed(plan_inv, species)
    if needs_answers and not args.no_ask and sys.stdin.isatty():
        # show what's missing, ask for the candy counts and items it needs, then plan with the answers
        print(render_evolvenew(result, plan_inv, species, listing, show_plan=False))
        result, plan_inv = _ask_and_replan(args, species, inventory, plan_inv) or (result, plan_inv)
        print(render_evolvenew(result, plan_inv, species, listing, show_missing=False), end="")
    else:
        print(render_evolvenew(result, plan_inv, species, listing), end="")
    if args.dashboard:
        _write_dashboard(args, species, args.output, args.dashboard, ", ".join(Path(path).name for path in args.files))


def cmd_scan(args) -> None:
    """Handle `pogo-evo-planner scan`: OCR the files, write the inventory, then plan or write a dashboard if asked."""
    species = _species(args)
    inventory, report = _scan(args, args.files, species)
    _save(args.output, inventory)

    summary = ", ".join(f"{kind}: {count}" for kind, count in sorted(report.screens.items()))
    print(f"Read {report.frames_read} frames ({summary})", file=sys.stderr)
    print(
        f"Found {len(inventory['pokemon'])} Pokémon, candy for {len(inventory['candy'])} families, "
        f"{len(inventory['pokedex'])} Pokédex entries, {inventory['rare_candy']} Rare Candy -> {args.output}",
        file=sys.stderr,
    )
    for warning in report.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    print(
        'Review the file (OCR makes mistakes): add candy counts the scan missed under "candy" (a family '
        'without one gets no Rare Candy) and evolution items under "items".',
        file=sys.stderr,
    )
    if args.plan:
        print(file=sys.stderr)
        _run_plan(args, species, args.output)
    if args.dashboard:
        _write_dashboard(args, species, args.output, args.dashboard, ", ".join(Path(path).name for path in args.files))


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the evolvenew, scan, plan and dashboard subcommands."""
    parser = argparse.ArgumentParser(
        prog="pogo-evo-planner", description="Plan Pokémon GO evolutions and rare candy use."
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    plan_parser = subparsers.add_parser("plan", help="recommend evolutions from an inventory JSON")
    plan_parser.add_argument("inventory")
    plan_parser.add_argument("--pokemon-csv", help="CSV of owned Pokémon (overrides the inventory list)")
    _add_data_args(plan_parser)
    _add_plan_args(plan_parser)
    plan_parser.set_defaults(func=cmd_plan)

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="write the plan for an inventory JSON as an HTML dashboard"
    )
    dashboard_parser.add_argument("inventory")
    dashboard_parser.add_argument("-o", "--output", default="dashboard.html")
    dashboard_parser.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    dashboard_parser.add_argument("--pokemon-csv", help="CSV of owned Pokémon (overrides the inventory list)")
    _add_data_args(dashboard_parser)
    _add_plan_args(dashboard_parser)
    dashboard_parser.set_defaults(func=cmd_dashboard)

    evolvenew_parser = subparsers.add_parser(
        "evolvenew",
        help="plan from the Pokémon the game's `evolvenew` search lists (quickest)",
        description="In Pokémon GO, search `evolvenew` in your Pokémon storage and screen-record "
        "yourself scrolling through the results, or screenshot them as you scroll "
        "(overlap a row or two). Add screenshots of the Pokédex filtered to Seen and "
        "your item bag (or --rare-candy).",
    )
    evolvenew_parser.add_argument(
        "files",
        nargs="+",
        help="a recording or screenshots of the evolvenew results, Pokédex Seen screenshots, the item "
        "bag, and any detail pages",
    )
    evolvenew_parser.add_argument("--rare-candy", type=int, help="your Rare Candy count (instead of a bag screenshot)")
    evolvenew_parser.add_argument(
        "--candy",
        action="append",
        default=[],
        metavar="NAME=N",
        help="set or fix a candy count, e.g. --candy NACLI=72 or --candy Naclstack=72 (repeatable)",
    )
    evolvenew_parser.add_argument(
        "--item",
        action="append",
        default=[],
        metavar="NAME=N",
        help='evolution items you hold, e.g. --item "Tart Apple=26" --item "Metal Coat=1" (repeatable)',
    )
    evolvenew_parser.add_argument("-o", "--output", default="evolvenew.json", help="where to save what was read")
    evolvenew_parser.add_argument(
        "--no-ask",
        action="store_true",
        help="don't ask for missing candy counts; just list them (asking only happens in a terminal)",
    )
    _add_scan_args(evolvenew_parser)
    _add_data_args(evolvenew_parser)
    _add_plan_args(evolvenew_parser)
    evolvenew_parser.set_defaults(func=cmd_evolvenew, new_entries_only=True)

    scan_parser = subparsers.add_parser("scan", help="OCR screenshots / screen recordings into an inventory JSON")
    scan_parser.add_argument("files", nargs="+", help="screenshots (.png/.jpg) and/or recordings (.mp4/.mov)")
    scan_parser.add_argument("-o", "--output", default="inventory.json")
    scan_parser.add_argument("--plan", action="store_true", help="run the planner on the result")
    _add_scan_args(scan_parser)
    _add_data_args(scan_parser)
    _add_plan_args(scan_parser)
    scan_parser.set_defaults(func=cmd_scan)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the chosen subcommand."""
    argv = sys.argv[1:] if argv is None else argv
    # backwards compatible: `pogo-evo-planner inventory.json` == `pogo-evo-planner plan inventory.json`
    if argv and argv[0] not in ("plan", "scan", "dashboard", "evolvenew", "-h", "--help"):
        argv = ["plan", *argv]
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
