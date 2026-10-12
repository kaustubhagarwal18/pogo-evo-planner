"""Read game screens with a vision LLM instead of OCR, checked with Guardrails AI.

Two models are supported:

- `--ocr ollama` (also `--ocr llm`, the default LLM): an open-weight model run locally by Ollama
  (https://ollama.com). Free, and nothing leaves your machine, but small models read less
  accurately and are slow on a laptop.
- `--ocr claude`: Claude through the Anthropic API. The most capable, but paid per frame, and the
  screenshots go to Anthropic. Needs ANTHROPIC_API_KEY (or a profile from `ant auth login`).

Either way the model returns a structured reading of the screen (bag, Pokémon detail page, storage
grid or Pokédex). Names are matched to the species table the same way OCR text is, and Guardrails
AI checks every value (known species, plausible CP and candy counts, a bag screen that actually has
a Rare Candy count). A value that fails is dropped with a note, and the model gets one retry with
the list of failures. Guardrails' own usage telemetry is switched off: validation runs locally.

Needs the `llm` extras: `pip install -e '.[llm]'`.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

import cv2
import numpy as np
from guardrails import Guard, OnFailAction
from guardrails.settings import settings as _guardrails_settings
from guardrails.validators import FailResult, PassResult, Validator, register_validator
from pydantic import BaseModel, ValidationError

from .screens import ScreenResult, SpeciesMatcher

CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_EFFORT = "medium"  # reading a screen isn't hard reasoning; raise it if readings come back wrong
OLLAMA_MODEL = "qwen3-vl:4b"  # 3.3 GB download, fits an 8 GB Mac; qwen3-vl:2b is 1.9 GB, qwen3-vl:8b 6.1 GB
REASKS = 1  # retries when Guardrails rejects part of a reading


def _guardrails_offline() -> None:
    """Turn off Guardrails' usage telemetry, which is on by default and posts traces to its servers."""
    _guardrails_settings.rc.enable_metrics = False
    _guardrails_settings.rc.use_remote_inferencing = False


_guardrails_offline()  # before any validator is built: each reads this setting when constructed
os.environ.setdefault("GUARDRAILS_RUN_SYNC", "true")  # validate in this thread; no event loop needed


# ---------- what Claude returns (the structured-output schema) ----------


class Specimen(BaseModel):
    name: str  # species name as printed in the cell
    cp: int | None  # null when the CP is cut off or unreadable


class DexEntry(BaseModel):
    number: int  # national dex number shown under the sprite
    caught: bool  # colour sprite = caught; dark silhouette = only seen


class ScreenReading(BaseModel):
    kind: Literal["bag", "detail", "storage", "pokedex", "other"]
    rare_candy: int | None  # bag: Rare Candy count (not Rare Candy XL)
    species: str | None  # detail: the Pokémon's species
    candy_name: str | None  # detail: the word before "CANDY", e.g. DRATINI
    candy: int | None  # detail: that candy count (not Candy XL)
    evolve_sprite: Literal["colour", "silhouette", "none"]  # detail: sprite on the EVOLVE button
    specimens: list[Specimen]  # storage: one per grid cell
    pokedex: list[DexEntry]  # pokedex: one per entry


SYSTEM = """You read screenshots of the Pokémon GO mobile game and report what they show.

Classify the screen as one of:
- bag: the item bag. Report the Rare Candy count. Ignore Rare Candy XL.
- detail: one Pokémon's page. Report its species (not a nickname: if a nickname is shown, identify \
the species from the sprite), the candy name before the word CANDY and the number above it. Ignore \
Candy XL. Report the sprite on the EVOLVE button: "colour" if it is a coloured Pokémon, "silhouette" \
if it is a dark single-colour shape, "none" if there is no EVOLVE button.
- storage: the grid of Pokémon. Report every cell's name and CP. Use null for a CP that is cut off.
- pokedex: the Pokédex grid. Report every entry's number and whether its sprite is in colour (caught).
- other: anything else.

Copy names and numbers exactly as shown. Never guess a number: use null when it can't be read. \
Leave fields that don't apply to the screen as null or empty lists, and evolve_sprite as "none"."""


