"""Pull distinct, sharp frames out of a screen recording."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def _thumb(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (90, 195), interpolation=cv2.INTER_AREA).astype(np.int16)


def _sharpness(img: np.ndarray) -> float:
    return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def same_screen(a: np.ndarray, b: np.ndarray, threshold: float = 1.0) -> bool:
    """Mean absolute difference of small greyscale thumbnails.

    Two different Pokémon detail pages share most of their layout, so the
    threshold is low: changing just a name and a few numbers is enough to
    count as a new screen, while video compression noise is not.
    """
    return float(np.abs(a - b).mean()) < threshold


def extract_frames(video: str | Path, fps: float = 3.0, threshold: float = 1.0,
                   max_frames: int = 600) -> list[np.ndarray]:
    """Sample the video, group runs of near-identical frames, keep the sharpest of each run.

    Scrolling produces a run of different frames (all kept, so overlapping
    content gets read more than once; the parsers de-duplicate). Pausing on a
    screen produces one run, kept once.
    """
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(video)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(src_fps / fps))
    groups: list[list[np.ndarray]] = []
    last = None
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            t = _thumb(frame)
            if last is not None and same_screen(t, last, threshold):
                groups[-1].append(frame)
            else:
                groups.append([frame])
            last = t
        i += 1
    cap.release()
    return [max(g, key=_sharpness) for g in groups][:max_frames]
