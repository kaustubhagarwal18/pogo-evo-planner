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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(PACKAGE.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix in (".py", ".json", ".html"):
                info = zipfile.ZipInfo(f.relative_to(ROOT).as_posix())   # fixed date, so the build id is stable
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, f.read_bytes())
    return buf.getvalue()


def site_files(species_path: Path) -> tuple[dict[str, bytes], int]:
    """Every file of the site by name, with index.html stamped with a build id, and the number of species."""
    species = load_species(species_path)
    files = {f.name: f.read_bytes() for f in sorted(WEB.iterdir()) if f.is_file()}
    files["planner.zip"] = package_zip()
    files["species.json"] = json.dumps(species, separators=(",", ":")).encode()
    files["inventory_sample.json"] = EXAMPLE.read_bytes()
    # the build id changes whenever any file does, so browsers fetch the new files instead of cached ones
    build = hashlib.sha256(b"".join(name.encode() + data for name, data in sorted(files.items()))).hexdigest()[:12]
    files["index.html"] = files["index.html"].replace(b"{{BUILD}}", build.encode())
    return files, len(species)


def write_site(out: Path, files: dict[str, bytes]) -> None:
    """Replace the folder with the site. Refuses a folder that has other things in it and isn't an earlier build."""
    if out.exists():
        if not out.is_dir() or (any(out.iterdir()) and not (out / "planner.zip").exists()):
            raise ValueError(f"{out} already exists and isn't a site this script built; pick another --output")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, data in files.items():
        (out / name).write_bytes(data)


def main(argv: list[str] | None = None) -> None:
    """Build the site and say what went into it."""
    ap = argparse.ArgumentParser(description="Build the browser app into a folder for a static web host.")
    ap.add_argument("--species", type=Path, default=LATEST if LATEST.exists() else SAMPLE_SPECIES,
                    help="PokeMiners latest.json or a normalized species table "
                         "(default: data/latest.json from `make gamemaster`, else the small bundled sample)")
    ap.add_argument("-o", "--output", type=Path, default=ROOT / "site", help="folder to write (default: site/)")
    a = ap.parse_args(argv)
    try:
        files, n = site_files(a.species)
        write_site(a.output, files)
    except OSError as e:
        sys.exit(f"error: can't build the site: {e.strerror or e}: {e.filename}")
    except ValueError as e:   # a species file that isn't JSON, or an output folder that isn't ours
        sys.exit(f"error: {e}")
    sample = " (the bundled sample; run `make gamemaster` for every species)" if a.species == SAMPLE_SPECIES else ""
    print(f"Built {a.output}: {n} species from {a.species}{sample}, {sum(map(len, files.values())) // 1024} KB")


if __name__ == "__main__":
    main()