# ---------- checks (Guardrails validators) ----------
# Validator's _inference_local/_inference_remote hooks are for model-based validators; these are plain rules.
# pylint: disable=abstract-method


@register_validator(name="pogo/known-species", data_type="string")
class KnownSpecies(Validator):
    """The name matches a species in the game data (after the same fuzzy matching OCR text gets)."""

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
        """Pass when the name, after fuzzy matching, is a species in the game data."""
        if value is None or metadata["matcher"].match(value):
            return PassResult()
        return FailResult(error_message=f"{value!r} is not a known species")


@register_validator(name="pogo/known-dex-number", data_type="integer")
class KnownDexNumber(Validator):
    """The Pokédex number belongs to a species in the game data."""

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
        """Pass when the number belongs to a species in the game data."""
        if value in metadata["matcher"].by_dex:
            return PassResult()
        return FailResult(error_message=f"#{value} is not a known Pokédex number")


@register_validator(name="pogo/in-range", data_type="integer")
class InRange(Validator):
    """A count or CP is within what the game allows; null (unreadable) passes."""

    def __init__(self, low: int, high: int, **kwargs):
        """Keep the allowed range, low to high inclusive."""
        super().__init__(low=low, high=high, **kwargs)
        self.low, self.high = low, high

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
        """Pass when the value is null (unreadable) or within the range."""
        if value is None or self.low <= value <= self.high:
            return PassResult()
        return FailResult(error_message=f"{value} is outside {self.low}-{self.high}")


@register_validator(name="pogo/screen-complete", data_type="object")
class ScreenComplete(Validator):
    """A bag or detail reading has the number that screen exists for."""

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
        """Fail a bag reading without Rare Candy, or a detail reading without a species."""
        if value.get("kind") == "bag" and value.get("rare_candy") is None:
            return FailResult(error_message="a bag screen with no Rare Candy count")
        if value.get("kind") == "detail" and not value.get("species"):
            return FailResult(error_message="a detail screen with no species")
        return PassResult()


# pylint: enable=abstract-method


def build_guard() -> Guard:
    """Guard for one reading. Every check only reports (no-op): Guardrails drops the whole output
    when a field-level fix or filter fails, so the caller removes just the failing values instead."""
    noop = OnFailAction.NOOP
    guard = Guard.for_pydantic(ScreenReading)
    guard.configure(allow_metrics_collection=False)
    _guardrails_offline()  # creating and configuring a Guard reloads settings from ~/.guardrailsrc
    guard.use(ScreenComplete(on_fail=noop))
    guard.use(InRange(0, 9_999, on_fail=noop), on="$.rare_candy")
    guard.use(InRange(0, 99_999, on_fail=noop), on="$.candy")
    guard.use(KnownSpecies(on_fail=noop), on="$.species")
    guard.use(KnownSpecies(on_fail=noop), on="$.candy_name")
    guard.use(KnownSpecies(on_fail=noop), on="$.specimens.name")
    guard.use(InRange(10, 10_000, on_fail=noop), on="$.specimens.cp")
    guard.use(KnownDexNumber(on_fail=noop), on="$.pokedex.number")
    return guard


def check(reading: ScreenReading, guard: Guard, matcher: SpeciesMatcher) -> list[tuple[str, str]]:
    """Run the Guardrails checks; return (JSON path, reason) for each failure."""
    outcome = guard.parse(reading.model_dump_json(), metadata={"matcher": matcher})
    return [
        (summary.property_path or "$", summary.failure_reason or "failed")
        for summary in outcome.validation_summaries or []
        if summary.validator_status == "fail"
    ]


def drop_failures(reading: ScreenReading, failures: list[tuple[str, str]]) -> tuple[ScreenReading, list[str]]:
    """Remove the values that failed: a list item goes entirely, a single field becomes null."""
    data = reading.model_dump()
    gone: dict[str, set[int]] = {"specimens": set(), "pokedex": set()}
    notes = []
    for path, reason in failures:
        parts = path.removeprefix("$.").split(".")
        if parts[0] in gone and len(parts) > 1 and parts[1].isdigit():
            gone[parts[0]].add(int(parts[1]))
        elif parts[0] in ("rare_candy", "species", "candy_name", "candy"):
            data[parts[0]] = None
        notes.append(f"Claude reading rejected at {path}: {reason}")
    for key, indexes in gone.items():
        data[key] = [item for index, item in enumerate(data[key]) if index not in indexes]
    return ScreenReading.model_validate(data), notes


