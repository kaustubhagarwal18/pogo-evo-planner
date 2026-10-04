import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rarecandy.gamemaster import load_species
from rarecandy.scan.frames import extract_frames
from rarecandy.scan.ocr import Word, _split_line, available_backend
from rarecandy.scan.pipeline import ScanReport, merge, scan
from rarecandy.scan.screens import (
    ScreenResult,
    SpeciesMatcher,
    classify,
    parse_bag,
    parse_detail,
    parse_pokedex,
    parse_storage,
)

from . import mockscreens

SPECIES = load_species(Path(__file__).resolve().parent.parent / "rarecandy/data/species_sample.json")
M = SpeciesMatcher(SPECIES)


def w(text, x, y, width=None, h=40):
    return Word(text, x, y, width or 22 * len(text), h, 0.9)


class ParserTests(unittest.TestCase):
    def test_matcher_exact_fuzzy_and_rejects(self):
        self.assertEqual(M.match("Dratini"), "DRATINI")
        self.assertEqual(M.match("Dratlni"), "DRATINI")      # OCR l/i confusion
        self.assertEqual(M.match("MAGIKARP"), "MAGIKARP")
        self.assertIsNone(M.match("Candy"))
        self.assertIsNone(M.match("CP"))
        self.assertEqual(M.by_dex[147], "DRATINI")

    def test_classify(self):
        self.assertEqual(classify([w("Rare", 300, 900), w("Candy", 420, 900)]), "bag")
        self.assertEqual(classify([w("DRATINI", 600, 1300), w("CANDY", 780, 1300), w("EVOLVE", 400, 1700)]), "detail")
        self.assertEqual(classify([w("POKEDEX", 400, 100)]), "pokedex")
        self.assertEqual(classify([w("CP", x, 200) for x in (100, 400, 700)]), "storage")
        self.assertEqual(classify([w("hello", 10, 10)]), "unknown")

    def test_bag_count_right_of_label_ignores_xl(self):
        words = [w("Rare", 300, 1160), w("Candy", 420, 1160), w("XL", 560, 1160), w("x3", 860, 1160),
                 w("Rare", 300, 900), w("Candy", 420, 900), w("x150", 860, 900)]
        self.assertEqual(parse_bag(words).rare_candy, 150)

    def test_bag_count_below_label(self):
        words = [w("Rare", 300, 900), w("Candy", 420, 900), w("1,204", 330, 960)]
        self.assertEqual(parse_bag(words).rare_candy, 1204)

    def test_detail_reads_family_candy_above_label(self):
        words = [w("Dragonair", 400, 900, h=80), w("48,210", 200, 1240), w("STARDUST", 200, 1320),
                 w("60", 760, 1240), w("DRATINI", 640, 1320), w("CANDY", 820, 1320), w("EVOLVE", 450, 1760)]
        self.assertEqual(parse_detail(words, M, SPECIES).candy, {"DRATINI": 60})

    def test_storage_pairs_cp_in_same_cell(self):
        words = [w("CP", 92, 146), w("412", 176, 146), w("CP", 436, 145), w("1543", 523, 145),
                 w("Dratini", 95, 436, 160), w("Dragonair", 415, 436, 220)]
        self.assertEqual(sorted(parse_storage(words, M).specimens), [("DRAGONAIR", 1543), ("DRATINI", 412)])

    def test_storage_skips_cell_whose_cp_is_off_screen(self):
        words = [w("CP", 92, 600), w("389", 176, 600), w("Dragonair", 415, 100, 220), w("Chansey", 73, 890, 170)]
        self.assertEqual(parse_storage(words, M).specimens, [("CHANSEY", 389)])

    def test_pokedex_colour_decides_caught(self):
        img = np.full((800, 600, 3), 235, np.uint8)
        img[100:300, 50:250] = (40, 120, 230)    # coloured sprite (BGR)
        img[100:300, 350:550] = (75, 70, 70)     # grey silhouette
        words = [w("#0147", 90, 320, 120), w("#0149", 390, 320, 120)]
        r = parse_pokedex(words, img, M)
        self.assertEqual((r.dex_caught, r.dex_seen_only), ({"DRATINI"}, {"DRAGONITE"}))

    def test_merge_votes_and_dedupes(self):
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
        words = _split_line([[100, 50], [500, 50], [500, 90], [100, 90]], "DRATINI CANDY", 0.95)
        self.assertEqual([x.text for x in words], ["DRATINI", "CANDY"])
        self.assertLess(words[0].x + words[0].w, words[1].x + 1)


class FrameTests(unittest.TestCase):
    def test_pauses_collapse_and_new_screens_split(self):
        with tempfile.TemporaryDirectory() as d:
            path = mockscreens.write_recording(Path(d) / "r.mp4")
            frames = extract_frames(path, fps=3)
        # 1 pause + scroll frames + pokedex + 4 detail screens; the 4 near-identical
        # detail screens must stay separate
        self.assertGreaterEqual(len(frames), 10)
        self.assertLess(len(frames), 20)


def _ocr_ok():
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
