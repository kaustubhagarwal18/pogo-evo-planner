# Roadmap

## Phase 1: CLI MVP (done)

- [x] Game master parser (PokeMiners format) and sample species data
- [x] Planner: per-family DP + cross-family grouped knapsack, shared items, trade evolutions
- [x] Scanner: frame extraction, OCR backends (RapidOCR / Tesseract), screen classification
      and parsing for item bag, detail page, storage grid, Pokédex
- [x] Merge with voting, conflict and gap warnings
- [x] Tests: brute-force-checked planner, parser unit tests, end-to-end on synthetic screens
- [x] Packaging, Makefile, CI

## Phase 2: Calibrate on real captures (next)

The scanner has only seen synthetic screens. Collect real ones and build a regression set.

- [ ] Capture bag, storage, Pokédex and detail screens on 2–3 phones (different aspect
      ratios, light/dark backgrounds, at least one non-1080p device)
- [ ] Store them under `tests/real/` with hand-labelled ground truth JSON (blur trainer names)
- [ ] Measure per-field accuracy; tune OCR passes, thresholds (frame diff, Pokédex
      saturation, CP-to-name pairing) and the fuzzy-match cutoff
- [ ] Nicknamed Pokémon: fall back to the species line on the detail page, or sprite matching
- [ ] Read evolution items from the bag too (Metal Coat, Sinnoh Stone, lures…)
- [ ] Regional forms (Alolan, Galarian…) in names and the species table
- [ ] Non-English game languages (OCR language + localised species names)

## Phase 3: Better recommendations

- [ ] Rarity from data rather than hand tiers (spawn/biome info, event boosts with expiry)
- [ ] Value model options: Pokédex completion, raid/PvP usefulness (PvPoke data), XP with Lucky Egg
- [ ] Candy XL, shadow/purified, trade discounts
- [ ] "What-if" view: how many km or catches until the next evolution without Rare Candy

## Phase 4: An app people can use

- [ ] Web UI: upload screenshot + recording, review/edit the inventory table, see the plan
      (runs the same Python, or Pyodide in the browser so nothing leaves the device)
- [ ] Android app: MediaProjection capture while the player browses, ML Kit OCR on device,
      planner ported or run via Chaquopy. Passive reading only; never automate input.
- [ ] iOS: screenshot/recording import (no live capture possible)

## Non-goals

- Logging in to game servers, unofficial APIs, or automated tapping/swiping. These break the
  Terms of Service and put accounts at risk.
