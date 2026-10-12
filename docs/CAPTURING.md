# What to capture

## The quick way: `evolvenew`

1. In your Pokémon storage, search `evolvenew` and screen-record yourself scrolling slowly
   through the results. The game lists every Pokémon whose evolution would be a new Pokédex
   entry, including ones you can't afford yet. Screenshots work too: scroll less than a screen
   between them, so each row is whole and above the floating buttons in at least one. The tool
   compares what it read with the count under the POKÉMON tab ("(210)") and warns if rows were
   skipped.
2. Open the Pokédex, tap the filter button, choose **Seen**, and screenshot the list.
3. Screenshot your item bag showing Rare Candy.

Run `pogo-evo-planner evolvenew <files>`. It lists what you're missing and which families'
candy counts it needs. For those, screenshot one Pokémon's page per family (the candy count is
only on that page) and run it again with those screenshots added.

## The full scan

Two inputs. Both can come from any phone's built-in screenshot and screen-recording tools.

## 1. Item bag screenshot

Open the item bag, scroll until **Rare Candy** and its count are visible, take a screenshot.

## 2. One screen recording

Record these in one go (or several recordings; pass them all to `pogo-evo-planner scan`):

1. **Storage grid.** Open your Pokémon list and scroll slowly from top to bottom.
   Tip: use the in-game search to filter first (e.g. to the families you care about),
   so a few hundred Pokémon becomes a few dozen.
2. **Pokédex, filtered to Seen.** Open the Pokédex, tap the filter button, choose **Seen**,
   and screenshot the list from top to bottom (a few screenshots is usually enough). Every
   entry there is a species you've seen but never caught. Optional, but it's the most reliable
   source for never-caught species (the Evolve button covers the ones you've never seen).
3. **One detail page per family.** Tap any Pokémon of each family you're considering
   and pause about a second on the page, then swipe to the next. The candy count
   (`123` above `DRATINI CANDY`) only appears here.

Pause briefly on each screen: the scanner keeps the sharpest frame of each pause.

## Then

```bash
pogo-evo-planner scan bag.png recording.mp4 -o inventory.json
```

With an LLM reader (`--ocr llm`, or the paid `--ocr claude`), screenshots of the bag, storage
and detail pages are the better input: each frame is one model request, so a recording's
hundreds of frames are slow with Ollama and costly with Claude.

Read the warnings it prints (missing families, conflicting readings), fix anything wrong
in `inventory.json`, and add evolution items you hold under `"items"`, by the names the game
shows, e.g. `{"Metal Coat": 2, "Sinnoh Stone": 1}`. The game master's ids work too
(`ITEM_GEN4_EVOLUTION_STONE` is the Sinnoh Stone). The plan names any item no evolution uses,
so a misspelt one doesn't go unnoticed.

## Privacy

Recordings can show your trainer name, friends and location. Everything runs locally,
including `--ocr llm` (Ollama), except `--ocr claude`, which sends each frame to Anthropic's
API. `.gitignore` keeps `captures/`, `mock/` and `inventory.json` out of the repo. Crop or
blur before sharing samples.
