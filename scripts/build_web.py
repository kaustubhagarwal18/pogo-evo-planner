"""Build the browser app into one folder that any static web host can serve.

    python scripts/build_web.py
    python scripts/build_web.py --species data/latest.json -o site

The folder gets the page (web/), the planner package as planner.zip (the page unpacks it into
Pyodide), the normalized species table as species.json, and the example inventory. Nothing personal
goes in: only the package, the game data and the bundled example. Needs the package installed
(`make install`), and reads the species from data/latest.json when `make gamemaster` has fetched it.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

from pogo_evo_planner.gamemaster import load_species

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "pogo_evo_planner"
WEB = ROOT / "web"
EXAMPLE = ROOT / "examples" / "inventory_sample.json"
SAMPLE_SPECIES = PACKAGE / "data" / "species_sample.json"
LATEST = ROOT / "data" / "latest.json"


def package_zip() -> bytes:
    """The planner package as zip bytes: its Python files and bundled data, the same bytes for the same files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".json", ".html"):
                info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix())  # fixed date, so the build id is stable
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def site_files(species_path: Path) -> tuple[dict[str, bytes], int]:
    """Every file of the site by name, with index.html stamped with a build id, and the number of species."""
    try:
        species = load_species(species_path)
    except (ValueError, KeyError) as error:  # not JSON, or a game master entry missing a field
        raise ValueError(f"{species_path} isn't a valid species table: {error}") from error
    files = {path.name: path.read_bytes() for path in sorted(WEB.iterdir()) if path.is_file()}
    files["planner.zip"] = package_zip()
    files["species.json"] = json.dumps(species, separators=(",", ":")).encode()
    files["inventory_sample.json"] = EXAMPLE.read_bytes()
    # the build id changes whenever any file does, so browsers fetch the new files instead of cached ones
    build = hashlib.sha256(b"".join(name.encode() + data for name, data in sorted(files.items()))).hexdigest()[:12]
    files["index.html"] = files["index.html"].replace(b"{{BUILD}}", build.encode())
    return files, len(species)


def write_site(folder: Path, files: dict[str, bytes]) -> None:
    """Replace the folder with the site. Refuses a folder that has other things in it and isn't an earlier build."""
    if folder.exists():
        if not folder.is_dir() or (any(folder.iterdir()) and not (folder / "planner.zip").exists()):
            raise ValueError(f"{folder} already exists and isn't a site this script built; pick another --output")
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


def main(argv: list[str] | None = None) -> None:
    """Build the site and say what went into it."""
    parser = argparse.ArgumentParser(description="Build the browser app into a folder for a static web host.")
    parser.add_argument(
        "--species",
        type=Path,
        default=LATEST if LATEST.exists() else SAMPLE_SPECIES,
        help="PokeMiners latest.json or a normalized species table "
        "(default: data/latest.json from `make gamemaster`, else the small bundled sample)",
    )
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "site", help="folder to write (default: site/)")
    args = parser.parse_args(argv)
    try:
        files, species_count = site_files(args.species)
        write_site(args.output, files)
    except OSError as error:
        where = f": {error.filename}" if error.filename else ""
        sys.exit(f"error: can't build the site: {error.strerror or error}{where}")
    except ValueError as error:  # a species file that isn't JSON, or an output folder that isn't ours
        sys.exit(f"error: {error}")
    sample = " (the bundled sample; run `make gamemaster` for every species)" if args.species == SAMPLE_SPECIES else ""
    print(
        f"Built {args.output}: {species_count} species from {args.species}{sample}, "
        f"{sum(map(len, files.values())) // 1024} KB"
    )


if __name__ == "__main__":
    main()
