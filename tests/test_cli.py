"""Command-line behaviour: one readable line instead of a traceback, and the options every command shares."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from pogo_evo_planner import cli
from pogo_evo_planner.scan.pipeline import load_images

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = str(ROOT / "pogo_evo_planner/data/species_sample.json")


def run(*argv: str) -> tuple[str, str]:
    """Run the command line; return its exit message ("" when it didn't exit) and what it printed."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
        try:
            cli.main(list(argv))
        except SystemExit as stop:
            return str(stop.code), printed.getvalue()
    return "", printed.getvalue()


class CliTests(unittest.TestCase):
    def test_missing_or_broken_files_give_one_line(self):
        """A missing or broken inventory or species file exits with one line naming it, not a traceback."""
        with tempfile.TemporaryDirectory() as folder:
            files = {
                "bad.json": '{"rare_candy": 1,',
                "list.json": "[1, 2]",
                "no_species.json": '{"pokemon": [{"cp": 10}]}',
            }
            for name, text in files.items():
                (Path(folder) / name).write_text(text)
            cases = [
                (["plan", "nothere.json"], "can't read the inventory nothere.json: No such file or directory"),
                (
                    ["dashboard", "nothere.json", "-o", str(Path(folder) / "x.html")],
                    "can't read the inventory nothere.json",
                ),
                (
                    ["plan", str(Path(folder) / "bad.json")],
                    "bad.json isn't a valid inventory file: Expecting property name",
                ),
                (["plan", str(Path(folder) / "list.json")], "list.json isn't a valid inventory file"),
                (["plan", str(Path(folder) / "no_species.json")], "an entry has no 'species' field"),
            ]
            for argv, expected in cases:
                with self.subTest(argv=argv[:2]):
                    message, _ = run(*argv, "--species", SAMPLE)
                    self.assertTrue(message.startswith("error: "), message)
                    self.assertIn(expected, message)
        message, _ = run("plan", str(ROOT / "examples/inventory_sample.json"), "--species", "nothere.json")
        self.assertEqual(message, "error: can't read the species table nothere.json: No such file or directory")

    def test_missing_screenshot_or_recording(self):
        """A missing screenshot or recording is reported before OpenCV tries to open it."""
        for name in ("nothere.png", "nothere.mp4"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"can't read {name}: not found"):
                load_images([name])
        message, _ = run("scan", "nothere.mp4", "--species", SAMPLE, "-q")
        self.assertEqual(message, "error: can't read nothere.mp4: not found")

    def test_missing_packages_name_the_install_command(self):
        """Without the scanning or LLM packages, the error names the module and the matching install command."""
        cases = [
            ("auto", "cv2", "error: reading screens needs the cv2 module", "pip install -e '.[ocr]'"),
            ("claude", "anthropic", "error: --ocr claude needs the anthropic module", "pip install -e '.[llm]'"),
        ]
        for reader, module, what, fix in cases:
            gone = ModuleNotFoundError(f"No module named '{module}'", name=module)
            with self.subTest(reader=reader), mock.patch("pogo_evo_planner.scan.pipeline.scan", side_effect=gone):
                message, _ = run("scan", "a.png", "--ocr", reader, "--species", SAMPLE, "-q")
                self.assertTrue(message.startswith(what), message)
                self.assertIn(fix, message)

    def test_no_ocr_engine(self):
        """With no OCR engine installed, the scan says how to install one instead of showing a traceback."""
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch("pogo_evo_planner.scan.ocr.rapidocr_available", return_value=False),
            mock.patch("pogo_evo_planner.scan.ocr.tesseract_available", return_value=False),
        ):
            shot = Path(folder) / "shot.png"
            cv2.imwrite(str(shot), np.full((40, 40, 3), 255, np.uint8))
            message, _ = run("scan", str(shot), "--species", SAMPLE, "-q", "-o", str(Path(folder) / "inv.json"))
        self.assertTrue(message.startswith("error: no OCR engine found"), message)
        self.assertIn("pip install -e '.[ocr]'", message)

    def test_evolvenew_has_the_reader_options_of_scan(self):
        """evolvenew takes the same reader options as scan, so the Claude frame cap it mentions can be raised."""
        with mock.patch("pogo_evo_planner.scan.pipeline.scan", side_effect=ValueError("stop")) as scan:
            message, _ = run(
                "evolvenew",
                "results.mp4",
                "--ocr",
                "claude",
                "--llm-max-frames",
                "100",
                "--llm-model",
                "a-model",
                "--species",
                SAMPLE,
                "-q",
            )
        self.assertEqual(message, "error: stop")
        self.assertEqual(
            {option: scan.call_args.kwargs[option] for option in ("backend", "max_llm_frames", "llm_model")},
            {"backend": "claude", "max_llm_frames": 100, "llm_model": "a-model"},
        )

    def test_plan_names_families_without_a_candy_count(self):
        """The text plan says which families got no Rare Candy because their candy count is unknown."""
        with tempfile.TemporaryDirectory() as folder:
            inventory_path = Path(folder) / "inv.json"
            inventory_path.write_text(json.dumps({"rare_candy": 200, "candy": {}, "pokemon": [{"species": "DRATINI"}]}))
            message, printed = run("plan", str(inventory_path), "--species", SAMPLE)
        self.assertEqual(message, "")
        self.assertIn("Rare candy: using 0 of 200", printed)
        self.assertIn('No candy count, so no Rare Candy planned for: Dratini (add their counts under "candy")', printed)

    def test_plan_takes_items_by_the_names_players_use(self):
        """`plan` uses a Sinnoh Stone written as ITEM_SINNOH_STONE (as the docs once showed), names any item no
        evolution uses, and stops with one line when a file lists the same item under two names."""
        kirlia = {"rare_candy": 0, "candy": {"RALTS": 300}, "pokemon": [{"species": "KIRLIA"}, {"species": "KIRLIA"}]}
        with tempfile.TemporaryDirectory() as folder:
            inventory_path = Path(folder) / "inv.json"
            inventory_path.write_text(json.dumps({**kirlia, "items": {"ITEM_SINNOH_STONE": 1, "Moon Stone": 1}}))
            message, printed = run("plan", str(inventory_path), "--species", SAMPLE)
            self.assertEqual(message, "")
            self.assertIn("Kirlia -> Gallade", printed)
            self.assertIn("Gallade: needs Sinnoh Stone", printed)  # the name players know, not the game's id
            self.assertIn("No evolution uses these items, so the plan ignores them: Moon Stone", printed)
            inventory_path.write_text(
                json.dumps({**kirlia, "items": {"Sinnoh Stone": 1, "ITEM_GEN4_EVOLUTION_STONE": 2}})
            )
            message, _ = run("plan", str(inventory_path), "--species", SAMPLE)
        self.assertEqual(
            message,
            f'error: {inventory_path} isn\'t a valid inventory file: "items" lists Sinnoh Stone twice, '
            "as Sinnoh Stone and ITEM_GEN4_EVOLUTION_STONE; keep one",
        )

    def test_an_output_that_cant_be_written_gives_one_line(self):
        """A dashboard written into a folder that doesn't exist stops with one line, not a traceback."""
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "missing" / "dashboard.html"
            message, _ = run(
                "dashboard", str(ROOT / "examples/inventory_sample.json"), "--species", SAMPLE, "-o", str(output)
            )
        self.assertEqual(message, f"error: can't write the dashboard {output}: No such file or directory")

    def test_ctrl_c_stops_without_a_traceback(self):
        """Ctrl-C stops a command with the shell's usual exit code and no traceback."""
        with (
            mock.patch("pogo_evo_planner.cli.cmd_plan", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(io.StringIO()) as printed,
            self.assertRaises(SystemExit) as stop,
        ):
            cli.main(["plan", "inventory.json"])
        self.assertEqual((stop.exception.code, printed.getvalue().strip()), (130, "stopped"))


if __name__ == "__main__":
    unittest.main()
