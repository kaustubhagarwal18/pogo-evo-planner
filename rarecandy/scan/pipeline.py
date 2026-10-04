"""Turn screenshots and screen recordings into a planner inventory."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

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
                raise FileNotFoundError(p)
            out.append((p.name, img))
    return out


def merge(results: list[ScreenResult], report: ScanReport) -> dict:
    """Combine per-screen readings into one inventory dict."""
    rare = Counter(r.rare_candy for r in results if r.rare_candy is not None)
    candy_votes: dict[str, Counter] = defaultdict(Counter)
    with_cp: set[tuple[str, int]] = set()
    no_cp: Counter = Counter()
    caught, seen = set(), set()
    for r in results:
        for fam, n in r.candy.items():
            candy_votes[fam][n] += 1
        frame_no_cp = Counter()
        for sp, cp in r.specimens:
            if cp is None:
                frame_no_cp[sp] += 1
            else:
                with_cp.add((sp, cp))
        for sp, n in frame_no_cp.items():  # can't de-duplicate across frames without CP
            no_cp[sp] = max(no_cp[sp], n)
        caught |= r.dex_caught
        seen |= r.dex_seen_only
        report.warnings.extend(r.notes)

    if len(rare) > 1:
        report.warnings.append(f"conflicting Rare Candy readings {dict(rare)}; using the most common")
    candy = {}
    for fam, votes in candy_votes.items():
        if len(votes) > 1:
            report.warnings.append(f"conflicting {fam} candy readings {dict(votes)}; using the most common")
        candy[fam] = votes.most_common(1)[0][0]

    pokemon = [{"species": sp, "cp": cp, "label": f"CP {cp}"} for sp, cp in sorted(with_cp)]
    covered = Counter(sp for sp, _ in with_cp)
    for sp, n in sorted(no_cp.items()):
        pokemon += [{"species": sp, "cp": None, "label": "CP ?"}] * max(0, n - covered[sp])

    return {
        "rare_candy": rare.most_common(1)[0][0] if rare else 0,
        "candy": dict(sorted(candy.items())),
        "items": {},
        "pokedex": sorted(caught),
        "pokedex_seen_only": sorted(seen - caught),
        "pokemon": pokemon,
    }


def scan(paths: list[str | Path], species: dict, backend: str = "auto", fps: float = 3.0,
         progress=None) -> tuple[dict, ScanReport]:
    matcher = SpeciesMatcher(species)
    report = ScanReport()
    results = []
    frames = load_images(paths, fps=fps)
    for i, (label, img) in enumerate(frames):
        words = read_words(img, backend=backend)
        res = parse(words, img, matcher, species)
        report.frames_read += 1
        report.screens[res.kind] += 1
        results.append(res)
        if progress:
            progress(i + 1, len(frames), label, res.kind)
    inv = merge(results, report)

    if not report.screens["bag"]:
        report.warnings.append("no item bag screen found: Rare Candy set to 0")
    families = {species[p["species"]]["family"] for p in inv["pokemon"] if p["species"] in species}
    missing = sorted(families - set(inv["candy"]))
    if missing:
        report.warnings.append("no candy count for: " + ", ".join(missing)
                               + " (open one detail screen per family in the recording, or add them by hand)")
    return inv, report
