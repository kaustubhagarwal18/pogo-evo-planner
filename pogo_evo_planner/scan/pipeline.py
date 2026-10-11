"""Turn screenshots and screen recordings into a planner inventory."""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..inventory import inventory_from_dict
from ..planner import families_missing_candy
from .frames import extract_frames
from .ocr import read_words
from .screens import ScreenResult, SpeciesMatcher, parse

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".3gp", ".avi"}


@dataclass
class ScanReport:
    frames_read: int = 0
    screens: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


def load_images(paths: list[str | Path], fps: float = 3.0) -> list[tuple[str, np.ndarray]]:
    """Expand a mix of image and video paths into (label, frame) pairs."""
    out = []
    for p in map(Path, paths):
        if p.suffix.lower() in VIDEO_EXT:
            for i, frame in enumerate(extract_frames(p, fps=fps)):
                out.append((f"{p.name}#{i}", frame))
        else:
            img = cv2.imread(str(p))
            if img is None:
                raise ValueError(f"can't read {p}: not found, or not an image")
            out.append((p.name, img))
    return out


_STAMP = re.compile(r"(20\d{2})(\d{2})(\d{2})[-_ ]?(\d{2})(\d{2})(\d{2})")


def taken_at(path: str | Path) -> float:
    """When a screenshot or recording was taken: the date and time in its file name
    (Screenshot_20261011-115015.png, VID_20261004_225901.mp4), else when the file was last modified."""
    m = _STAMP.search(Path(path).name)
    if m:
        try:
            # phones name files in local time, with no time zone; strptime rejects impossible dates
            return time.mktime(time.strptime("".join(m.groups()), "%Y%m%d%H%M%S"))
        except ValueError:
            pass
    return Path(path).stat().st_mtime


def _pick(name: str, votes: dict[float, Counter], report: ScanReport) -> int:
    """One count from readings grouped by when their capture was taken: the newest capture's most
    common reading. Counts change over time, so an older capture loses; within one capture, the
    most common reading outvotes a misread frame."""
    newest = max(votes)
    value = votes[newest].most_common(1)[0][0]
    every = sum(votes.values(), Counter())
    if len(every) > 1:
        how = "from the newest capture" if len(votes) > 1 else "the most common"
        report.warnings.append(f"conflicting {name} readings {dict(every)}; using {value}, {how}")
    return value


