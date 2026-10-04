# What to capture

Two inputs. Both can come from any phone's built-in screenshot and screen-recording tools.

## 1. Item bag screenshot

Open the item bag, scroll until **Rare Candy** and its count are visible, take a screenshot.

## 2. One screen recording

Record these in one go (or several recordings; pass them all to `pogo-evo-planner scan`):

1. **Storage grid.** Open your Pokémon list and scroll slowly from top to bottom.
   Tip: use the in-game search to filter first (e.g. to the families you care about),
   so a few hundred Pokémon becomes a few dozen.
2. **Pokédex.** Scroll through it once. Optional; it only adds a bonus for new entries.
3. **One detail page per family.** Tap any Pokémon of each family you're considering
   and pause about a second on the page, then swipe to the next. The candy count
   (`123` above `DRATINI CANDY`) only appears here.

Pause briefly on each screen: the scanner keeps the sharpest frame of each pause.

## Then

```bash
pogo-evo-planner scan bag.png recording.mp4 -o inventory.json
```

Read the warnings it prints (missing families, conflicting readings), fix anything wrong
in `inventory.json`, and add evolution items you hold under `"items"`, e.g.
`{"ITEM_METAL_COAT": 2, "ITEM_SINNOH_STONE": 1}`.

## Privacy

Recordings can show your trainer name, friends and location. Everything runs locally;
`.gitignore` keeps `captures/`, `mock/` and `inventory.json` out of the repo. Crop or
blur before sharing samples.
