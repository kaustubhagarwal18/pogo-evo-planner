import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.scan.frames import extract_frames
from pogo_evo_planner.scan.ocr import Word, _split_line, available_backend
from pogo_evo_planner.scan.pipeline import ScanReport, merge, scan, taken_at
from pogo_evo_planner.scan.screens import (
    ScreenResult,
    SpeciesMatcher,
    classify,
    evolve_buttons,
    evolve_sprite_caught,
    parse_bag,
    parse_detail,
    parse_pokedex,
    parse_storage,
)

from . import mockscreens

SPECIES = load_species(Path(__file__).resolve().parent.parent / "pogo_evo_planner/data/species_sample.json")
M = SpeciesMatcher(SPECIES)


def evolve_button(sprite: str) -> tuple[np.ndarray, Word]:
    """A green EVOLVE button like a 576 px wide phone shows, with a colour or silhouette sprite."""
    img = np.full((1296, 576, 3), 245, np.uint8)
    green = cv2.cvtColor(np.uint8([[[57, 88, 228]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    cv2.rectangle(img, (40, 975), (400, 1060), green, -1)
    if sprite == "silhouette":   # a darker shade of the button green
        dark = cv2.cvtColor(np.uint8([[[57, 110, 140]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        cv2.circle(img, (100, 1016), 26, dark, -1)
    elif sprite == "dark":       # a dark-bodied Pokémon in colour, like Kilowattrel: near-black, thin yellow trim
        dark = cv2.cvtColor(np.uint8([[[57, 110, 140]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        cv2.circle(img, (100, 1016), 26, dark, -1)
        cv2.circle(img, (100, 1016), 9, (30, 30, 35), -1)
        cv2.line(img, (76, 1004), (124, 1004), (40, 200, 230), 2)
    else:                        # white body with a purple patch
        cv2.circle(img, (100, 1016), 26, (250, 250, 250), -1)
        cv2.circle(img, (92, 1008), 10, (160, 90, 140), -1)
    return img, Word("EVOLVE", 140, 1003, 103, 27, 0.9)


def w(text, x, y, width=None, h=40):
    """A word box sized from the text, as OCR returns it."""
    return Word(text, x, y, width or 22 * len(text), h, 0.9)


class ParserTests(unittest.TestCase):
    def test_matcher_exact_fuzzy_and_rejects(self):
        """Names match exactly or fuzzily (OCR slips); too-short or unknown text doesn't match."""
        self.assertEqual(M.match("Dratini"), "DRATINI")
        self.assertEqual(M.match("Dratlni"), "DRATINI")      # OCR l/i confusion
        self.assertEqual(M.match("MAGIKARP"), "MAGIKARP")
        self.assertIsNone(M.match("Candy"))
        self.assertIsNone(M.match("CP"))
        self.assertEqual(M.by_dex[147], "DRATINI")

    def test_classify(self):
        """Screens are classified as bag, detail, Pokédex or storage from their words."""
        self.assertEqual(classify([w("Rare", 300, 900), w("Candy", 420, 900)]), "bag")
        self.assertEqual(classify([w("DRATINI", 600, 1300), w("CANDY", 780, 1300), w("EVOLVE", 400, 1700)]), "detail")
        self.assertEqual(classify([w("POKEDEX", 400, 100)]), "pokedex")
        self.assertEqual(classify([w("CP", x, 200) for x in (100, 400, 700)]), "storage")
        self.assertEqual(classify([w("hello", 10, 10)]), "unknown")

    def test_bag_count_right_of_label_ignores_xl(self):
        """The bag's Rare Candy count is read right of its label, ignoring Rare Candy XL."""
        words = [w("Rare", 300, 1160), w("Candy", 420, 1160), w("XL", 560, 1160), w("x3", 860, 1160),
                 w("Rare", 300, 900), w("Candy", 420, 900), w("x150", 860, 900)]
        self.assertEqual(parse_bag(words).rare_candy, 150)

    def test_bag_count_below_label(self):
        """The bag's Rare Candy count is read below the label when it isn't beside it."""
        words = [w("Rare", 300, 900), w("Candy", 420, 900), w("1,204", 330, 960)]
        self.assertEqual(parse_bag(words).rare_candy, 1204)

    def test_detail_reads_family_candy_above_label(self):
        """A detail page's family candy is the number above '<FAMILY> CANDY'."""
        words = [w("Dragonair", 400, 900, h=80), w("48,210", 200, 1240), w("STARDUST", 200, 1320),
                 w("60", 760, 1240), w("DRATINI", 640, 1320), w("CANDY", 820, 1320), w("EVOLVE", 450, 1760)]
        self.assertEqual(parse_detail(words, M, SPECIES).candy, {"DRATINI": 60})

    def test_evolve_button_sprite_colour_or_silhouette(self):
        """An Evolve-button sprite in colour reads as caught, a silhouette as never caught."""
        self.assertTrue(evolve_sprite_caught(*evolve_button("colour")))
        self.assertFalse(evolve_sprite_caught(*evolve_button("silhouette")))
        self.assertTrue(evolve_sprite_caught(*evolve_button("dark")))  # real Kilowattrel misread before

    def test_evolve_button_colour_does_not_matter(self):
        """On a grey button the sprite decides the same way as on a green one."""
        # buttons come grey, pink or blue-grey as well as green; the sprite alone decides
        for sprite, expected in (("silhouette", False), ("colour", True)):
            img, ev = evolve_button(sprite)
            button = np.all(img == img[980, 300], axis=2)
            img[button] = (215, 218, 220)
            if sprite == "silhouette":   # a darker shade of the grey button
                img[np.all(img == img[1016, 100], axis=2)] = (120, 122, 124)
            self.assertIs(evolve_sprite_caught(img, ev), expected)

    def test_evolve_button_on_real_screens(self):
        """All 39 real Evolve-button crops read as labelled by eye: caught, never caught or unknown."""
        # 39 buttons from a real recording and real screenshots, each checked by eye: 22 in colour,
        # 11 silhouettes (on green, pink and blue-grey buttons), and 6 frames where the sprite hadn't
        # loaded yet, which used to read as caught (how Dragapult was wrongly marked caught)
        folder = Path(__file__).resolve().parent / "real" / "evolve_buttons"
        cases = json.loads((folder / "cases.json").read_text())
        answer = {True: "caught", False: "never caught", None: "unknown"}
        wrong = {}
        for name, case in cases.items():
            got = answer[evolve_sprite_caught(cv2.imread(str(folder / f"{name}.png")),
                                              Word("EVOLVE", *case["label_box"], 0.9))]
            if got != case["expected"]:
                wrong[name] = f"{got}, expected {case['expected']}"
        self.assertEqual(wrong, {})

    def test_evolve_buttons_skip_hint_text_and_mega(self):
        """Only the capitalised EVOLVE label counts: not hint text, not MEGA EVOLVE."""
        button = w("EVOLVE", 270, 1890)
        hint = [w("Adventure", 180, 2040), w("together", 420, 2040), w("to", 600, 2040), w("evolve", 650, 2040)]
        mega = [w("MEGA", 270, 1680), w("EVOLVE", 270, 1730)]   # stacked on two lines
        self.assertEqual(evolve_buttons([button] + hint), [button])
        self.assertEqual(evolve_buttons(mega), [])
        self.assertEqual(evolve_buttons([w("MEGA", 150, 1700), w("EVOLVE", 270, 1700)]), [])

    def test_detail_reads_evolution_never_caught_from_silhouette(self):
        """A detail page's silhouette marks the evolution never caught; colour marks it caught."""
        img, ev = evolve_button("silhouette")
        words = [w("Dratini", 200, 400, h=80), w("60", 300, 600), w("DRATINI", 250, 680), w("CANDY", 420, 680), ev]
        r = parse_detail(words, M, SPECIES, img)
        self.assertEqual((r.candy, r.dex_not_caught, r.dex_caught), ({"DRATINI": 60}, {"DRAGONAIR"}, set()))
        img, ev = evolve_button("colour")
        self.assertEqual(parse_detail(words[:-1] + [ev], M, SPECIES, img).dex_caught, {"DRAGONAIR"})

    def test_detail_ignores_candy_xl_label_wrapped_onto_next_line(self):
        """The Candy XL count is ignored even when 'XL' wraps onto the next line."""
        # narrow phones: "DRATINI CANDY" on one line, "XL" centred under it, XL count above
        words = [w("48,210", 100, 1240), w("STARDUST", 100, 1320),
                 w("60", 460, 1240), w("DRATINI", 380, 1320), w("CANDY", 550, 1320),
                 w("7", 820, 1240), w("DRATINI", 740, 1320), w("CANDY", 910, 1320), w("XL", 830, 1370),
                 w("EVOLVE", 450, 1760)]
        self.assertEqual(parse_detail(words, M, SPECIES).candy, {"DRATINI": 60})

    def test_detail_reads_candy_label_wrapped_onto_two_lines(self):
        """A candy label whose name wraps above 'CANDY' is still read (a real Greavard page)."""
        # real Greavard page: "GREAVARD" above "CANDY"; the Candy XL label wraps the same way
        words = [Word("9,176,980", 116, 1480, 260, 57, 0.9), Word("77", 533, 1480, 50, 53, 0.9),
                 Word("19", 865, 1480, 50, 54, 0.9), Word("STARDUST", 151, 1548, 190, 32, 0.9),
                 Word("GREAVARD", 480, 1548, 190, 32, 0.9), Word("GREAVARD", 811, 1550, 190, 32, 0.9),
                 Word("CANDY", 511, 1584, 120, 34, 0.9), Word("CANDY", 819, 1585, 110, 32, 0.9),
                 Word("XL", 941, 1585, 40, 32, 0.9)]
        species = {**SPECIES, "GREAVARD": {"dex": 971, "family": "GREAVARD", "buddy_km": 3}}
        self.assertEqual(parse_detail(words, SpeciesMatcher(species), species).candy, {"GREAVARD": 77})

    def test_storage_pairs_cp_in_same_cell(self):
        """Each storage name gets the CP above it in the same cell."""
        words = [w("CP", 92, 146), w("412", 176, 146), w("CP", 436, 145), w("1543", 523, 145),
                 w("Dratini", 95, 436, 160), w("Dragonair", 415, 436, 220)]
        self.assertEqual(sorted(parse_storage(words, M).specimens), [("DRAGONAIR", 1543), ("DRATINI", 412)])

    def test_storage_skips_cell_whose_cp_is_off_screen(self):
        """A storage name whose CP is scrolled off-screen is skipped."""
        words = [w("CP", 92, 600), w("389", 176, 600), w("Dragonair", 415, 100, 220), w("Chansey", 73, 890, 170)]
        self.assertEqual(parse_storage(words, M).specimens, [("CHANSEY", 389)])

    def test_storage_reads_how_many_a_search_found(self):
        """The "(210)" below the POKÉMON tab is how many Pokémon a search found; a count elsewhere isn't."""
        tabs = [w("POKÉMON", 431, 106, 218, 49), w("EGGS", 811, 110), w("11/12", 811, 171), w("(5)", 456, 900)]
        cells = [w("CP", x, 600) for x in (100, 400, 700)]
        self.assertEqual(parse_storage(tabs + [w("Q(210)", 456, 170, 130, 47)] + cells, M).listed, 210)
        self.assertIsNone(parse_storage(tabs + cells, M).listed)   # no search: nothing to check against

    def test_pokedex_colour_decides_caught(self):
        """On an unfiltered Pokédex a coloured sprite reads as caught, a grey one as seen only."""
        img = np.full((800, 600, 3), 235, np.uint8)
        img[100:300, 50:250] = (40, 120, 230)    # coloured sprite (BGR)
        img[100:300, 350:550] = (75, 70, 70)     # grey silhouette
        words = [w("#0147", 90, 320, 120), w("#0149", 390, 320, 120)]
        r = parse_pokedex(words, img, M)
        self.assertEqual((r.dex_caught, r.dex_seen_only), ({"DRATINI"}, {"DRAGONITE"}))

    def test_pokedex_seen_filter_lists_never_caught(self):
        """With the Seen filter every entry is never caught; status-bar and header numbers are ignored."""
        # real "Seen" screen: the status bar's battery (60) and "Caught: 893" are not dex numbers
        words = [Word("11:48", 80, 41, 90, 38, 0.9), Word("60", 928, 43, 40, 37, 0.9),
                 Word("POKÉDEX", 45, 127, 190, 53, 0.9), Word("Caught:", 750, 133, 150, 52, 0.9),
                 Word("893", 929, 133, 70, 52, 0.9), Word("Seen", 248, 263, 90, 51, 0.9),
                 Word("0147", 100, 865, 110, 51, 0.9), Word("0148", 351, 864, 110, 52, 0.9)]
        r = parse_pokedex(words, None, M)
        self.assertEqual((r.dex_not_caught, r.dex_caught, r.dex_seen_only), ({"DRATINI", "DRAGONAIR"}, set(), set()))
        # unfiltered, the same header numbers are still ignored
        r = parse_pokedex([x for x in words if x.text != "Seen"], None, M)
        self.assertEqual(r.dex_caught, {"DRATINI", "DRAGONAIR"})

    def test_merge_prefers_the_newest_capture(self):
        """When captures disagree on a count the newest wins; within one, the most common reading."""
        # Snom: an older recording read 80 three times, today's screenshot reads 102
        frames = [ScreenResult("detail", candy={"SNOM": 80})] * 3 + [ScreenResult("detail", candy={"SNOM": 102}),
                                                                     ScreenResult("bag", rare_candy=300),
                                                                     ScreenResult("bag", rare_candy=305)]
        rep = ScanReport()
        inv = merge(frames, rep, times=[1.0, 1.0, 1.0, 2.0, 1.0, 2.0])
        self.assertEqual((inv["candy"], inv["rare_candy"]), ({"SNOM": 102}, 305))
        self.assertTrue(any("using 102, from the newest capture" in x for x in rep.warnings))
        # within one capture the most common reading still outvotes a misread frame
        newest = [ScreenResult("detail", candy={"SNOM": 102})] * 2 + [ScreenResult("detail", candy={"SNOM": 162})]
        self.assertEqual(merge(newest, ScanReport(), times=[2.0, 2.0, 2.0])["candy"], {"SNOM": 102})

    def test_taken_at_reads_the_file_name(self):
        """A capture's time comes from the date in its file name, else its modified time."""
        def local(stamp):   # file names carry the phone's local time, with no time zone
            """A local-time file-name stamp as a timestamp."""
            return time.mktime(time.strptime(stamp, "%Y%m%d%H%M%S"))
        self.assertEqual(taken_at("Screenshot_20261011-115015.png"), local("20261011115015"))
        self.assertEqual(taken_at("VID_20261004_225901.mp4"), local("20261004225901"))
        with tempfile.TemporaryDirectory() as d:   # no date (or an impossible one): when the file was last modified
            for name in ("bag.png", "IMG_20261399_250000.png"):
                f = Path(d) / name
                f.write_bytes(b"")
                os.utime(f, (1_000_000, 1_000_000))
                self.assertEqual(taken_at(f), 1_000_000)

    def test_merge_majority_decides_caught(self):
        """Caught status is decided by a majority of frames; a tie decides nothing and warns."""
        frames = [ScreenResult("detail", dex_caught={"DRAGAPULT"}),        # one bad frame
                  ScreenResult("detail", dex_not_caught={"DRAGAPULT"}),
                  ScreenResult("pokedex", dex_not_caught={"DRAGAPULT"}),   # "Seen" list
                  ScreenResult("detail", dex_caught={"PAWMO"}), ScreenResult("detail", dex_not_caught={"PAWMO"})]
        rep = ScanReport()
        inv = merge(frames, rep)
        self.assertEqual((inv["not_caught"], inv["pokedex"]), (["DRAGAPULT"], []))
        self.assertTrue(any(x.startswith("PAWMO:") for x in rep.warnings))   # a tie decides nothing

    def test_merge_warns_when_rows_were_skipped(self):
        """Fewer Pokémon read than the search found means screenshots skipped rows; reading them all doesn't warn."""
        first = ScreenResult("storage", specimens=[("DRATINI", 412), ("FEEBAS", 126)], listed=3)
        rep = ScanReport()
        merge([first], rep)
        self.assertEqual((rep.listed, rep.grid), (3, 2))
        self.assertTrue(any(x.startswith("read 2 of the 3 Pokémon") for x in rep.warnings))
        rep = ScanReport()
        merge([first, ScreenResult("storage", specimens=[("FEEBAS", 126), ("CHANSEY", 389)], listed=3)], rep)
        self.assertEqual((rep.grid, rep.warnings), (3, []))   # an overlapping row is counted once

    def test_merge_votes_and_dedupes(self):
        """Merging votes on candy counts and counts each storage Pokémon once across frames."""
        a = ScreenResult("storage", specimens=[("DRATINI", 412), ("EEVEE", None), ("EEVEE", None)])
        b = ScreenResult("storage", specimens=[("DRATINI", 412), ("EEVEE", None)])
        c = ScreenResult("detail", candy={"DRATINI": 60})
        d = ScreenResult("detail", candy={"DRATINI": 66})
        e = ScreenResult("detail", candy={"DRATINI": 60})
        rep = ScanReport()
        inv = merge([a, b, c, d, e], rep)
        self.assertEqual(inv["candy"], {"DRATINI": 60})
        self.assertEqual(sorted(p["species"] for p in inv["pokemon"]), ["DRATINI", "EEVEE", "EEVEE"])
        self.assertTrue(any("conflicting DRATINI" in x for x in rep.warnings))

    def test_rapidocr_line_split(self):
        """A RapidOCR text line is split into word boxes in reading order."""
        words = _split_line([[100, 50], [500, 50], [500, 90], [100, 90]], "DRATINI CANDY", 0.95)
        self.assertEqual([x.text for x in words], ["DRATINI", "CANDY"])
        self.assertLess(words[0].x + words[0].w, words[1].x + 1)


class FrameTests(unittest.TestCase):
    def test_pauses_collapse_and_new_screens_split(self):
        """A recording's pauses collapse to one frame each, and each new screen gets its own."""
        with tempfile.TemporaryDirectory() as d:
            path = mockscreens.write_recording(Path(d) / "r.mp4")
            frames = extract_frames(path, fps=3)
        # 1 pause + scroll frames + pokedex + 4 detail screens; the 4 near-identical
        # detail screens must stay separate
        self.assertGreaterEqual(len(frames), 10)
        self.assertLess(len(frames), 20)


def _ocr_ok():
    """True when an OCR backend is installed."""
    try:
        available_backend()
        return True
    except RuntimeError:
        return False


@unittest.skipUnless(_ocr_ok(), "no OCR backend installed")
@unittest.skipIf(os.environ.get("SKIP_SLOW"), "SKIP_SLOW set")
class EndToEndTests(unittest.TestCase):
    """Full pipeline on synthetic screens (slow: real OCR on ~14 frames)."""

    def test_scan_recovers_ground_truth(self):
        """Scanning the synthetic bag and recording recovers the known inventory."""
        g = mockscreens.GROUND_TRUTH
        with tempfile.TemporaryDirectory() as d:
            bag, rec = mockscreens.write_fixtures(d)
            inv, _report = scan([bag, rec], SPECIES)
        self.assertEqual(inv["rare_candy"], g["rare_candy"])
        self.assertEqual(inv["candy"], g["candy"])
        self.assertEqual({(p["species"], p["cp"]) for p in inv["pokemon"]}, set(g["storage"]))
        self.assertEqual(set(inv["pokedex"]), {M.by_dex[n] for n in g["dex_caught"]})
        self.assertEqual(set(inv["pokedex_seen_only"]), {M.by_dex[n] for n in g["dex_seen"]})
        json.dumps(inv)  # serialisable


if __name__ == "__main__":
    unittest.main()
