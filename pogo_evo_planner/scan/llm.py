"""Read game screens with Claude (a vision LLM) instead of OCR, checked with Guardrails AI.

`--ocr claude` sends each frame to the Claude API and asks for a structured reading of the screen
(bag, Pokémon detail page, storage grid or Pokédex). Names are matched to the species table the
same way OCR text is, and Guardrails AI checks every value (known species, plausible CP and candy
counts, a bag screen that actually has a Rare Candy count). A value that fails is dropped with a
note, and Claude gets one retry with the list of failures.

Unlike OCR, this sends your screenshots to Anthropic's API and costs money per frame. Guardrails'
own usage telemetry is switched off: validation runs locally and sends nothing.

Needs the `llm` extras (`pip install -e '.[llm]'`) and Claude API credentials (ANTHROPIC_API_KEY,
or a profile from `ant auth login`).
"""
from __future__ import annotations

import base64
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

import cv2
import numpy as np
from guardrails import Guard, OnFailAction
from guardrails.settings import settings as _guardrails_settings
from guardrails.validators import FailResult, PassResult, Validator, register_validator
from pydantic import BaseModel

from .screens import ScreenResult, SpeciesMatcher

MODEL = "claude-opus-5-5"
EFFORT = "medium"      # reading a screen isn't hard reasoning; raise it if readings come back wrong
WORKERS = 4            # frames read in parallel
REASKS = 1             # retries when Guardrails rejects part of a reading


def _guardrails_offline() -> None:
    """Turn off Guardrails' usage telemetry, which is on by default and posts traces to its servers."""
    _guardrails_settings.rc.enable_metrics = False
    _guardrails_settings.rc.use_remote_inferencing = False


_guardrails_offline()  # before any validator is built: each reads this setting when constructed
os.environ.setdefault("GUARDRAILS_RUN_SYNC", "true")  # validate in this thread; no event loop needed


# ---------- what Claude returns (the structured-output schema) ----------

class Specimen(BaseModel):
    name: str              # species name as printed in the cell
    cp: int | None         # null when the CP is cut off or unreadable


class DexEntry(BaseModel):
    number: int            # national dex number shown under the sprite
    caught: bool           # colour sprite = caught; dark silhouette = only seen


class ScreenReading(BaseModel):
    kind: Literal["bag", "detail", "storage", "pokedex", "other"]
    rare_candy: int | None                                  # bag: Rare Candy count (not Rare Candy XL)
    species: str | None                                     # detail: the Pokémon's species
    candy_name: str | None                                  # detail: the word before "CANDY", e.g. DRATINI
    candy: int | None                                       # detail: that candy count (not Candy XL)
    evolve_sprite: Literal["colour", "silhouette", "none"]  # detail: sprite on the EVOLVE button
    specimens: list[Specimen]                               # storage: one per grid cell
    pokedex: list[DexEntry]                                 # pokedex: one per entry


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
        if value is None or metadata["matcher"].match(value):
            return PassResult()
        return FailResult(error_message=f"{value!r} is not a known species")


@register_validator(name="pogo/known-dex-number", data_type="integer")
class KnownDexNumber(Validator):
    """The Pokédex number belongs to a species in the game data."""

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
        if value in metadata["matcher"].by_dex:
            return PassResult()
        return FailResult(error_message=f"#{value} is not a known Pokédex number")


@register_validator(name="pogo/in-range", data_type="integer")
class InRange(Validator):
    """A count or CP is within what the game allows; null (unreadable) passes."""

    def __init__(self, low: int, high: int, **kwargs):
        super().__init__(low=low, high=high, **kwargs)
        self.low, self.high = low, high

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
        if value is None or self.low <= value <= self.high:
            return PassResult()
        return FailResult(error_message=f"{value} is outside {self.low}-{self.high}")


@register_validator(name="pogo/screen-complete", data_type="object")
class ScreenComplete(Validator):
    """A bag or detail reading has the number that screen exists for."""

    def _validate(self, value: Any, metadata: dict) -> PassResult | FailResult:
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
    return [(s.property_path or "$", s.failure_reason or "failed")
            for s in outcome.validation_summaries or [] if s.validator_status == "fail"]


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
    for key, idx in gone.items():
        data[key] = [item for i, item in enumerate(data[key]) if i not in idx]
    return ScreenReading.model_validate(data), notes


