import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.scan.frames import extract_frames
from pogo_evo_planner.scan.ocr import (
    Word,
    _split_line,
    available_backend,
    merge_passes,
    tesseract_passes,
    words_from_tsv,
)
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
MATCHER = SpeciesMatcher(SPECIES)


def evolve_button(sprite: str) -> tuple[np.ndarray, Word]:
    """A green EVOLVE button like a 576 px wide phone shows, with a colour or silhouette sprite."""
    image = np.full((1296, 576, 3), 245, np.uint8)
    green = cv2.cvtColor(np.uint8([[[57, 88, 228]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    cv2.rectangle(image, (40, 975), (400, 1060), green, -1)
    if sprite == "silhouette":  # a darker shade of the button green
        dark = cv2.cvtColor(np.uint8([[[57, 110, 140]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        cv2.circle(image, (100, 1016), 26, dark, -1)
    elif sprite == "dark":  # a dark-bodied Pokémon in colour, like Kilowattrel: near-black, thin yellow trim
        dark = cv2.cvtColor(np.uint8([[[57, 110, 140]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        cv2.circle(image, (100, 1016), 26, dark, -1)
        cv2.circle(image, (100, 1016), 9, (30, 30, 35), -1)
        cv2.line(image, (76, 1004), (124, 1004), (40, 200, 230), 2)
    else:  # white body with a purple patch
        cv2.circle(image, (100, 1016), 26, (250, 250, 250), -1)
        cv2.circle(image, (92, 1008), 10, (160, 90, 140), -1)
    return image, Word("EVOLVE", 140, 1003, 103, 27, 0.9)


def make_word(text, left, top, width=None, height=40):
    """A word box sized from the text, as OCR returns it."""
    return Word(text, left, top, width or 22 * len(text), height, 0.9)


class ParserTests(unittest.TestCase):
    def test_matcher_exact_fuzzy_and_rejects(self):
        """Names match exactly or fuzzily (OCR slips); too-short or unknown text doesn't match."""
        self.assertEqual(MATCHER.match("Dratini"), "DRATINI")
        self.assertEqual(MATCHER.match("Dratlni"), "DRATINI")  # OCR l/i confusion
        self.assertEqual(MATCHER.match("MAGIKARP"), "MAGIKARP")
        self.assertIsNone(MATCHER.match("Candy"))
        self.assertIsNone(MATCHER.match("CP"))
        self.assertEqual(MATCHER.by_dex[147], "DRATINI")

    def test_classify(self):
        """Screens are classified as bag, detail, Pokédex or storage from their words."""
        self.assertEqual(classify([make_word("Rare", 300, 900), make_word("Candy", 420, 900)]), "bag")
        self.assertEqual(
            classify([make_word("DRATINI", 600, 1300), make_word("CANDY", 780, 1300), make_word("EVOLVE", 400, 1700)]),
            "detail",
        )
        self.assertEqual(classify([make_word("POKEDEX", 400, 100)]), "pokedex")
        self.assertEqual(classify([make_word("CP", left, 200) for left in (100, 400, 700)]), "storage")
        self.assertEqual(classify([make_word("hello", 10, 10)]), "unknown")

    def test_bag_count_right_of_label_ignores_xl(self):
        """The bag's Rare Candy count is read right of its label, ignoring Rare Candy XL."""
        words = [
            make_word("Rare", 300, 1160),
            make_word("Candy", 420, 1160),
            make_word("XL", 560, 1160),
            make_word("x3", 860, 1160),
            make_word("Rare", 300, 900),
            make_word("Candy", 420, 900),
            make_word("x150", 860, 900),
        ]
        self.assertEqual(parse_bag(words).rare_candy, 150)

    def test_bag_count_below_label(self):
        """The bag's Rare Candy count is read below the label when it isn't beside it."""
        words = [make_word("Rare", 300, 900), make_word("Candy", 420, 900), make_word("1,204", 330, 960)]
        self.assertEqual(parse_bag(words).rare_candy, 1204)

    def test_detail_reads_family_candy_above_label(self):
        """A detail page's family candy is the number above '<FAMILY> CANDY'."""
        words = [
            make_word("Dragonair", 400, 900, height=80),
            make_word("48,210", 200, 1240),
            make_word("STARDUST", 200, 1320),
            make_word("60", 760, 1240),
            make_word("DRATINI", 640, 1320),
            make_word("CANDY", 820, 1320),
            make_word("EVOLVE", 450, 1760),
        ]
        self.assertEqual(parse_detail(words, MATCHER, SPECIES).candy, {"DRATINI": 60})

    def test_evolve_button_sprite_colour_or_silhouette(self):
        """An Evolve-button sprite in colour reads as caught, a silhouette as never caught."""
        self.assertTrue(evolve_sprite_caught(*evolve_button("colour")))
        self.assertFalse(evolve_sprite_caught(*evolve_button("silhouette")))
        self.assertTrue(evolve_sprite_caught(*evolve_button("dark")))  # real Kilowattrel misread before

    def test_evolve_button_colour_does_not_matter(self):
        """On a grey button the sprite decides the same way as on a green one."""
        # buttons come grey, pink or blue-grey as well as green; the sprite alone decides
        for sprite, expected in (("silhouette", False), ("colour", True)):
            image, evolve_label = evolve_button(sprite)
            button = np.all(image == image[980, 300], axis=2)
            image[button] = (215, 218, 220)
            if sprite == "silhouette":  # a darker shade of the grey button
                image[np.all(image == image[1016, 100], axis=2)] = (120, 122, 124)
            self.assertIs(evolve_sprite_caught(image, evolve_label), expected)

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
            got = answer[
                evolve_sprite_caught(cv2.imread(str(folder / f"{name}.png")), Word("EVOLVE", *case["label_box"], 0.9))
            ]
            if got != case["expected"]:
                wrong[name] = f"{got}, expected {case['expected']}"
        self.assertEqual(wrong, {})

    def test_evolve_buttons_skip_hint_text_and_mega(self):
        """Only the capitalised EVOLVE label counts: not hint text, not MEGA EVOLVE."""
        button = make_word("EVOLVE", 270, 1890)
        hint = [
            make_word("Adventure", 180, 2040),
            make_word("together", 420, 2040),
            make_word("to", 600, 2040),
            make_word("evolve", 650, 2040),
        ]
        mega = [make_word("MEGA", 270, 1680), make_word("EVOLVE", 270, 1730)]  # stacked on two lines
        self.assertEqual(evolve_buttons([button] + hint), [button])
        self.assertEqual(evolve_buttons(mega), [])
        self.assertEqual(evolve_buttons([make_word("MEGA", 150, 1700), make_word("EVOLVE", 270, 1700)]), [])

    def test_detail_reads_evolution_never_caught_from_silhouette(self):
        """A detail page's silhouette marks the evolution never caught; colour marks it caught."""
        image, evolve_label = evolve_button("silhouette")
        words = [
            make_word("Dratini", 200, 400, height=80),
            make_word("60", 300, 600),
            make_word("DRATINI", 250, 680),
            make_word("CANDY", 420, 680),
            evolve_label,
        ]
        result = parse_detail(words, MATCHER, SPECIES, image)
        self.assertEqual(
            (result.candy, result.dex_not_caught, result.dex_caught), ({"DRATINI": 60}, {"DRAGONAIR"}, set())
        )
        image, evolve_label = evolve_button("colour")
        self.assertEqual(parse_detail(words[:-1] + [evolve_label], MATCHER, SPECIES, image).dex_caught, {"DRAGONAIR"})

    def test_detail_ignores_candy_xl_label_wrapped_onto_next_line(self):
        """The Candy XL count is ignored even when 'XL' wraps onto the next line."""
        # narrow phones: "DRATINI CANDY" on one line, "XL" centred under it, XL count above
        words = [
            make_word("48,210", 100, 1240),
            make_word("STARDUST", 100, 1320),
            make_word("60", 460, 1240),
            make_word("DRATINI", 380, 1320),
            make_word("CANDY", 550, 1320),
            make_word("7", 820, 1240),
            make_word("DRATINI", 740, 1320),
            make_word("CANDY", 910, 1320),
            make_word("XL", 830, 1370),
            make_word("EVOLVE", 450, 1760),
        ]
        self.assertEqual(parse_detail(words, MATCHER, SPECIES).candy, {"DRATINI": 60})

    def test_detail_reads_candy_label_wrapped_onto_two_lines(self):
        """A candy label whose name wraps above 'CANDY' is still read (a real Greavard page)."""
        # real Greavard page: "GREAVARD" above "CANDY"; the Candy XL label wraps the same way
        words = [
            Word("9,176,980", 116, 1480, 260, 57, 0.9),
            Word("77", 533, 1480, 50, 53, 0.9),
            Word("19", 865, 1480, 50, 54, 0.9),
            Word("STARDUST", 151, 1548, 190, 32, 0.9),
            Word("GREAVARD", 480, 1548, 190, 32, 0.9),
            Word("GREAVARD", 811, 1550, 190, 32, 0.9),
            Word("CANDY", 511, 1584, 120, 34, 0.9),
            Word("CANDY", 819, 1585, 110, 32, 0.9),
            Word("XL", 941, 1585, 40, 32, 0.9),
        ]
        species = {**SPECIES, "GREAVARD": {"dex": 971, "family": "GREAVARD", "buddy_km": 3}}
        self.assertEqual(parse_detail(words, SpeciesMatcher(species), species).candy, {"GREAVARD": 77})

    def test_storage_pairs_cp_in_same_cell(self):
        """Each storage name gets the CP above it in the same cell."""
        words = [
            make_word("CP", 92, 146),
            make_word("412", 176, 146),
            make_word("CP", 436, 145),
            make_word("1543", 523, 145),
            make_word("Dratini", 95, 436, 160),
            make_word("Dragonair", 415, 436, 220),
        ]
        self.assertEqual(sorted(parse_storage(words, MATCHER).specimens), [("DRAGONAIR", 1543), ("DRATINI", 412)])

    def test_storage_skips_cell_whose_cp_is_off_screen(self):
        """A storage name whose CP is scrolled off-screen is skipped."""
        words = [
            make_word("CP", 92, 600),
            make_word("389", 176, 600),
            make_word("Dragonair", 415, 100, 220),
            make_word("Chansey", 73, 890, 170),
        ]
        self.assertEqual(parse_storage(words, MATCHER).specimens, [("CHANSEY", 389)])

    def test_storage_reads_how_many_a_search_found(self):
        """The "(210)" below the POKÉMON tab is how many Pokémon a search found; a count elsewhere isn't."""
        tabs = [
            make_word("POKÉMON", 431, 106, 218, 49),
            make_word("EGGS", 811, 110),
            make_word("11/12", 811, 171),
            make_word("(5)", 456, 900),
        ]
        cells = [make_word("CP", left, 600) for left in (100, 400, 700)]
        self.assertEqual(parse_storage(tabs + [make_word("Q(210)", 456, 170, 130, 47)] + cells, MATCHER).listed, 210)
        self.assertIsNone(parse_storage(tabs + cells, MATCHER).listed)  # no search: nothing to check against

    def test_pokedex_colour_decides_caught(self):
        """On an unfiltered Pokédex a coloured sprite reads as caught, a grey one as seen only."""
        image = np.full((800, 600, 3), 235, np.uint8)
        image[100:300, 50:250] = (40, 120, 230)  # coloured sprite (BGR)
        image[100:300, 350:550] = (75, 70, 70)  # grey silhouette
        words = [make_word("#0147", 90, 320, 120), make_word("#0149", 390, 320, 120)]
        result = parse_pokedex(words, image, MATCHER)
        self.assertEqual((result.dex_caught, result.dex_seen_only), ({"DRATINI"}, {"DRAGONITE"}))

    def test_pokedex_seen_filter_lists_never_caught(self):
        """With the Seen filter every entry is never caught; status-bar and header numbers are ignored."""
        # real "Seen" screen: the status bar's battery (60) and "Caught: 893" are not dex numbers
        words = [
            Word("11:48", 80, 41, 90, 38, 0.9),
            Word("60", 928, 43, 40, 37, 0.9),
            Word("POKÉDEX", 45, 127, 190, 53, 0.9),
            Word("Caught:", 750, 133, 150, 52, 0.9),
            Word("893", 929, 133, 70, 52, 0.9),
            Word("Seen", 248, 263, 90, 51, 0.9),
            Word("0147", 100, 865, 110, 51, 0.9),
            Word("0148", 351, 864, 110, 52, 0.9),
        ]
        result = parse_pokedex(words, None, MATCHER)
        self.assertEqual(
            (result.dex_not_caught, result.dex_caught, result.dex_seen_only), ({"DRATINI", "DRAGONAIR"}, set(), set())
        )
        # unfiltered, the same header numbers are still ignored
        result = parse_pokedex([word for word in words if word.text != "Seen"], None, MATCHER)
        self.assertEqual(result.dex_caught, {"DRATINI", "DRAGONAIR"})

    def test_merge_prefers_the_newest_capture(self):
        """When captures disagree on a count the newest wins; within one, the most common reading."""
        # Snom: an older recording read 80 three times, today's screenshot reads 102
        frames = [ScreenResult("detail", candy={"SNOM": 80})] * 3 + [
            ScreenResult("detail", candy={"SNOM": 102}),
            ScreenResult("bag", rare_candy=300),
            ScreenResult("bag", rare_candy=305),
        ]
        report = ScanReport()
        inventory = merge(frames, report, times=[1.0, 1.0, 1.0, 2.0, 1.0, 2.0])
        self.assertEqual((inventory["candy"], inventory["rare_candy"]), ({"SNOM": 102}, 305))
        self.assertTrue(any("using 102, from the newest capture" in warning for warning in report.warnings))
        # within one capture the most common reading still outvotes a misread frame
        newest = [ScreenResult("detail", candy={"SNOM": 102})] * 2 + [ScreenResult("detail", candy={"SNOM": 162})]
        self.assertEqual(merge(newest, ScanReport(), times=[2.0, 2.0, 2.0])["candy"], {"SNOM": 102})

    def test_taken_at_reads_the_file_name(self):
        """A capture's time comes from the date in its file name, else its modified time."""

        def local(stamp):  # file names carry the phone's local time, with no time zone
            """A local-time file-name stamp as a timestamp."""
            return time.mktime(time.strptime(stamp, "%Y%m%d%H%M%S"))

        self.assertEqual(taken_at("Screenshot_20261011-115015.png"), local("20261011115015"))
        self.assertEqual(taken_at("VID_20261004_225901.mp4"), local("20261004225901"))
        with tempfile.TemporaryDirectory() as folder:  # no date (or an impossible one): when the file was last modified
            for name in ("bag.png", "IMG_20261399_250000.png"):
                path = Path(folder) / name
                path.write_bytes(b"")
                os.utime(path, (1_000_000, 1_000_000))
                self.assertEqual(taken_at(path), 1_000_000)

    def test_merge_majority_decides_caught(self):
        """Caught status is decided by a majority of frames; a tie decides nothing and warns."""
        frames = [
            ScreenResult("detail", dex_caught={"DRAGAPULT"}),  # one bad frame
            ScreenResult("detail", dex_not_caught={"DRAGAPULT"}),
            ScreenResult("pokedex", dex_not_caught={"DRAGAPULT"}),  # "Seen" list
            ScreenResult("detail", dex_caught={"PAWMO"}),
            ScreenResult("detail", dex_not_caught={"PAWMO"}),
        ]
        report = ScanReport()
        inventory = merge(frames, report)
        self.assertEqual((inventory["not_caught"], inventory["pokedex"]), (["DRAGAPULT"], []))
        self.assertTrue(any(warning.startswith("PAWMO:") for warning in report.warnings))  # a tie decides nothing

    def test_merge_warns_when_rows_were_skipped(self):
        """Fewer Pokémon read than the search found means screenshots skipped rows; reading them all doesn't warn."""
        first = ScreenResult("storage", specimens=[("DRATINI", 412), ("FEEBAS", 126)], listed=3)
        report = ScanReport()
        merge([first], report)
        self.assertEqual((report.listed, report.grid), (3, 2))
        self.assertTrue(any(warning.startswith("read 2 of the 3 Pokémon") for warning in report.warnings))
        report = ScanReport()
        merge([first, ScreenResult("storage", specimens=[("FEEBAS", 126), ("CHANSEY", 389)], listed=3)], report)
        self.assertEqual((report.grid, report.warnings), (3, []))  # an overlapping row is counted once

    def test_merge_votes_and_dedupes(self):
        """Merging votes on candy counts and counts each storage Pokémon once across frames."""
        first_storage = ScreenResult("storage", specimens=[("DRATINI", 412), ("EEVEE", None), ("EEVEE", None)])
        second_storage = ScreenResult("storage", specimens=[("DRATINI", 412), ("EEVEE", None)])
        first_detail = ScreenResult("detail", candy={"DRATINI": 60})
        second_detail = ScreenResult("detail", candy={"DRATINI": 66})
        third_detail = ScreenResult("detail", candy={"DRATINI": 60})
        report = ScanReport()
        inventory = merge([first_storage, second_storage, first_detail, second_detail, third_detail], report)
        self.assertEqual(inventory["candy"], {"DRATINI": 60})
        self.assertEqual(sorted(entry["species"] for entry in inventory["pokemon"]), ["DRATINI", "EEVEE", "EEVEE"])
        self.assertTrue(any("conflicting DRATINI" in warning for warning in report.warnings))

    def test_tsv_from_the_command_line_or_tesseract_js(self):
        """Tesseract's TSV is read with the command line's header row or without it, as tesseract.js writes it, with
        boxes scaled back to the screenshot and empty or unconfident rows left out."""
        rows = (
            "5\t1\t1\t1\t1\t1\t300\t150\t60\t30\t91.5\tRare\n"
            "5\t1\t1\t1\t1\t2\t390\t150\t90\t30\t88\tCandy\n"
            "4\t1\t1\t1\t1\t0\t300\t150\t180\t30\t-1\t\n"
            "5\t1\t1\t1\t1\t3\t600\t150\t60\t30\t-1\tx\n"
        )
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        self.assertEqual(words_from_tsv(rows, 1.5), words_from_tsv(header + rows, 1.5))
        self.assertEqual(
            words_from_tsv(rows, 1.5), [Word("Rare", 200, 100, 40, 20, 0.915), Word("Candy", 260, 100, 60, 20, 0.88)]
        )

    def test_passes_grey_negative_and_upscaled(self):
        """Tesseract reads a screen in grey, as a negative, and upscaled to 1600 px wide when it's narrower."""
        image = np.zeros((2400, 1080, 3), np.uint8)
        image[100:200, 100:300] = (40, 200, 90)
        (grey, grey_scale), (negative, negative_scale), (big, big_scale) = tesseract_passes(image)
        self.assertEqual((grey_scale, negative_scale, big_scale), (1.0, 1.0, 1600 / 1080))
        self.assertTrue((negative == 255 - grey).all())
        self.assertEqual(big.shape, (round(2400 * 1600 / 1080), 1600))
        self.assertEqual(tesseract_passes(np.zeros((3000, 2000, 3), np.uint8))[2][1], 1.0)

    def test_merge_passes_counts_a_word_once(self):
        """The same word read in several passes counts once, keeping the most confident reading."""
        unsure, confident = Word("CANDY", 100, 100, 80, 30, 0.6), Word("candy", 103, 101, 80, 30, 0.9)
        self.assertEqual(
            merge_passes([[unsure], [confident, make_word("60", 100, 50)]]), [confident, make_word("60", 100, 50)]
        )

    def test_scan_takes_words_read_elsewhere(self):
        """scan() parses words it's given (the browser's tesseract.js) instead of running OCR itself."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bag.png"
            cv2.imwrite(str(path), mockscreens.bag_screen())
            given = []

            def read_text(label, image):
                """The words of a bag screen, noting which frame was asked for."""
                given.append((label, image.shape))
                return [make_word("Rare", 300, 900), make_word("Candy", 420, 900), make_word("x150", 860, 900)]

            with mock.patch("pogo_evo_planner.scan.pipeline.read_words", side_effect=AssertionError("ran OCR")):
                inventory, report = scan([path], SPECIES, read_text=read_text)
        self.assertEqual(given, [("bag.png", mockscreens.bag_screen().shape)])
        self.assertEqual((inventory["rare_candy"], dict(report.screens)), (150, {"bag": 1}))

    def test_rapidocr_line_split(self):
        """A RapidOCR text line is split into word boxes in reading order."""
        words = _split_line([[100, 50], [500, 50], [500, 90], [100, 90]], "DRATINI CANDY", 0.95)
        self.assertEqual([word.text for word in words], ["DRATINI", "CANDY"])
        self.assertLess(words[0].x + words[0].w, words[1].x + 1)


class FrameTests(unittest.TestCase):
    def test_pauses_collapse_and_new_screens_split(self):
        """A recording's pauses collapse to one frame each, and each new screen gets its own."""
        with tempfile.TemporaryDirectory() as folder:
            path = mockscreens.write_recording(Path(folder) / "r.mp4")
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
        truth = mockscreens.GROUND_TRUTH
        with tempfile.TemporaryDirectory() as folder:
            bag, recording = mockscreens.write_fixtures(folder)
            inventory, _report = scan([bag, recording], SPECIES)
        self.assertEqual(inventory["rare_candy"], truth["rare_candy"])
        self.assertEqual(inventory["candy"], truth["candy"])
        self.assertEqual({(entry["species"], entry["cp"]) for entry in inventory["pokemon"]}, set(truth["storage"]))
        self.assertEqual(set(inventory["pokedex"]), {MATCHER.by_dex[dex_number] for dex_number in truth["dex_caught"]})
        self.assertEqual(
            set(inventory["pokedex_seen_only"]), {MATCHER.by_dex[dex_number] for dex_number in truth["dex_seen"]}
        )
        json.dumps(inventory)  # serialisable


if __name__ == "__main__":
    unittest.main()
