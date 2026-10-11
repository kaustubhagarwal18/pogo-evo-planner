"""Recognise Pokémon GO screens from OCR'd words and pull out the numbers we need.

Screens handled:
  bag      - item bag; reads the Rare Candy count
  detail   - a Pokémon's detail page; reads "<FAMILY> CANDY" and the count above it
  pokedex  - Pokédex grid; dex numbers, caught vs. only-seen by sprite colour
  storage  - Pokémon storage grid; species name + CP for each cell

Everything is located relative to text the game always shows rather than
fixed pixel regions, so different phone sizes should mostly work. The
thresholds still need calibrating against real screenshots.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .ocr import Word

try:  # RapidFuzz is faster and better; difflib is the stdlib fallback
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
except ImportError:
    _rf_fuzz = _rf_process = None  # type: ignore[assignment]

_NUM = re.compile(r"^[x×X*]?(\d{1,3}(?:[,.]\d{3})+|\d+)$")
_CP_JOINED = re.compile(r"^CP(\d{2,5})$", re.IGNORECASE)
_DEX_NUM = re.compile(r"^(?:#|No\.?)?0*(\d{1,4})$")


def norm(s: str) -> str:
    """Uppercase and strip everything but letters and digits, for comparing OCR text."""
    return re.sub(r"[^A-Z0-9]", "", s.upper().replace("É", "E"))


def parse_int(text: str) -> int | None:
    """Parse a count like 1,204, 60 or x12; None when the text isn't a number."""
    m = _NUM.match(text.strip())
    return int(re.sub(r"[,.]", "", m.group(1))) if m else None


class SpeciesMatcher:
    """Fuzzy-match OCR text to species ids from the species table."""

    def __init__(self, species: dict, cutoff: float = 0.82):
        """Index species by normalized name (and base name for forms) and by dex number."""
        self.cutoff = cutoff
        self.by_name: dict[str, str] = {}
        for key in species:
            base = key.split("_")[0] if "_" in key and norm(key) not in self.by_name else key
            self.by_name.setdefault(norm(key), key)
            self.by_name.setdefault(norm(base), base if base in species else key)
        self.by_dex = {v["dex"]: k for k, v in species.items() if "dex" in v and "_" not in k}
        self.names = list(self.by_name)

    def match(self, text: str) -> str | None:
        """Return the species id for OCR text, exactly or by fuzzy match; None when unsure."""
        n = norm(text)
        if len(n) < 3:
            return None
        if n in self.by_name:
            return self.by_name[n]
        if len(n) < 5:
            return None  # too short for fuzzy matching to be trustworthy
        if _rf_process is not None:
            hit = _rf_process.extractOne(n, self.names, scorer=_rf_fuzz.ratio, score_cutoff=self.cutoff * 100)
            return self.by_name[hit[0]] if hit else None
        hit = difflib.get_close_matches(n, self.names, n=1, cutoff=self.cutoff)
        return self.by_name[hit[0]] if hit else None


@dataclass
class ScreenResult:
    kind: str
    rare_candy: int | None = None
    candy: dict[str, int] = field(default_factory=dict)          # family -> count
    specimens: list[tuple[str, int | None]] = field(default_factory=list)  # (species, cp)
    dex_caught: set[str] = field(default_factory=set)
    dex_seen_only: set[str] = field(default_factory=set)
    dex_not_caught: set[str] = field(default_factory=set)      # evolve button shows a silhouette
    notes: list[str] = field(default_factory=list)


# ---------- geometry helpers ----------

def _same_line(a: Word, b: Word) -> bool:
    """True when two words' vertical centres are close enough to be on one text line."""
    return abs(a.cy - b.cy) < 0.6 * max(a.h, b.h)


def _lines(words: list[Word]) -> list[list[Word]]:
    """Group words into text lines, top to bottom, each sorted left to right."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x)):
        for row in rows:
            if _same_line(row[0], w):
                row.append(w)
                break
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x) for r in rows]


def _phrases(words: list[Word], max_len: int = 2):
    """Yield (text, words) for single words and runs of adjacent words on a line."""
    for line in _lines(words):
        for i in range(len(line)):
            for n in range(1, max_len + 1):
                run = line[i:i + n]
                if len(run) < n:
                    break
                if n > 1 and run[-1].x - (run[-2].x + run[-2].w) > 2.5 * run[-1].h:
                    break
                yield " ".join(w.text for w in run), run


def _span(ws: list[Word]) -> tuple[float, float, float, float]:
    """Bounding box (x0, y0, x1, y1) around a run of words."""
    x0, x1 = min(w.x for w in ws), max(w.x + w.w for w in ws)
    y0, y1 = min(w.y for w in ws), max(w.y + w.h for w in ws)
    return x0, y0, x1, y1


def _number_above(words: list[Word], anchor: list[Word], max_gap: float = 6.0) -> int | None:
    """Nearest number directly above a label, within max_gap label-heights; None if there isn't one."""
    x0, y0, x1, _ = _span(anchor)
    cx, h = (x0 + x1) / 2, max(w.h for w in anchor)
    best = None
    for w in words:
        v = parse_int(w.text)
        if v is None or w.cy >= y0:
            continue
        if abs(w.cx - cx) > max(x1 - x0, w.w) * 0.75:
            continue
        gap = y0 - (w.y + w.h)
        if gap <= max_gap * h and (best is None or gap < best[0]):  # pylint: disable=unsubscriptable-object
            best = (gap, v)
    return best[1] if best else None