def merge(results: list[ScreenResult], report: ScanReport, times: list[float] | None = None) -> dict:
    """Combine per-screen readings into one inventory dict.

    times gives when each result's capture was taken (see taken_at); a count read differently in
    different captures takes the newest. Without it, every result counts as taken at once.
    """
    times = times or [0.0] * len(results)
    rare: dict[float, Counter] = defaultdict(Counter)
    candy_votes: dict[str, dict[float, Counter]] = defaultdict(lambda: defaultdict(Counter))
    with_cp: set[tuple[str, int]] = set()
    no_cp: Counter = Counter()
    seen: set[str] = set()
    shown: set[str] = set()             # Pokémon whose own detail page was captured: you own one
    caught_votes: Counter = Counter()   # frames showing the species as caught (colour sprite, storage...)
    never_votes: Counter = Counter()    # frames showing it as never caught (silhouette, "Seen" list)
    for r, when in zip(results, times):
        if r.rare_candy is not None:
            rare[when][r.rare_candy] += 1
        for fam, n in r.candy.items():
            candy_votes[fam][when][n] += 1
        frame_no_cp: Counter[str] = Counter()
        for sp, cp in r.specimens:
            if cp is None:
                frame_no_cp[sp] += 1
            else:
                with_cp.add((sp, cp))
        for sp, n in frame_no_cp.items():  # can't de-duplicate across frames without CP
            no_cp[sp] = max(no_cp[sp], n)
        if r.kind == "detail" and r.species:
            shown.add(r.species)
        caught_votes.update(r.dex_caught)
        seen |= r.dex_seen_only
        never_votes.update(r.dex_not_caught)
        report.warnings.extend(r.notes)

    rare_candy = _pick("Rare Candy", rare, report) if rare else 0
    candy = {fam: _pick(f"{fam} candy", votes, report) for fam, votes in candy_votes.items()}

    # majority vote per species: a single misread frame (a sprite still loading, a green Pokémon on a
    # green button) no longer decides on its own
    caught = {sp for sp in caught_votes if caught_votes[sp] > never_votes[sp]}
    not_caught = {sp for sp in never_votes if never_votes[sp] > caught_votes[sp]}
    for sp in sorted(set(caught_votes) & set(never_votes) - caught - not_caught):
        report.warnings.append(f"{sp}: as many frames show it caught as never caught; left out of both lists")

    pokemon = [{"species": sp, "cp": cp, "label": f"CP {cp}"} for sp, cp in sorted(with_cp)]
    covered = Counter(sp for sp, _ in with_cp)
    for sp, n in sorted(no_cp.items()):
        pokemon += [{"species": sp, "cp": None, "label": "CP ?"}] * max(0, n - covered[sp])
    # a detail page proves you own that Pokémon, even when the storage grid wasn't captured
    listed = {p["species"] for p in pokemon}
    pokemon += [{"species": sp, "cp": None, "label": "detail page"} for sp in sorted(shown - listed)]

    return {
        "rare_candy": rare_candy,
        "candy": dict(sorted(candy.items())),
        "items": {},
        "pokedex": sorted(caught),
        "pokedex_seen_only": sorted(seen - caught),
        # never caught: a silhouette on an EVOLVE button, or listed under the Pokédex "Seen" filter
        "not_caught": sorted(not_caught),
        "pokemon": pokemon,
    }


def scan(paths: list[str | Path], species: dict, backend: str = "auto", fps: float = 3.0,
         progress=None, max_llm_frames: int = 60, llm_model: str | None = None) -> tuple[dict, ScanReport]:
    """OCR every screenshot and recording frame, parse each screen, and merge them into an inventory and report.

    backend "llm" (= "ollama", local and free) or "claude" reads the screens with a vision LLM instead of
    OCR (see scan/llm.py).
    Claude is paid per frame, so more than max_llm_frames frames is refused rather than sent.
    """
    matcher = SpeciesMatcher(species)
    report = ScanReport()
    frames = load_images(paths, fps=fps)
    if backend in ("llm", "ollama", "claude"):
        if backend == "claude" and len(frames) > max_llm_frames:
            raise ValueError(f"{len(frames)} frames would each be a paid Claude request (limit {max_llm_frames}); "
                             "lower --fps, pass fewer files, or raise --llm-max-frames")
        from . import llm  # needs the `llm` extras
        results = llm.read_screens(frames, matcher, species, llm.make_model(backend, llm_model), progress=progress)
    else:
        results = []
        for i, (label, img) in enumerate(frames):
            res = parse(read_words(img, backend=backend), img, matcher, species)
            results.append(res)
            if progress:
                progress(i + 1, len(frames), label, res.kind)
    report.frames_read = len(results)
    report.screens.update(res.kind for res in results)
    taken = {Path(p).name: taken_at(p) for p in paths}
    inv = merge(results, report, [taken.get(label, taken.get(label.rsplit("#", 1)[0], 0.0)) for label, _ in frames])

    misread = sorted({p["species"] for p in inv["pokemon"]} & set(inv["not_caught"]))
    if misread:
        report.warnings.append("storage shows " + ", ".join(misread) + " but an EVOLVE button shows it as never "
                               "caught; probably misread from the grid, so the planner treats it as not owned")
    if not report.screens["bag"]:
        report.warnings.append("no item bag screen found: Rare Candy set to 0")
    missing = families_missing_candy(inventory_from_dict(inv), species)
    if missing:
        report.warnings.append("no candy count for: " + ", ".join(missing) + " (these can still evolve into a "
                               "species you don't have; open one detail screen per family, or add them by hand)")
    return inv, report
