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
    species: str | None = None                                   # detail: the Pokémon the page shows
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

    The button's own colour varies (green, grey, pink, blue-grey) and says nothing about this, so the
    sprite is judged against the button around it: a silhouette is one flat tone darker than the
    button, while a caught Pokémon has shading, highlights and several colours. None when there is
    no sprite to judge, e.g. a frame where it hasn't loaded yet.
    """
    h = evolve.h
    box = img[max(0, int(evolve.cy - 1.4 * h)):int(evolve.cy + 1.4 * h),
              max(0, int(evolve.x - 0.8 * evolve.w)):int(evolve.x - 0.05 * evolve.w)]
    if box.size == 0:
        return None
    lab = cv2.cvtColor(box, cv2.COLOR_BGR2LAB).astype(float)
    flat = lab.reshape(-1, 3)
    # the box can reach past the button's rounded end onto the white page; leave that out of the
    # button colour, which is then the brighter half of what's left (sprites are mostly darker)
    page = (flat[:, 0] > 232) & (np.abs(flat[:, 1:] - 128).max(axis=1) < 8)
    rest = flat[~page] if (~page).sum() > 50 else flat
    button = np.median(rest[rest[:, 0] >= np.median(rest[:, 0])], axis=0)
    differs = (np.abs(lab - button).sum(axis=2) > 30).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(differs, connectivity=8)
    height, width = differs.shape
    sprite = np.zeros(differs.shape, bool)
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        part = lab[labels == i]
        on_edge = x == 0 or y == 0 or x + w == width or y + hh == height
        page_like = np.median(part[:, 0]) > button[0] + 8 and np.median(np.abs(part[:, 1:] - 128)) < 10
        if area >= 15 and not (on_edge and page_like):
            sprite |= labels == i
    if sprite.sum() < 100:
        return None  # no sprite yet (still loading, under 20 px on real frames), or too small to judge
    core = cv2.erode(sprite.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    px = lab[core] if core.sum() > 50 else lab[sprite]   # without the anti-aliased rim
    # real buttons: silhouettes vary by at most ~7 in lightness, Pokémon in colour by 15 or more
    if px[:, 0].std() >= 11:
        return True
    return False if np.median(px[:, 0]) < button[0] else None


def evolve_buttons(words: list[Word]) -> list[Word]:
    """The EVOLVE button labels on a detail screen.

    Leaves out "evolve" in hint text ("Adventure together to evolve"), which isn't in capitals, and
    MEGA EVOLVE buttons, whose sprite is a Mega Evolution rather than the next species.
    """
    def mega(e: Word) -> bool:
        """True when MEGA sits just left of the label or stacked just above it."""
        return any(norm(w.text) == "MEGA" and (_same_line(w, e) and w.x < e.x
                                               or 0 <= e.y - (w.y + w.h) < e.h and abs(w.cx - e.cx) < e.w)
                   for w in words)
    return [w for w in words if norm(w.text) == "EVOLVE" and w.text.strip().isupper() and not mega(w)]


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
    r = ScreenResult("detail", species=_screen_species(words, m))
    cp = _detail_cp(words)
    if r.species and cp is not None:
        r.specimens.append((r.species, cp))   # each Pokémon swiped past is its own copy; frames share its CP
    evolves = evolve_buttons(words)
    evos = species.get(r.species, {}).get("evolutions", []) if r.species else []
    if len(evos) == 1 and len(evolves) == 1 and img is not None:
        caught = evolve_sprite_caught(img, evolves[0])
        if caught is not None:
            (r.dex_caught if caught else r.dex_not_caught).add(evos[0]["to"])
    for line in _lines(words):
        ups = [norm(w.text) for w in line]
        for i, u in enumerate(ups):
            if u != "CANDY" or (i + 1 < len(ups) and ups[i + 1] == "XL"):
                continue
            label = _candy_label(words, line, i, m)
            if label is None:
                continue
            sp, anchor = label
            if _xl_below(words, anchor):
                continue
            count = _number_above(words, anchor)
            if count is not None:
                r.candy[species[sp]["family"]] = count
            else:
                r.notes.append(f"{sp} candy label found but no count above it")
    return r


def _detail_cp(words: list[Word]) -> int | None:
    """The CP printed in large type at the top of a detail page ("CP291", or "CP" and "291"); None when
    it isn't on screen (the page is scrolled down)."""
    found = []
    for line in _lines(words):
        for i, w in enumerate(line):
            j = _CP_JOINED.match(norm(w.text))
            if j:
                found.append((w.h, int(j.group(1))))
            elif norm(w.text) == "CP" and i + 1 < len(line) and (n := parse_int(line[i + 1].text)) is not None:
                found.append((w.h, n))
    return max(found)[1] if found else None   # the largest: move names and costs are smaller


def _candy_label(words: list[Word], line: list[Word], i: int, m: SpeciesMatcher) -> tuple[str, list[Word]] | None:
    """The species named in "<FAMILY> CANDY" for the CANDY at line[i], and the label's words.

    The name is one or two words before CANDY, or, when a long name wraps ("GREAVARD" above "CANDY"),
    on the line just above it.
    """
    for n in (1, 2):
        if i - n < 0:
            break
        sp = m.match(" ".join(w.text for w in line[i - n:i]))
        if sp:
            return sp, line[i - n:i + 1]
    candy = line[i]
    above = sorted((w for w in words if 0 <= candy.y - (w.y + w.h) < candy.h
                    and w.x < candy.x + candy.w and w.x + w.w > candy.x), key=lambda w: w.x)
    sp = m.match(" ".join(w.text for w in above)) if above else None
    return (sp, above + [candy]) if sp else None


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
    """Read dex numbers, marking each entry caught or seen-only by its sprite's colour saturation.

    With the Pokédex filtered to "Seen", every entry shown has been seen but never caught.
    """
    r = ScreenResult("pokedex")
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV) if img is not None else None
    # entries sit below the POKEDEX title; above it is the phone's status bar (time, battery), and on
    # the title line the "Caught: 893" count, neither of which are dex numbers
    titles = [w for w in words if norm(w.text) == "POKEDEX"]
    top = max(w.y + w.h for w in titles) if titles else 0
    seen_filter = any(norm(w.text) == "SEEN" for w in words)
    for w in words:
        text = w.text.strip()
        d = _DEX_NUM.match(text)
        if not d or w.y < top or not text[0] in "#0123456789N":
            continue
        if text.isdigit() and len(text) != 4:
            continue  # bare entry numbers are zero-padded to four digits (0841, 1000)
        sp = m.by_dex.get(int(d.group(1)))
        if not sp:
            continue
        if seen_filter:
            r.dex_not_caught.add(sp)
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
