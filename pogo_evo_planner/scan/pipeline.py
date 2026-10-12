"""Turn screenshots and screen recordings into a planner inventory."""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..inventory import inventory_from_dict
from ..planner import families_missing_candy
from . import LLM_READERS
from .frames import extract_frames
from .ocr import Word, read_words
from .screens import ScreenResult, SpeciesMatcher, parse

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".3gp", ".avi"}


@dataclass
class ScanReport:
    frames_read: int = 0
    screens: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    listed: int | None = None  # how many Pokémon the storage search found, from the "(210)" under the tab
    grid: int = 0  # how many of the storage grid's Pokémon were read


def load_images(paths: Sequence[str | Path], fps: float = 3.0) -> list[tuple[str, np.ndarray]]:
    """Expand a mix of image and video paths into (label, frame) pairs."""
    frames = []
    for path in map(Path, paths):
        if not path.is_file():
            raise ValueError(f"can't read {path}: not found")
        if path.suffix.lower() in VIDEO_EXT:
            for index, frame in enumerate(extract_frames(path, fps=fps)):
                frames.append((f"{path.name}#{index}", frame))
        else:
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError(f"can't read {path}: not an image")
            frames.append((path.name, image))
    return frames


_STAMP = re.compile(r"(20\d{2})(\d{2})(\d{2})[-_ ]?(\d{2})(\d{2})(\d{2})")


def taken_at(path: str | Path) -> float:
    """When a screenshot or recording was taken: the date and time in its file name
    (Screenshot_20261011-115015.png, VID_20261004_225901.mp4), else when the file was last modified."""
    stamp = _STAMP.search(Path(path).name)
    if stamp:
        try:
            # phones name files in local time, with no time zone; strptime rejects impossible dates
            return time.mktime(time.strptime("".join(stamp.groups()), "%Y%m%d%H%M%S"))
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
    shown: set[str] = set()  # Pokémon whose own detail page was captured: you own one
    caught_votes: Counter = Counter()  # frames showing the species as caught (colour sprite, storage...)
    never_votes: Counter = Counter()  # frames showing it as never caught (silhouette, "Seen" list)
    for result, when in zip(results, times):
        if result.rare_candy is not None:
            rare[when][result.rare_candy] += 1
        for family, count in result.candy.items():
            candy_votes[family][when][count] += 1
        frame_no_cp: Counter[str] = Counter()
        for species_id, combat_power in result.specimens:
            if combat_power is None:
                frame_no_cp[species_id] += 1
            else:
                with_cp.add((species_id, combat_power))
        for species_id, count in frame_no_cp.items():  # can't de-duplicate across frames without CP
            no_cp[species_id] = max(no_cp[species_id], count)
        if result.kind == "detail" and result.species:
            shown.add(result.species)
        caught_votes.update(result.dex_caught)
        seen |= result.dex_seen_only
        never_votes.update(result.dex_not_caught)
        report.warnings.extend(result.notes)

    rare_candy = _pick("Rare Candy", rare, report) if rare else 0
    candy = {family: _pick(f"{family} candy", votes, report) for family, votes in candy_votes.items()}

    # majority vote per species: a single misread frame (a sprite still loading, a green Pokémon on a
    # green button) no longer decides on its own
    caught = {species_id for species_id in caught_votes if caught_votes[species_id] > never_votes[species_id]}
    not_caught = {species_id for species_id in never_votes if never_votes[species_id] > caught_votes[species_id]}
    for species_id in sorted(set(caught_votes) & set(never_votes) - caught - not_caught):
        report.warnings.append(f"{species_id}: as many frames show it caught as never caught; left out of both lists")

    pokemon = [
        {"species": species_id, "cp": combat_power, "label": f"CP {combat_power}"}
        for species_id, combat_power in sorted(with_cp)
    ]
    covered = Counter(species_id for species_id, _ in with_cp)
    for species_id, count in sorted(no_cp.items()):
        pokemon += [{"species": species_id, "cp": None, "label": "CP ?"}] * max(0, count - covered[species_id])
    found = Counter(result.listed for result in results if result.listed)
    report.listed = found.most_common(1)[0][0] if found else None
    report.grid = len(pokemon)
    if report.listed and report.grid < report.listed:
        report.warnings.append(
            f"read {report.grid} of the {report.listed} Pokémon your search found: rows were skipped between "
            "screenshots or frames. Take screenshots that overlap by a row or two (the floating buttons hide "
            "the bottom row's names), or record the scroll instead"
        )
    # a detail page proves you own that Pokémon, even when the storage grid wasn't captured
    listed = {entry["species"] for entry in pokemon}
    pokemon += [{"species": species_id, "cp": None, "label": "detail page"} for species_id in sorted(shown - listed)]

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


def scan(
    paths: Sequence[str | Path],
    species: dict,
    backend: str = "auto",
    fps: float = 3.0,
    progress=None,
    max_llm_frames: int = 60,
    llm_model: str | None = None,
    read_text: Callable[[str, np.ndarray], list[Word]] | None = None,
) -> tuple[dict, ScanReport]:
    """OCR every screenshot and recording frame, parse each screen, and merge them into an inventory and report.

    backend "llm" (= "ollama", local and free) or "claude" reads the screens with a vision LLM instead of
    OCR (see scan/llm.py).
    Claude is paid per frame, so more than max_llm_frames frames is refused rather than sent.
    read_text(label, image) gives a frame's words in place of OCR: the browser app passes the words
    tesseract.js read.
    """
    matcher = SpeciesMatcher(species)
    report = ScanReport()
    frames = load_images(paths, fps=fps)
    if backend in LLM_READERS:
        if backend == "claude" and len(frames) > max_llm_frames:
            raise ValueError(
                f"{len(frames)} frames would each be a paid Claude request (limit {max_llm_frames}); "
                "lower --fps, pass fewer files, or raise --llm-max-frames"
            )
        from . import llm  # needs the `llm` extras

        results = llm.read_screens(frames, matcher, species, llm.make_model(backend, llm_model), progress=progress)
    else:
        read = read_text or (lambda _label, image: read_words(image, backend=backend))
        results = []
        for index, (label, image) in enumerate(frames):
            result = parse(read(label, image), image, matcher, species)
            results.append(result)
            if progress:
                progress(index + 1, len(frames), label, result.kind)
    report.frames_read = len(results)
    report.screens.update(result.kind for result in results)
    taken = {Path(path).name: taken_at(path) for path in paths}
    inventory = merge(
        results, report, [taken.get(label, taken.get(label.rsplit("#", 1)[0], 0.0)) for label, _ in frames]
    )

    misread = sorted({entry["species"] for entry in inventory["pokemon"]} & set(inventory["not_caught"]))
    if misread:
        report.warnings.append(
            "storage shows " + ", ".join(misread) + " but an EVOLVE button shows it as never "
            "caught; probably misread from the grid, so the planner treats it as not owned"
        )
    if not report.screens["bag"]:
        report.warnings.append("no item bag screen found: Rare Candy set to 0")
    missing = families_missing_candy(inventory_from_dict(inventory), species)
    if missing:
        report.warnings.append(
            "no candy count for: " + ", ".join(missing) + " (these can still evolve into a "
            "species you don't have; open one detail screen per family, or add them by hand)"
        )
    return inventory, report
