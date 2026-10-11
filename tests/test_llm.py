"""The LLM scanning backends (Claude, Ollama) with fake clients: no network, no API key, no cost."""
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from pogo_evo_planner.gamemaster import load_species
from pogo_evo_planner.scan.pipeline import scan
from pogo_evo_planner.scan.screens import SpeciesMatcher

try:
    from pogo_evo_planner.scan import llm
except ImportError:  # the llm extras (anthropic, guardrails-ai) aren't installed
    llm = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
SPECIES = load_species(ROOT / "pogo_evo_planner/data/species_sample.json")


def reading(**fields):
    base = {"kind": "other", "rare_candy": None, "species": None, "candy_name": None, "candy": None,
            "evolve_sprite": "none", "specimens": [], "pokedex": []}
    return llm.ScreenReading.model_validate({**base, **fields})


class FakeClient:
    """Stands in for anthropic.Anthropic(): returns queued readings and records each request."""

    def __init__(self, *readings, stop_reason="end_turn"):
        self.queue, self.calls = list(readings), []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        r = self.queue.pop(0) if self.queue else None
        block = SimpleNamespace(model_dump=lambda **_: {"type": "text", "text": r.model_dump_json() if r else ""})
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=r, content=[block])


class FakeOllama:
    """Stands in for ollama.Client(): returns queued JSON replies and records each request."""

    def __init__(self, *replies):
        self.queue, self.calls = list(replies), []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.queue.pop(0)))


IMG = np.zeros((40, 20, 3), np.uint8)