def _xl_below(words: list[Word], anchor: list[Word]) -> bool:
    """True when "XL" sits just under the label: narrow screens wrap "<FAMILY> CANDY XL" onto two lines."""
    x0, _, x1, y1 = _span(anchor)
    h = max(w.h for w in anchor)
    return any(norm(w.text) == "XL" and x0 <= w.cx <= x1 and -0.2 * h <= w.y - y1 <= 1.5 * h for w in words)


# ---------- classification ----------

def classify(words: list[Word]) -> str:
    """Name the screen type from its words: bag, detail, pokedex, storage or unknown."""
    up = [norm(w.text) for w in words]
    joined = " ".join(up)
    if re.search(r"\bRARE CANDY\b", joined) or "ITEMS" in up:
        return "bag"
    if "CANDY" in up and any(k in up for k in ("POWERUP", "POWER", "EVOLVE", "STARDUST", "TRANSFER")):
        return "detail"
    if "POKEDEX" in up:
        return "pokedex"
    cp_tokens = sum(1 for w in words if norm(w.text) == "CP" or _CP_JOINED.match(norm(w.text)))
    if cp_tokens >= 3:
        return "storage"
    return "unknown"


# ---------- parsers ----------

def parse_bag(words: list[Word]) -> ScreenResult:
    """Read the Rare Candy count from an item bag screen, ignoring Rare Candy XL."""
    r = ScreenResult("bag")
    for line in _lines(words):
        ups = [norm(w.text) for w in line]
        for i in range(len(line) - 1):
            if ups[i] == "RARE" and ups[i + 1] == "CANDY":
                if i + 2 < len(ups) and ups[i + 2] == "XL":
                    continue
                anchor = line[i:i + 2]
                # count: right of the label on the same line, else nearest number above/below
                right = [parse_int(w.text) for w in line[i + 2:] if parse_int(w.text) is not None]
                if right:
                    r.rare_candy = right[0]
                else:
                    x0, y0, x1, y1 = _span(anchor)
                    cands = [(min(abs(w.cy - y0), abs(w.cy - y1)) + abs(w.cx - (x0 + x1) / 2), parse_int(w.text))
                             for w in words if parse_int(w.text) is not None]
                    if cands:
                        r.rare_candy = min(cands)[1]
    if r.rare_candy is None:
        r.notes.append("bag screen seen but no Rare Candy count found")
    return r


def evolve_sprite_caught(img: np.ndarray, evolve: Word) -> bool | None:
    """Whether the sprite on an EVOLVE button is in colour (caught) or a silhouette (never caught).

    A silhouette is a darker shade of the button's own green; a caught Pokémon's sprite has its
    own hues and washed-out whites and greys. None when it can't tell: the grey button shown when
    you can't afford the evolution isn't calibrated yet, and tiny or missing sprites are skipped.
    """
    h, x0 = evolve.h, int(evolve.x - 0.8 * evolve.w)
    box = img[max(0, int(evolve.cy - 1.4 * h)):int(evolve.cy + 1.4 * h), max(0, x0):int(evolve.x - 0.05 * evolve.w)]
    if box.size == 0:
        return None
    hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(float)
    lab = cv2.cvtColor(box, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float)
    bright = hsv[:, 2] >= np.median(hsv[:, 2])   # the button fills the brighter half of the box
    bg_hsv, bg_lab = np.median(hsv[bright], axis=0), np.median(lab[bright], axis=0)
    if bg_hsv[1] < 60:
        return None  # grey (unaffordable) button
    sprite = hsv[np.abs(lab - bg_lab).sum(axis=1) > 30]
    if len(sprite) < 200:
        return None
    hue_off = float((np.abs(sprite[:, 0] - bg_hsv[0]) > 12).mean())
    washed_out = float((sprite[:, 1] < 0.5 * bg_hsv[1]).mean())
    # silhouettes are a mid-dark green and never near-black; a dark sprite (Kilowattrel) is in colour
    near_black = float((sprite[:, 2] < 80).mean())
    return (hue_off + washed_out) / 2 >= 0.4 or near_black >= 0.05


def _screen_species(words: list[Word], m: SpeciesMatcher) -> str | None:
    """The Pokémon a detail screen is about: its name is the largest species name on screen."""
    named = [(w.h, sp) for w in words if (sp := m.match(w.text))]
    return max(named)[1] if named else None


