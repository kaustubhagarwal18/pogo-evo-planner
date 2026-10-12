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
_LISTED = re.compile(r"\((\d{1,4})\)$")  # "(210)" under the POKÉMON tab; OCR reads the search icon as "Q"


def norm(text: str) -> str:
    """Uppercase and strip everything but letters and digits, for comparing OCR text."""
    return re.sub(r"[^A-Z0-9]", "", text.upper().replace("É", "E"))


def parse_int(text: str) -> int | None:
    """Parse a count like 1,204, 60 or x12; None when the text isn't a number."""
    match = _NUM.match(text.strip())
    return int(re.sub(r"[,.]", "", match.group(1))) if match else None


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
        self.by_dex = {
            entry["dex"]: species_id
            for species_id, entry in species.items()
            if "dex" in entry and "_" not in species_id
        }
        self.names = list(self.by_name)

    def match(self, text: str) -> str | None:
        """Return the species id for OCR text, exactly or by fuzzy match; None when unsure."""
        normalized = norm(text)
        if len(normalized) < 3:
            return None
        if normalized in self.by_name:
            return self.by_name[normalized]
        if len(normalized) < 5:
            return None  # too short for fuzzy matching to be trustworthy
        if _rf_process is not None:
            hit = _rf_process.extractOne(normalized, self.names, scorer=_rf_fuzz.ratio, score_cutoff=self.cutoff * 100)
            return self.by_name[hit[0]] if hit else None
        hit = difflib.get_close_matches(normalized, self.names, n=1, cutoff=self.cutoff)
        return self.by_name[hit[0]] if hit else None


@dataclass
class ScreenResult:
    kind: str
    species: str | None = None  # detail: the Pokémon the page shows
    rare_candy: int | None = None
    candy: dict[str, int] = field(default_factory=dict)  # family -> count
    specimens: list[tuple[str, int | None]] = field(default_factory=list)  # (species, cp)
    dex_caught: set[str] = field(default_factory=set)
    dex_seen_only: set[str] = field(default_factory=set)
    dex_not_caught: set[str] = field(default_factory=set)  # evolve button shows a silhouette
    listed: int | None = None  # storage: how many Pokémon a search found
    notes: list[str] = field(default_factory=list)


# ---------- geometry helpers ----------


def _same_line(first: Word, second: Word) -> bool:
    """True when two words' vertical centres are close enough to be on one text line."""
    return abs(first.cy - second.cy) < 0.6 * max(first.h, second.h)


def _lines(words: list[Word]) -> list[list[Word]]:
    """Group words into text lines, top to bottom, each sorted left to right."""
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda word: (word.cy, word.x)):
        for row in rows:
            if _same_line(row[0], word):
                row.append(word)
                break
        else:
            rows.append([word])
    return [sorted(row, key=lambda word: word.x) for row in rows]


def _phrases(words: list[Word], max_len: int = 2):
    """Yield (text, words) for single words and runs of adjacent words on a line."""
    for line in _lines(words):
        for start in range(len(line)):
            for length in range(1, max_len + 1):
                run = line[start : start + length]
                if len(run) < length:
                    break
                if length > 1 and run[-1].x - (run[-2].x + run[-2].w) > 2.5 * run[-1].h:
                    break
                yield " ".join(word.text for word in run), run


def _span(words: list[Word]) -> tuple[float, float, float, float]:
    """Bounding box (x0, y0, x1, y1) around a run of words."""
    left, right = min(word.x for word in words), max(word.x + word.w for word in words)
    top, bottom = min(word.y for word in words), max(word.y + word.h for word in words)
    return left, top, right, bottom


def _number_above(words: list[Word], anchor: list[Word], max_gap: float = 6.0) -> int | None:
    """Nearest number directly above a label, within max_gap label-heights; None if there isn't one."""
    left, top, right, _ = _span(anchor)
    center_x, label_height = (left + right) / 2, max(word.h for word in anchor)
    best = None
    for word in words:
        number = parse_int(word.text)
        if number is None or word.cy >= top:
            continue
        if abs(word.cx - center_x) > max(right - left, word.w) * 0.75:
            continue
        gap = top - (word.y + word.h)
        if gap <= max_gap * label_height and (best is None or gap < best[0]):  # pylint: disable=unsubscriptable-object
            best = (gap, number)
    return best[1] if best else None


def _xl_below(words: list[Word], anchor: list[Word]) -> bool:
    """True when "XL" sits just under the label: narrow screens wrap "<FAMILY> CANDY XL" onto two lines."""
    left, _, right, bottom = _span(anchor)
    label_height = max(word.h for word in anchor)
    return any(
        norm(word.text) == "XL"
        and left <= word.cx <= right
        and -0.2 * label_height <= word.y - bottom <= 1.5 * label_height
        for word in words
    )


