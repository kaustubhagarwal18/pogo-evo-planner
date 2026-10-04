"""OCR returning word boxes, with pluggable backends.

Backends, in order of preference for `backend="auto"`:
  rapidocr   - RapidOCR (PaddleOCR models on ONNX Runtime). Best on game UI text.
               Install with `pip install rapidocr onnxruntime` (or the older
               `rapidocr_onnxruntime` package; both APIs are supported).
  tesseract  - the Tesseract command line. Weaker on stylised text, but no
               Python dependencies; used as the fallback and in CI.

RapidOCR returns whole text lines, while the screen parsers work on words, so
lines are split into words with x positions estimated from character offsets.
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Word:
    text: str
    x: int   # left, in original image pixels
    y: int   # top
    w: int
    h: int
    conf: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


# ---------------- tesseract ----------------

def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _run_tesseract(img: np.ndarray, scale: float, psm: int) -> list[Word]:
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        cv2.imwrite(f.name, img)
        out = subprocess.run(
            ["tesseract", f.name, "stdout", "--psm", str(psm), "-l", "eng", "tsv"],
            capture_output=True, text=True, check=True,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},  # tesseract's threading is slower on small images
        ).stdout
    words = []
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        conf = float(row.get("conf") or -1)
        if not text or conf < 0:
            continue
        words.append(Word(text, int(int(row["left"]) / scale), int(int(row["top"]) / scale),
                          int(int(row["width"]) / scale), int(int(row["height"]) / scale), conf / 100))
    return words


def _dedupe(words: list[Word]) -> list[Word]:
    """Merge passes: the same text at roughly the same place counts once."""
    kept: list[Word] = []
    for w in sorted(words, key=lambda w: -w.conf):
        if any(k.text.upper() == w.text.upper() and abs(k.cx - w.cx) < max(k.w, 8) and abs(k.cy - w.cy) < max(k.h, 8)
               for k in kept):
            continue
        kept.append(w)
    return kept


def _tesseract_words(img: np.ndarray, psm: int = 11) -> list[Word]:
    """Several passes merged: sparse-text mode silently drops lines now and then,
    and which ones it drops depends on scale and polarity."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # phone screenshots are ~1080 px wide; an upscaled pass helps small UI text
    scale = max(1.0, 1600 / gray.shape[1])
    big = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # game text is both light-on-dark and dark-on-light, so read both polarities
    passes = [(gray, 1.0), (255 - gray, 1.0), (big, scale)]
    with ThreadPoolExecutor(max_workers=len(passes)) as ex:
        results = ex.map(lambda p: _run_tesseract(p[0], p[1], psm), passes)
    return _dedupe([w for ws in results for w in ws])


# ---------------- rapidocr ----------------

@lru_cache(maxsize=1)
def _rapidocr_engine():
    try:
        from rapidocr import RapidOCR  # rapidocr >= 2
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR  # older package name
    return RapidOCR()


def rapidocr_available() -> bool:
    try:
        _rapidocr_engine()
        return True
    except Exception:  # noqa: BLE001 - import or ONNX model init can fail many ways; fall back
        return False


def _split_line(box, text: str, score: float) -> list[Word]:
    pts = np.asarray(box, dtype=float).reshape(-1, 2)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    n = max(len(text), 1)
    words, pos = [], 0
    for token in text.split():
        start = text.index(token, pos)
        pos = start + len(token)
        wx0 = x0 + (x1 - x0) * start / n
        wx1 = x0 + (x1 - x0) * pos / n
        words.append(Word(token, int(wx0), int(y0), max(1, int(wx1 - wx0)), max(1, int(y1 - y0)), float(score)))
    return words


def _rapidocr_words(img: np.ndarray) -> list[Word]:
    out = _rapidocr_engine()(img)
    if isinstance(out, tuple):  # rapidocr_onnxruntime: (result, elapse); result = [[box, text, score], ...]
        lines = [(b, t, s) for b, t, s in (out[0] or [])]
    else:                      # rapidocr >= 2: object with boxes / txts / scores
        if out.boxes is None:
            return []
        lines = list(zip(out.boxes, out.txts, out.scores))
    words: list[Word] = []
    for box, text, score in lines:
        words.extend(_split_line(box, str(text), float(score)))
    return words


# ---------------- entry point ----------------

def available_backend(preferred: str = "auto") -> str:
    if preferred in ("rapidocr", "auto") and rapidocr_available():
        return "rapidocr"
    if preferred in ("tesseract", "auto") and tesseract_available():
        return "tesseract"
    if preferred == "rapidocr":
        raise RuntimeError("RapidOCR not installed: pip install 'rarecandy[ocr]'")
    raise RuntimeError("No OCR backend found: install RapidOCR (pip install 'rarecandy[ocr]') or Tesseract")


def read_words(image: np.ndarray | str | Path, backend: str = "auto") -> list[Word]:
    """OCR an image (array or path) and return words with pixel boxes."""
    img = image if isinstance(image, np.ndarray) else cv2.imread(str(image))
    if img is None:
        raise FileNotFoundError(image)
    if available_backend(backend) == "rapidocr":
        return _rapidocr_words(img)
    return _tesseract_words(img)