# ---------- calling Claude ----------

def _image_block(img: np.ndarray) -> dict:
    """A frame as a base64 PNG image block (lossless, so small text stays sharp)."""
    ok, png = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("could not encode frame as PNG")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(png.tobytes()).decode()}}


def _ask(client: Any, messages: list[dict]) -> Any:
    """One structured-output request; server-side fallback answers if the model declines."""
    return client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=messages,
        output_format=ScreenReading,
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )


def read_reading(img: np.ndarray, client: Any, guard: Guard, matcher: SpeciesMatcher,
                 reasks: int = REASKS) -> tuple[ScreenReading | None, list[str]]:
    """Ask Claude to read one frame, check it, and retry with the failures; returns (reading, notes)."""
    messages: list[dict] = [{"role": "user",
                             "content": [_image_block(img), {"type": "text", "text": "Read this screen."}]}]
    for attempt in range(reasks + 1):
        response = _ask(client, messages)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return None, [f"Claude returned no reading (stop reason: {response.stop_reason})"]
        reading = response.parsed_output
        failures = check(reading, guard, matcher)
        if not failures or attempt == reasks:
            break
        # append-only: the assistant turn goes back as returned (minus the SDK's parsed copy of the JSON),
        # then the failures as a new user turn
        messages += [
            {"role": "assistant",
             "content": [b.model_dump(exclude={"parsed_output"}, exclude_none=True) for b in response.content]},
            {"role": "user", "content": "These values failed validation:\n"
             + "\n".join(f"- {p}: {r}" for p, r in failures)
             + "\nLook at the screen again and return the full corrected reading. Use null for anything you "
               "can't read."},
        ]
    return drop_failures(reading, failures)


def to_screen_result(reading: ScreenReading, matcher: SpeciesMatcher, species: dict) -> ScreenResult:
    """Convert a checked reading into the ScreenResult the OCR parsers produce."""
    if reading.kind == "other":
        return ScreenResult("unknown")
    r = ScreenResult(reading.kind)
    if reading.kind == "bag":
        r.rare_candy = reading.rare_candy
    elif reading.kind == "detail":
        sp = matcher.match(reading.species) if reading.species else None
        candy_sp = matcher.match(reading.candy_name) if reading.candy_name else sp
        if candy_sp and reading.candy is not None:
            r.candy[species[candy_sp]["family"]] = reading.candy
        evos = species.get(sp, {}).get("evolutions", []) if sp else []
        if len(evos) == 1 and reading.evolve_sprite != "none":   # branched lines show several buttons
            (r.dex_caught if reading.evolve_sprite == "colour" else r.dex_not_caught).add(evos[0]["to"])
    elif reading.kind == "storage":
        for s in reading.specimens:
            sp = matcher.match(s.name)
            if sp:
                r.specimens.append((sp, s.cp))
    elif reading.kind == "pokedex":
        for e in reading.pokedex:
            sp = matcher.by_dex.get(e.number)
            if sp:
                (r.dex_caught if e.caught else r.dex_seen_only).add(sp)
    return r


def read_screens(frames: list[tuple[str, np.ndarray]], matcher: SpeciesMatcher, species: dict,
                 client: Any = None, progress: Callable | None = None) -> list[ScreenResult]:
    """Read every frame with Claude, in parallel, keeping frame order.

    Raises ValueError, with what to do, when the API can't be used (no credentials, rejected key, no network).
    """
    import anthropic
    if client is None:
        client = anthropic.Anthropic()

    def one(img: np.ndarray) -> ScreenResult:
        # a guard per frame, so threads don't share one
        reading, notes = read_reading(img, client, build_guard(), matcher)
        res = to_screen_result(reading, matcher, species) if reading else ScreenResult("unknown")
        res.notes.extend(notes)
        return res

    results = []
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for i, ((label, _), res) in enumerate(zip(frames, pool.map(one, [img for _, img in frames]))):
                results.append(res)
                if progress:
                    progress(i + 1, len(frames), label, res.kind)
    except anthropic.AuthenticationError as e:
        raise ValueError(f"the Claude API rejected the credentials: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ValueError("can't reach the Claude API; check the network connection") from e
    except TypeError as e:
        if "authentication method" not in str(e):
            raise
        raise ValueError("no Claude API credentials: set ANTHROPIC_API_KEY or run `ant auth login`") from e
    return results