# ---------- classification ----------


def classify(words: list[Word]) -> str:
    """Name the screen type from its words: bag, detail, pokedex, storage or unknown."""
    normalized = [norm(word.text) for word in words]
    joined = " ".join(normalized)
    if re.search(r"\bRARE CANDY\b", joined) or "ITEMS" in normalized:
        return "bag"
    if "CANDY" in normalized and any(
        keyword in normalized for keyword in ("POWERUP", "POWER", "EVOLVE", "STARDUST", "TRANSFER")
    ):
        return "detail"
    if "POKEDEX" in normalized:
        return "pokedex"
    cp_tokens = sum(1 for word in words if norm(word.text) == "CP" or _CP_JOINED.match(norm(word.text)))
    if cp_tokens >= 3:
        return "storage"
    return "unknown"


# ---------- parsers ----------


def parse_bag(words: list[Word]) -> ScreenResult:
    """Read the Rare Candy count from an item bag screen, ignoring Rare Candy XL."""
    result = ScreenResult("bag")
    for line in _lines(words):
        normalized = [norm(word.text) for word in line]
        for index in range(len(line) - 1):
            if normalized[index] == "RARE" and normalized[index + 1] == "CANDY":
                if index + 2 < len(normalized) and normalized[index + 2] == "XL":
                    continue
                anchor = line[index : index + 2]
                # count: right of the label on the same line, else nearest number above/below
                counts_right = [parse_int(word.text) for word in line[index + 2 :] if parse_int(word.text) is not None]
                if counts_right:
                    result.rare_candy = counts_right[0]
                else:
                    left, top, right, bottom = _span(anchor)
                    candidates = [
                        (
                            min(abs(word.cy - top), abs(word.cy - bottom)) + abs(word.cx - (left + right) / 2),
                            parse_int(word.text),
                        )
                        for word in words
                        if parse_int(word.text) is not None
                    ]
                    if candidates:
                        result.rare_candy = min(candidates)[1]
    if result.rare_candy is None:
        result.notes.append("bag screen seen but no Rare Candy count found")
    return result


