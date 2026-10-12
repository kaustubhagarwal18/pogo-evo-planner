"""OCR returning word boxes, with pluggable backends.

Backends, in order of preference for `backend="auto"`:
  rapidocr   - RapidOCR (PaddleOCR models on ONNX Runtime). Best on game UI text.
               Install with `pip install rapidocr onnxruntime` (or the older
               `rapidocr_onnxruntime` package; both APIs are supported).
  tesseract  - the Tesseract command line. Weaker on stylised text, but no
               Python dependencies; used as the fallback and in CI.

The browser app reads screens with tesseract.js instead: it reads the same passes
(tesseract_passes) with the same model, and its TSV goes through words_from_tsv.

RapidOCR returns whole text lines, while the screen parsers work on words, so
lines are split into words with x positions estimated from character offsets.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess  # only runs the tesseract CLI on our own temp file  # nosec B404
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
    x: int  # left, in original image pixels
    y: int  # top
    w: int
    h: int
    conf: float

    @property
    def cx(self) -> float:
        """Horizontal centre of the word box."""
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        """Vertical centre of the word box."""
        return self.y + self.h / 2


# ---------------- tesseract ----------------


def tesseract_available() -> bool:
    """True when the tesseract binary is on PATH."""
    return shutil.which("tesseract") is not None


_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def words_from_tsv(tsv: str, scale: float = 1.0) -> list[Word]:
    """Words from Tesseract's TSV output, with boxes scaled back to original pixels. The header row is optional:
    the command line writes one, tesseract.js doesn't."""
    if not tsv.startswith("level"):
        tsv = _TSV_HEADER + tsv
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        confidence = float(row.get("conf") or -1)
        if not text or confidence < 0:
            continue
        words.append(
            Word(
                text,
                int(int(row["left"]) / scale),
                int(int(row["top"]) / scale),
                int(int(row["width"]) / scale),
                int(int(row["height"]) / scale),
                confidence / 100,
            )
        )
    return words


def tesseract_passes(image: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """The images Tesseract reads for one screen, each with the scale it was resized by. Sparse-text mode silently
    drops lines now and then, and which ones it drops depends on scale and polarity, so it reads three."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # phone screenshots are ~1080 px wide; an upscaled pass helps small UI text
    scale = max(1.0, 1600 / gray.shape[1])
    big = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # game text is both light-on-dark and dark-on-light, so read both polarities
    return [(gray, 1.0), (255 - gray, 1.0), (big, scale)]


def merge_passes(passes: list[list[Word]]) -> list[Word]:
    """The words of several passes over one screen: the same text at roughly the same place counts once."""
    kept: list[Word] = []
    for word in sorted((word for words in passes for word in words), key=lambda word: -word.conf):
        if any(
            kept_word.text.upper() == word.text.upper()
            and abs(kept_word.cx - word.cx) < max(kept_word.w, 8)
            and abs(kept_word.cy - word.cy) < max(kept_word.h, 8)
            for kept_word in kept
        ):
            continue
        kept.append(word)
    return kept


def _run_tesseract(image: np.ndarray, scale: float, segmentation_mode: int) -> list[Word]:
    """One Tesseract pass over an image, with boxes scaled back to original pixels."""
    with tempfile.NamedTemporaryFile(suffix=".png") as png_file:
        cv2.imwrite(png_file.name, image)
        try:
            tsv = subprocess.run(  # fixed argv, no shell, tesseract from PATH  # nosec B603 B607
                ["tesseract", png_file.name, "stdout", "--psm", str(segmentation_mode), "-l", "eng", "tsv"],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},  # tesseract's threading is slower on small images
            ).stdout
        except subprocess.CalledProcessError as error:  # reported as one line by the commands, like a missing engine
            stderr_lines = (error.stderr or "").strip().splitlines()
            reason = stderr_lines[-1] if stderr_lines else f"exit status {error.returncode}"
            raise RuntimeError(f"Tesseract failed on a screen: {reason}") from error
    return words_from_tsv(tsv, scale)


def _tesseract_words(image: np.ndarray, segmentation_mode: int = 11) -> list[Word]:
    """OCR an image with the Tesseract command line: every pass, merged."""
    passes = tesseract_passes(image)
    with ThreadPoolExecutor(max_workers=len(passes)) as executor:
        results = executor.map(
            lambda image_and_scale: _run_tesseract(image_and_scale[0], image_and_scale[1], segmentation_mode), passes
        )
    return merge_passes(list(results))


# ---------------- rapidocr ----------------


@lru_cache(maxsize=1)
def _rapidocr_engine():
    """Create the RapidOCR engine once and reuse it; supports both package names."""
    try:
        from rapidocr import RapidOCR  # rapidocr >= 2
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR  # older package name
    return RapidOCR()


def rapidocr_available() -> bool:
    """True when RapidOCR imports and its models load."""
    try:
        _rapidocr_engine()
        return True
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # import or ONNX init can fail many ways
        return False


def _split_line(box, text: str, score: float) -> list[Word]:
    """Split a RapidOCR text line into words, estimating each word's box from its character position."""
    points = np.asarray(box, dtype=float).reshape(-1, 2)
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    length = max(len(text), 1)
    words, end = [], 0
    for token in text.split():
        start = text.index(token, end)
        end = start + len(token)
        word_left = left + (right - left) * start / length
        word_right = left + (right - left) * end / length
        words.append(
            Word(
                token,
                int(word_left),
                int(top),
                max(1, int(word_right - word_left)),
                max(1, int(bottom - top)),
                float(score),
            )
        )
    return words


def _rapidocr_words(image: np.ndarray) -> list[Word]:
    """OCR an image with RapidOCR and return word-level boxes."""
    output = _rapidocr_engine()(image)
    if isinstance(output, tuple):  # rapidocr_onnxruntime: (result, elapse); result = [[box, text, score], ...]
        lines = [tuple(row) for row in (output[0] or [])]
    else:  # rapidocr >= 2: object with boxes / txts / scores
        if output.boxes is None:
            return []
        lines = list(zip(output.boxes, output.txts, output.scores))
    words: list[Word] = []
    for box, text, score in lines:
        words.extend(_split_line(box, str(text), float(score)))
    return words


# ---------------- entry point ----------------


def available_backend(preferred: str = "auto") -> str:
    """Pick the OCR backend to use, raising with install hints when none is available."""
    if preferred in ("rapidocr", "auto") and rapidocr_available():
        return "rapidocr"
    if preferred in ("tesseract", "auto") and tesseract_available():
        return "tesseract"
    if preferred == "rapidocr":
        raise RuntimeError("RapidOCR isn't installed. In the project folder, run: pip install -e '.[ocr]'")
    raise RuntimeError(
        "no OCR engine found. In the project folder, run: pip install -e '.[ocr]' "
        "(RapidOCR), or install Tesseract (brew install tesseract / apt install tesseract-ocr)"
    )


def read_words(image: np.ndarray | str | Path, backend: str = "auto") -> list[Word]:
    """OCR an image (array or path) and return words with pixel boxes."""
    pixels = image if isinstance(image, np.ndarray) else cv2.imread(str(image))
    if pixels is None:
        raise FileNotFoundError(image)
    if available_backend(backend) == "rapidocr":
        return _rapidocr_words(pixels)
    return _tesseract_words(pixels)