# ---------- the models ----------


def _png(image: np.ndarray) -> bytes:
    """A frame as PNG bytes (lossless, so small text stays sharp)."""
    encoded, png = cv2.imencode(".png", image)
    if not encoded:
        raise ValueError("could not encode frame as PNG")
    return png.tobytes()


@dataclass
class Reply:
    reading: ScreenReading | None  # None when the model gave no usable reading
    turn: dict | None  # the model's message, sent back with the failures on a retry
    problem: str | None = None  # why there's no reading


class Claude:
    """Claude through the Anthropic API: paid per frame, and the frames leave your machine."""

    workers = 4

    def __init__(self, model: str = CLAUDE_MODEL, client: Any = None):
        """Use the given client, or one built from the Anthropic credentials in the environment."""
        import anthropic

        self.model = model
        self.client: Any = client if client is not None else anthropic.Anthropic()  # Any: tests pass a fake

    def start(self, image: np.ndarray) -> list[dict]:
        """The first request's messages: the frame and the instruction."""
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(_png(image)).decode(),
            },
        }
        return [{"role": "user", "content": [image_block, {"type": "text", "text": "Read this screen."}]}]

    def ask(self, messages: list[dict]) -> Reply:
        """One structured-output request; server-side fallback answers if the model declines."""
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            messages=messages,
            output_format=ScreenReading,
            output_config={"effort": CLAUDE_EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return Reply(None, None, f"Claude returned no reading (stop reason: {response.stop_reason})")
        # sent back as returned, minus the SDK's parsed copy of the JSON (append-only history)
        turn = {
            "role": "assistant",
            "content": [block.model_dump(exclude={"parsed_output"}, exclude_none=True) for block in response.content],
        }
        return Reply(response.parsed_output, turn)

    @contextmanager
    def errors(self) -> Iterator[None]:
        """Turn API setup failures into ValueErrors that say what to do."""
        import anthropic

        try:
            yield
        except anthropic.AuthenticationError as error:
            raise ValueError(f"the Claude API rejected the credentials: {error.message}") from error
        except anthropic.APIConnectionError as error:
            raise ValueError("can't reach the Claude API; check the network connection") from error
        except TypeError as error:
            if "authentication method" not in str(error):
                raise
            raise ValueError("no Claude API credentials: set ANTHROPIC_API_KEY or run `ant auth login`") from error


class Ollama:
    """An open-weight model run locally by Ollama: free, and the frames never leave your machine."""

    workers = 1  # Ollama answers one request at a time unless OLLAMA_NUM_PARALLEL is raised

    def __init__(self, model: str = OLLAMA_MODEL, client: Any = None):
        """Use the given client, or one for the local Ollama server (OLLAMA_HOST picks another)."""
        import ollama

        self.model = model
        self.client: Any = client if client is not None else ollama.Client()  # OLLAMA_HOST picks another server

    def start(self, image: np.ndarray) -> list[dict]:
        """The first request's messages: the instruction, then the frame."""
        return [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Read this screen.", "images": [_png(image)]},
        ]

    def ask(self, messages: list[dict]) -> Reply:
        """One request, constrained to the reading's JSON schema."""
        response = self.client.chat(
            model=self.model, messages=messages, format=ScreenReading.model_json_schema(), options={"temperature": 0}
        )
        text = response.message.content or ""
        try:
            reading = ScreenReading.model_validate_json(text)
        except ValidationError as error:
            return Reply(
                None,
                None,
                f"{self.model} returned a reading that doesn't fit the schema ({error.error_count()} errors)",
            )
        return Reply(reading, {"role": "assistant", "content": text})

    @contextmanager
    def errors(self) -> Iterator[None]:
        """Turn setup failures (Ollama not running, model not downloaded) into ValueErrors that say what to do."""
        import ollama

        try:
            yield
        except ConnectionError as error:
            raise ValueError(
                "can't reach Ollama: install it from https://ollama.com and start it "
                "(open the app, or run `ollama serve`)"
            ) from error
        except ollama.ResponseError as error:
            if error.status_code == 404:
                raise ValueError(f"the model {self.model} isn't downloaded: run `ollama pull {self.model}`") from error
            raise ValueError(f"Ollama error: {error.error}") from error


def make_model(backend: str, name: str | None = None) -> Claude | Ollama:
    """The model for an --ocr choice: "claude", or "ollama"/"llm" (the default LLM: local and free)."""
    if backend == "claude":
        return Claude(name or CLAUDE_MODEL)
    return Ollama(name or OLLAMA_MODEL)


def read_reading(
    image: np.ndarray, model: Claude | Ollama, guard: Guard, matcher: SpeciesMatcher, reasks: int = REASKS
) -> tuple[ScreenReading | None, list[str]]:
    """Ask the model to read one frame, check it, and retry with the failures; returns (reading, notes)."""
    messages = model.start(image)
    for attempt in range(reasks + 1):
        reply = model.ask(messages)
        reading = reply.reading
        if reading is None:
            return None, [reply.problem or "no reading"]
        failures = check(reading, guard, matcher)
        if not failures or attempt == reasks or reply.turn is None:
            return drop_failures(reading, failures)
        messages += [
            reply.turn,
            {
                "role": "user",
                "content": "These values failed validation:\n"
                + "\n".join(f"- {path}: {reason}" for path, reason in failures)
                + "\nLook at the screen again and return the full corrected reading. Use null for anything you "
                "can't read.",
            },
        ]
    raise AssertionError("unreachable: the last attempt always returns")


def to_screen_result(reading: ScreenReading, matcher: SpeciesMatcher, species: dict) -> ScreenResult:
    """Convert a checked reading into the ScreenResult the OCR parsers produce."""
    if reading.kind == "other":
        return ScreenResult("unknown")
    result = ScreenResult(reading.kind)
    if reading.kind == "bag":
        result.rare_candy = reading.rare_candy
    elif reading.kind == "detail":
        species_id = matcher.match(reading.species) if reading.species else None
        candy_species = matcher.match(reading.candy_name) if reading.candy_name else species_id
        if candy_species and reading.candy is not None:
            result.candy[species[candy_species]["family"]] = reading.candy
        evolutions = species.get(species_id, {}).get("evolutions", []) if species_id else []
        if len(evolutions) == 1 and reading.evolve_sprite != "none":  # branched lines show several buttons
            (result.dex_caught if reading.evolve_sprite == "colour" else result.dex_not_caught).add(evolutions[0]["to"])
    elif reading.kind == "storage":
        for specimen in reading.specimens:
            species_id = matcher.match(specimen.name)
            if species_id:
                result.specimens.append((species_id, specimen.cp))
    elif reading.kind == "pokedex":
        for entry in reading.pokedex:
            species_id = matcher.by_dex.get(entry.number)
            if species_id:
                (result.dex_caught if entry.caught else result.dex_seen_only).add(species_id)
    return result


def read_screens(
    frames: list[tuple[str, np.ndarray]],
    matcher: SpeciesMatcher,
    species: dict,
    model: Claude | Ollama | None = None,
    progress: Callable | None = None,
) -> list[ScreenResult]:
    """Read every frame with the model (default: the local Ollama one), in parallel where it allows, in order.

    Raises ValueError, with what to do, when the model can't be used (no credentials, Ollama not running...).
    """
    model = model or make_model("llm")

    def one(image: np.ndarray) -> ScreenResult:
        """Read one frame with the model and turn the checked reading into a screen result."""
        # a guard per frame, so threads don't share one
        reading, notes = read_reading(image, model, build_guard(), matcher)
        result = to_screen_result(reading, matcher, species) if reading else ScreenResult("unknown")
        result.notes.extend(notes)
        return result

    results = []
    with model.errors(), ThreadPoolExecutor(max_workers=model.workers) as pool:
        for index, ((label, _), result) in enumerate(zip(frames, pool.map(one, [image for _, image in frames]))):
            results.append(result)
            if progress:
                progress(index + 1, len(frames), label, result.kind)
    return results