def evolve_sprite_caught(image: np.ndarray, evolve: Word) -> bool | None:
    """Whether the sprite on an EVOLVE button is in colour (caught) or a silhouette (never caught).

    The button's own colour varies (green, grey, pink, blue-grey) and says nothing about this, so the
    sprite is judged against the button around it: a silhouette is one flat tone darker than the
    button, while a caught Pokémon has shading, highlights and several colours. None when there is
    no sprite to judge, e.g. a frame where it hasn't loaded yet.
    """
    label_height = evolve.h
    region = image[
        max(0, int(evolve.cy - 1.4 * label_height)) : int(evolve.cy + 1.4 * label_height),
        max(0, int(evolve.x - 0.8 * evolve.w)) : int(evolve.x - 0.05 * evolve.w),
    ]
    if region.size == 0:
        return None
    lab_colors = cv2.cvtColor(region, cv2.COLOR_BGR2LAB).astype(float)
    flat = lab_colors.reshape(-1, 3)
    # the box can reach past the button's rounded end onto the white page; leave that out of the
    # button colour, which is then the brighter half of what's left (sprites are mostly darker)
    page = (flat[:, 0] > 232) & (np.abs(flat[:, 1:] - 128).max(axis=1) < 8)
    rest = flat[~page] if (~page).sum() > 50 else flat
    button = np.median(rest[rest[:, 0] >= np.median(rest[:, 0])], axis=0)
    differs = (np.abs(lab_colors - button).sum(axis=2) > 30).astype(np.uint8)
    components, labels, stats, _ = cv2.connectedComponentsWithStats(differs, connectivity=8)
    height, width = differs.shape
    sprite = np.zeros(differs.shape, bool)
    for component in range(1, components):
        part_left, part_top, part_width, part_height, area = stats[component]
        part = lab_colors[labels == component]
        on_edge = part_left == 0 or part_top == 0 or part_left + part_width == width or part_top + part_height == height
        page_like = np.median(part[:, 0]) > button[0] + 8 and np.median(np.abs(part[:, 1:] - 128)) < 10
        if area >= 15 and not (on_edge and page_like):
            sprite |= labels == component
    if sprite.sum() < 100:
        return None  # no sprite yet (still loading, under 20 px on real frames), or too small to judge
    core = cv2.erode(sprite.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    core_colors = lab_colors[core] if core.sum() > 50 else lab_colors[sprite]  # without the anti-aliased rim
    # real buttons: silhouettes vary by at most ~7 in lightness, Pokémon in colour by 15 or more
    if core_colors[:, 0].std() >= 11:
        return True
    return False if np.median(core_colors[:, 0]) < button[0] else None


def evolve_buttons(words: list[Word]) -> list[Word]:
    """The EVOLVE button labels on a detail screen.

    Leaves out "evolve" in hint text ("Adventure together to evolve"), which isn't in capitals, and
    MEGA EVOLVE buttons, whose sprite is a Mega Evolution rather than the next species.
    """

    def mega(label: Word) -> bool:
        """True when MEGA sits just left of the label or stacked just above it."""
        return any(
            norm(word.text) == "MEGA"
            and (
                _same_line(word, label)
                and word.x < label.x
                or 0 <= label.y - (word.y + word.h) < label.h
                and abs(word.cx - label.cx) < label.w
            )
            for word in words
        )

    return [word for word in words if norm(word.text) == "EVOLVE" and word.text.strip().isupper() and not mega(word)]


def _screen_species(words: list[Word], matcher: SpeciesMatcher) -> str | None:
    """The Pokémon a detail screen is about: its name is the largest species name on screen."""
    named = [(word.h, species_id) for word in words if (species_id := matcher.match(word.text))]
    return max(named)[1] if named else None


def parse_detail(
    words: list[Word], matcher: SpeciesMatcher, species: dict, image: np.ndarray | None = None
) -> ScreenResult:
    """Read '<FAMILY> CANDY' counts from a Pokémon detail screen, skipping the Candy XL label.

    With the image, also reads whether the evolution on the EVOLVE button has ever been caught.
    Only species with a single evolution are read, since branched ones show several buttons.
    """
    result = ScreenResult("detail", species=_screen_species(words, matcher))
    combat_power = _detail_cp(words)
    if result.species and combat_power is not None:
        result.specimens.append(
            (result.species, combat_power)
        )  # each Pokémon swiped past is its own copy; frames share its CP
    evolves = evolve_buttons(words)
    evolutions = species.get(result.species, {}).get("evolutions", []) if result.species else []
    if len(evolutions) == 1 and len(evolves) == 1 and image is not None:
        caught = evolve_sprite_caught(image, evolves[0])
        if caught is not None:
            (result.dex_caught if caught else result.dex_not_caught).add(evolutions[0]["to"])
    for line in _lines(words):
        normalized = [norm(word.text) for word in line]
        for index, text in enumerate(normalized):
            if text != "CANDY" or (index + 1 < len(normalized) and normalized[index + 1] == "XL"):
                continue
            label = _candy_label(words, line, index, matcher)
            if label is None:
                continue
            species_id, anchor = label
            if _xl_below(words, anchor):
                continue
            count = _number_above(words, anchor)
            if count is not None:
                result.candy[species[species_id]["family"]] = count
            else:
                result.notes.append(f"{species_id} candy label found but no count above it")
    return result


def _detail_cp(words: list[Word]) -> int | None:
    """The CP printed in large type at the top of a detail page ("CP291", or "CP" and "291"); None when
    it isn't on screen (the page is scrolled down)."""
    found = []
    for line in _lines(words):
        for index, word in enumerate(line):
            joined = _CP_JOINED.match(norm(word.text))
            if joined:
                found.append((word.h, int(joined.group(1))))
            elif (
                norm(word.text) == "CP"
                and index + 1 < len(line)
                and (number := parse_int(line[index + 1].text)) is not None
            ):
                found.append((word.h, number))
    return max(found)[1] if found else None  # the largest: move names and costs are smaller


def _candy_label(
    words: list[Word], line: list[Word], candy_index: int, matcher: SpeciesMatcher
) -> tuple[str, list[Word]] | None:
    """The species named in "<FAMILY> CANDY" for the CANDY at line[candy_index], and the label's words.

    The name is one or two words before CANDY, or, when a long name wraps ("GREAVARD" above "CANDY"),
    on the line just above it.
    """
    for length in (1, 2):
        if candy_index - length < 0:
            break
        species_id = matcher.match(" ".join(word.text for word in line[candy_index - length : candy_index]))
        if species_id:
            return species_id, line[candy_index - length : candy_index + 1]
    candy = line[candy_index]
    above = sorted(
        (
            word
            for word in words
            if 0 <= candy.y - (word.y + word.h) < candy.h and word.x < candy.x + candy.w and word.x + word.w > candy.x
        ),
        key=lambda word: word.x,
    )
    species_id = matcher.match(" ".join(word.text for word in above)) if above else None
    return (species_id, above + [candy]) if species_id else None


def _listed(words: list[Word]) -> int | None:
    """How many Pokémon a storage search found: the "(210)" below the POKÉMON tab. None without a search."""
    for tab in (candidate for candidate in words if norm(candidate.text) == "POKEMON"):
        for word in words:
            found = _LISTED.search(word.text.replace(" ", ""))
            if found and abs(word.cx - tab.cx) < tab.w and 0 < word.cy - tab.cy < 3 * tab.h:
                return int(found.group(1))
    return None


def parse_storage(words: list[Word], matcher: SpeciesMatcher) -> ScreenResult:
    """Pair each species name in the storage grid with the CP shown above it in the same cell."""
    result = ScreenResult("storage", listed=_listed(words))
    # CP labels as (centre x, centre y, width, value); "CP 1234" may be one word or two
    cp_labels: list[tuple[float, float, float, int]] = []
    for line in _lines(words):
        for index, word in enumerate(line):
            joined = _CP_JOINED.match(norm(word.text))
            if joined:
                cp_labels.append((word.cx, word.cy, word.w, int(joined.group(1))))
            elif (
                norm(word.text) == "CP"
                and index + 1 < len(line)
                and (combat_power := parse_int(line[index + 1].text)) is not None
            ):
                left, _, right, _ = _span([word, line[index + 1]])
                cp_labels.append(((left + right) / 2, word.cy, right - left, combat_power))
    used: set[int] = set()
    for text, run in _phrases(words):
        species_id = matcher.match(text)
        if not species_id or any(id(word) in used for word in run):
            continue
        left, top, right, _ = _span(run)
        center_x, name_height = (left + right) / 2, max(word.h for word in run)
        # the CP sits above the name in the same cell: close horizontally, within a cell's height
        above = [
            (top - label_y + 3 * abs(label_x - center_x), combat_power)
            for label_x, label_y, label_width, combat_power in cp_labels
            if label_y < top
            and top - label_y < 12 * name_height
            and abs(label_x - center_x) < max(right - left, label_width) * 0.75
        ]
        # a name whose CP is scrolled off-screen is skipped; another frame will have the whole cell
        if not above and cp_labels:
            continue
        result.specimens.append((species_id, min(above)[1] if above else None))
        used.update(id(word) for word in run)
    return result


def parse_pokedex(
    words: list[Word], image: np.ndarray | None, matcher: SpeciesMatcher, sat_threshold: float = 55.0
) -> ScreenResult:
    """Read dex numbers, marking each entry caught or seen-only by its sprite's colour saturation.

    With the Pokédex filtered to "Seen", every entry shown has been seen but never caught.
    """
    result = ScreenResult("pokedex")
    hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if image is not None else None
    # entries sit below the POKEDEX title; above it is the phone's status bar (time, battery), and on
    # the title line the "Caught: 893" count, neither of which are dex numbers
    titles = [word for word in words if norm(word.text) == "POKEDEX"]
    top = max(word.y + word.h for word in titles) if titles else 0
    seen_filter = any(norm(word.text) == "SEEN" for word in words)
    for word in words:
        text = word.text.strip()
        dex_match = _DEX_NUM.match(text)
        if not dex_match or word.y < top or not text[0] in "#0123456789N":
            continue
        if text.isdigit() and len(text) != 4:
            continue  # bare entry numbers are zero-padded to four digits (0841, 1000)
        species_id = matcher.by_dex.get(int(dex_match.group(1)))
        if not species_id:
            continue
        if seen_filter:
            result.dex_not_caught.add(species_id)
            continue
        if hsv_image is None:
            result.dex_caught.add(species_id)
            continue
        # the sprite sits above the number; caught entries are coloured, seen-only are silhouettes
        side = int(max(word.h * 5, word.w * 2))
        left, bottom = max(0, int(word.cx - side / 2)), max(0, word.y - 2)
        patch = hsv_image[max(0, bottom - side) : bottom, left : left + side]
        if patch.size == 0:
            continue
        coloured = patch[..., 1][patch[..., 2] > 40]  # saturation of non-dark pixels
        saturation = float(np.percentile(coloured, 90)) if coloured.size > 20 else 0.0
        (result.dex_caught if saturation >= sat_threshold else result.dex_seen_only).add(species_id)
    return result


def parse(
    words: list[Word], image: np.ndarray | None, matcher: SpeciesMatcher, species: dict, kind: str | None = None
) -> ScreenResult:
    """Classify the screen (unless given) and run the matching parser."""
    kind = kind or classify(words)
    if kind == "bag":
        return parse_bag(words)
    if kind == "detail":
        return parse_detail(words, matcher, species, image)
    if kind == "pokedex":
        return parse_pokedex(words, image, matcher)
    if kind == "storage":
        return parse_storage(words, matcher)
    return ScreenResult("unknown")
