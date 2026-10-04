# rarecandy

Tells a Pokémon GO player which evolutions to do and where their Rare Candy saves the
most effort, from screenshots and a screen recording of their own game.

```
screenshot of item bag ─┐
                        ├─► scan (OCR) ─► inventory.json ─► plan ─► recommendations
screen recording ───────┘                 (review/edit)
```

It never logs in to or talks to the game servers, and never taps or swipes in the game.
The player moves through their own screens; this only reads them. That is the same line
long-running scanner apps stay behind, and it keeps accounts safe from Terms of Service bans.

## Quick start

```bash
make install-ocr        # pip install -e '.[ocr,dev]'  (RapidOCR + RapidFuzz)
make gamemaster         # latest datamined game data -> data/latest.json

rarecandy scan bag.png recording.mp4 -o inventory.json --species data/latest.json
# review inventory.json, add evolution items under "items", then:
rarecandy plan inventory.json --species data/latest.json
```

No RapidOCR? `make install` uses Tesseract (`apt install tesseract-ocr` /
`brew install tesseract`) and Python's difflib instead: slower, less accurate.

Try it without the game: `make demo` (planner on an example inventory) or `make mock`
(renders synthetic screens and a recording, scans them, plans).

See [docs/CAPTURING.md](docs/CAPTURING.md) for what to record.

## Example output

```
Rare candy: using 150 of 150  (value score 13,392)

== Spend rare candy on ==
Feebas family: 100 candy (30 owned + 70 rare) - saves ~1,400 km of buddy walking
   Feebas -> Milotic  [#5 88.9%]
      Milotic: walk 20 km as buddy first
Dratini family: 100 candy (60 owned + 40 rare) - saves ~200 km of buddy walking
   Dragonair -> Dragonite  [#2 93.3%]
...
== Trade instead of spending candy ==
   Haunter -> Gengar is free when traded (from Haunter #9)
```

## How it works

### Scanning (`rarecandy/scan/`)

| Step | Module | Notes |
|---|---|---|
| Frames | `frames.py` | Samples recordings at 3 fps, collapses pauses into one sharpest frame, keeps every scroll frame. |
| OCR | `ocr.py` | RapidOCR (PaddleOCR models on ONNX) by default; Tesseract fallback reads 3 passes (2 polarities + upscaled) and merges. Returns word boxes. |
| Screens | `screens.py` | Classifies each frame (bag / detail / Pokédex / storage) and parses it relative to labels the game always shows, not fixed pixel regions. Species names are fuzzy-matched. |
| Merge | `pipeline.py` | Votes across frames, de-duplicates storage cells by (species, CP), reports conflicts and gaps. |

What each screen provides:

- **Item bag** → Rare Candy count (ignores Rare Candy XL).
- **Pokémon detail page** → that family's candy (`60` above `DRATINI CANDY`). Neither the
  storage grid nor the Pokédex shows candy, so one detail page per family is needed.
- **Storage grid** → owned species and CP per cell. A cell whose CP is scrolled off is
  skipped; another frame will have it whole.
- **Pokédex** → caught vs. only-seen, by sprite colour above the dex number.

### Planning (`rarecandy/planner.py`)

- Cost per candy = buddy km × rarity multiplier (`data/rarity_tiers.json`, hand-tuned:
  spawn rarity isn't in the game data).
- An evolution is worth candy cost × cost per candy, plus a bonus for a new Pokédex entry.
- Per family, a DP over owned specimens and their evolution paths (multi-step, branched)
  gives (candy needed, items used, value) options. Across families, a grouped knapsack
  spends the Rare Candy and shared items (Metal Coat, Sinnoh Stone, lures) for maximum value.
- Partial top-ups are never suggested: Rare Candy only pays off when it completes an
  evolution. Trade evolutions are listed as "trade instead". Verified against brute force.

## Data

- `rarecandy/data/species_sample.json`: ~65 hand-entered species for tests and demos.
  **Not authoritative**; use `make gamemaster` for real data.
- `scripts/fetch_gamemaster.sh`: downloads PokeMiners' datamined `latest.json`;
  `gamemaster.py` parses it (candy cost, buddy km, items, lures, trade flags, conditions).

## Status

MVP. The planner is solid. The scanner is tested only on **synthetic** screens
(`tests/mockscreens.py`), which mimic the text layout but aren't real captures, so expect
calibration work on real phones. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Development

```bash
make test        # all tests incl. end-to-end OCR (~30 s)
make test-fast   # skips the OCR end-to-end test
make lint
```

CI (`.github/workflows/ci.yml`) runs the suite with the Tesseract backend on Python
3.10–3.13, plus a non-blocking RapidOCR job.

Not affiliated with Niantic, Scopely, Nintendo or The Pokémon Company.