@unittest.skipUnless(llm, "needs the llm extras: pip install -e '.[llm]'")
class ClaudeScanTests(unittest.TestCase):
    def setUp(self):
        self.m = SpeciesMatcher(SPECIES)

    def read(self, client):
        model = client if isinstance(client, llm.Ollama) else llm.Claude(client=client)
        found, notes = llm.read_reading(IMG, model, llm.build_guard(), self.m)
        return (llm.to_screen_result(found, self.m, SPECIES) if found else None), notes

    def test_request_shape(self):
        client = FakeClient(reading(kind="bag", rare_candy=150))
        self.read(client)
        call = client.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertIs(call["output_format"], llm.ScreenReading)
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["messages"][0]["content"][0]["type"], "image")

    def test_bag_and_fuzzy_species_names(self):
        res, notes = self.read(FakeClient(reading(kind="bag", rare_candy=150)))
        self.assertEqual((res.kind, res.rare_candy, notes), ("bag", 150, []))
        res, _ = self.read(FakeClient(reading(kind="storage", specimens=[{"name": "Dragonalr", "cp": 1500}])))
        self.assertEqual(res.specimens, [("DRAGONAIR", 1500)])

    def test_retry_uses_the_corrected_reading(self):
        bad = reading(kind="storage", specimens=[{"name": "Dratini", "cp": 99999}])
        good = reading(kind="storage", specimens=[{"name": "Dratini", "cp": 999}])
        client = FakeClient(bad, good)
        res, notes = self.read(client)
        self.assertEqual((res.specimens, notes), ([("DRATINI", 999)], []))
        retry = client.calls[1]["messages"]
        self.assertEqual([m["role"] for m in retry], ["user", "assistant", "user"])
        self.assertIn("$.specimens.0.cp", retry[2]["content"])
        self.assertNotIn("parsed_output", retry[1]["content"][0])

    def test_values_still_failing_after_the_retry_are_dropped(self):
        bad = reading(kind="storage", specimens=[{"name": "Dratini", "cp": 500}, {"name": "Notamon", "cp": 500},
                                                 {"name": "Feebas", "cp": 99999}])
        res, notes = self.read(FakeClient(bad, bad))
        self.assertEqual(res.specimens, [("DRATINI", 500)])
        self.assertEqual(len(notes), 2)
        bag, notes = self.read(FakeClient(reading(kind="bag"), reading(kind="bag")))
        self.assertIsNone(bag.rare_candy)
        self.assertIn("no Rare Candy count", notes[0])

    def test_detail_page_candy_and_evolve_button(self):
        res, _ = self.read(FakeClient(reading(kind="detail", species="Dratini", candy_name="DRATINI", candy=60,
                                              evolve_sprite="silhouette")))
        self.assertEqual(res.candy, {"DRATINI": 60})
        self.assertEqual(res.dex_not_caught, {"DRAGONAIR"})
        res, _ = self.read(FakeClient(reading(kind="detail", species="Eevee", candy_name="EEVEE", candy=30,
                                              evolve_sprite="colour")))
        # branched line: several Evolve buttons, so the sprite can't be pinned to one species
        self.assertEqual((res.dex_caught, res.dex_not_caught), (set(), set()))

    def test_pokedex_entries(self):
        entries = [{"number": 147, "caught": True}, {"number": 148, "caught": False},
                   {"number": 99999, "caught": True}]
        res, notes = self.read(FakeClient(*[reading(kind="pokedex", pokedex=entries)] * 2))
        self.assertEqual((res.dex_caught, res.dex_seen_only), ({"DRATINI"}, {"DRAGONAIR"}))
        self.assertEqual(len(notes), 1)

    def test_refusal_gives_no_reading(self):
        res, notes = self.read(FakeClient(stop_reason="refusal"))
        self.assertIsNone(res)
        self.assertIn("refusal", notes[0])

    def test_missing_credentials_is_a_clear_error(self):
        def no_auth(**_):
            raise TypeError('"Could not resolve authentication method. Expected one of api_key ..."')
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=no_auth)))
        with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY"):
            llm.read_screens([("a", IMG)], self.m, SPECIES, llm.Claude(client=client))

    def test_validation_makes_no_network_calls(self):
        # Guardrails sends usage telemetry by default; the backend must switch it off
        lookups = []
        real = socket.getaddrinfo
        socket.getaddrinfo = lambda host, *a, **k: lookups.append(host) or real(host, *a, **k)
        try:
            self.read(FakeClient(*[reading(kind="storage", specimens=[{"name": "Notamon", "cp": 1}])] * 2))
        finally:
            socket.getaddrinfo = real
        self.assertEqual(lookups, [])

    def test_pipeline_reads_frames_in_order_and_caps_paid_frames(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [str(Path(d) / f"{i}.png") for i in range(2)]
            for p in paths:
                cv2.imwrite(p, IMG)
            with self.assertRaisesRegex(ValueError, "paid Claude request"):
                scan(paths, SPECIES, backend="claude", max_llm_frames=1)
        model = llm.Claude(client=FakeClient(reading(kind="bag", rare_candy=7), reading(kind="storage", specimens=[])))
        model.workers = 1  # so the fake client's queue order is the frame order
        seen = []
        results = llm.read_screens([("a", IMG), ("b", IMG)], self.m, SPECIES, model,
                                   progress=lambda i, n, label, kind: seen.append((label, kind)))
        self.assertEqual([r.kind for r in results], ["bag", "storage"])
        self.assertEqual(seen, [("a", "bag"), ("b", "storage")])

    def test_ollama_request_and_reading(self):
        fake = FakeOllama(reading(kind="bag", rare_candy=42).model_dump_json())
        res, notes = self.read(llm.Ollama(client=fake))
        self.assertEqual((res.rare_candy, notes), (42, []))
        call = fake.calls[0]
        self.assertEqual(call["model"], "qwen3-vl:4b")
        self.assertEqual(call["format"], llm.ScreenReading.model_json_schema())
        self.assertEqual([m["role"] for m in call["messages"]], ["system", "user"])
        self.assertEqual(call["messages"][1]["images"][0][:4], b"\x89PNG")

    def test_ollama_retry_and_unusable_reply(self):
        bad = reading(kind="storage", specimens=[{"name": "Dratini", "cp": 99999}]).model_dump_json()
        good = reading(kind="storage", specimens=[{"name": "Dratini", "cp": 999}]).model_dump_json()
        fake = FakeOllama(bad, good)
        res, _ = self.read(llm.Ollama(client=fake))
        self.assertEqual(res.specimens, [("DRATINI", 999)])
        self.assertEqual(fake.calls[1]["messages"][2], {"role": "assistant", "content": bad})
        res, notes = self.read(llm.Ollama(client=FakeOllama("not json")))
        self.assertIsNone(res)
        self.assertIn("doesn't fit the schema", notes[0])

    def test_ollama_setup_errors_are_clear(self):
        import ollama

        def down(**_):
            raise ConnectionError("Failed to connect to Ollama")

        def missing(**_):
            raise ollama.ResponseError("model not found", 404)

        for chat, message in ((down, "ollama serve"), (missing, "ollama pull qwen3-vl:4b")):
            model = llm.Ollama(client=SimpleNamespace(chat=chat))
            with self.assertRaisesRegex(ValueError, message):
                llm.read_screens([("a", IMG)], self.m, SPECIES, model)


if __name__ == "__main__":
    unittest.main()