def parse_detail(words: list[Word], m: SpeciesMatcher, species: dict,
                 img: np.ndarray | None = None) -> ScreenResult:
    """Read '<FAMILY> CANDY' counts from a Pokémon detail screen, skipping the Candy XL label.

    With the image, also reads whether the evolution on the EVOLVE button has ever been caught.
    Only species with a single evolution are read, since branched ones show several buttons.
    """
    r = ScreenResult("detail")
    evolves = [w for w in words if norm(w.text) == "EVOLVE"]
    sp = _screen_species(words, m) if img is not None and len(evolves) == 1 else None
    evos = species.get(sp, {}).get("evolutions", []) if sp else []
    if len(evos) == 1 and img is not None:
        caught = evolve_sprite_caught(img, evolves[0])
        if caught is not None:
            (r.dex_caught if caught else r.dex_not_caught).add(evos[0]["to"])
    for line in _lines(words):
        ups = [norm(w.text) for w in line]
        for i, u in enumerate(ups):
            if u != "CANDY" or i == 0 or (i + 1 < len(ups) and ups[i + 1] == "XL"):
                continue
            # the family name may be one or two words before CANDY
            for n in (1, 2):
                if i - n < 0:
                    break
                sp = m.match(" ".join(w.text for w in line[i - n:i]))
                if sp:
                    if _xl_below(words, line[i - n:i + 1]):
                        break
                    count = _number_above(words, line[i - n:i + 1])
                    if count is not None:
                        r.candy[species[sp]["family"]] = count
                    else:
                        r.notes.append(f"{sp} candy label found but no count above it")
                    break
    return r


def parse_storage(words: list[Word], m: SpeciesMatcher) -> ScreenResult:
    """Pair each species name in the storage grid with the CP shown above it in the same cell."""
    r = ScreenResult("storage")
    # CP labels as (centre x, centre y, width, value); "CP 1234" may be one word or two
    cps: list[tuple[float, float, float, int]] = []
    for line in _lines(words):
        for i, w in enumerate(line):
            j = _CP_JOINED.match(norm(w.text))
            if j:
                cps.append((w.cx, w.cy, w.w, int(j.group(1))))
            elif norm(w.text) == "CP" and i + 1 < len(line) and (cp := parse_int(line[i + 1].text)) is not None:
                x0, _, x1, _ = _span([w, line[i + 1]])
                cps.append(((x0 + x1) / 2, w.cy, x1 - x0, cp))
    used: set[int] = set()
    for text, run in _phrases(words):
        sp = m.match(text)
        if not sp or any(id(w) in used for w in run):
            continue
        x0, y0, x1, _ = _span(run)
        cx, h = (x0 + x1) / 2, max(w.h for w in run)
        # the CP sits above the name in the same cell: close horizontally, within a cell's height
        above = [(y0 - cy + 3 * abs(ccx - cx), cp) for ccx, cy, cw, cp in cps
                 if cy < y0 and y0 - cy < 12 * h and abs(ccx - cx) < max(x1 - x0, cw) * 0.75]
        # a name whose CP is scrolled off-screen is skipped; another frame will have the whole cell
        if not above and cps:
            continue
        r.specimens.append((sp, min(above)[1] if above else None))
        used.update(id(w) for w in run)
    return r


def parse_pokedex(words: list[Word], img: np.ndarray | None, m: SpeciesMatcher,
                  sat_threshold: float = 55.0) -> ScreenResult:
    """Read dex numbers, marking each entry caught or seen-only by its sprite's colour saturation."""
    r = ScreenResult("pokedex")
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV) if img is not None else None
    for w in words:
        d = _DEX_NUM.match(w.text.strip())
        if not d or not w.text.strip()[0] in "#0123456789N":
            continue
        sp = m.by_dex.get(int(d.group(1)))
        if not sp:
            continue
        if hsv is None:
            r.dex_caught.add(sp)
            continue
        # the sprite sits above the number; caught entries are coloured, seen-only are silhouettes
        side = int(max(w.h * 5, w.w * 2))
        x0, y1 = max(0, int(w.cx - side / 2)), max(0, w.y - 2)
        patch = hsv[max(0, y1 - side):y1, x0:x0 + side]
        if patch.size == 0:
            continue
        coloured = patch[..., 1][patch[..., 2] > 40]   # saturation of non-dark pixels
        sat = float(np.percentile(coloured, 90)) if coloured.size > 20 else 0.0
        (r.dex_caught if sat >= sat_threshold else r.dex_seen_only).add(sp)
    return r


def parse(words: list[Word], img: np.ndarray | None, m: SpeciesMatcher, species: dict,
          kind: str | None = None) -> ScreenResult:
    """Classify the screen (unless given) and run the matching parser."""
    kind = kind or classify(words)
    if kind == "bag":
        return parse_bag(words)
    if kind == "detail":
        return parse_detail(words, m, species, img)
    if kind == "pokedex":
        return parse_pokedex(words, img, m)
    if kind == "storage":
        return parse_storage(words, m)
    return ScreenResult("unknown")
