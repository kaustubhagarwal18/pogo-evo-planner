# What to capture

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
in `inventory.json`, and add evolution items you hold under `"items"`, e.g.
`{"ITEM_METAL_COAT": 2, "ITEM_SINNOH_STONE": 1}`.

## Privacy

Recordings can show your trainer name, friends and location. Everything runs locally,
including `--ocr llm` (Ollama), except `--ocr claude`, which sends each frame to Anthropic's
API. `.gitignore` keeps `captures/`, `mock/` and `inventory.json` out of the repo. Crop or
blur before sharing samples.
