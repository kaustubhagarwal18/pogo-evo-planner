# Roadmap

## Phase 1: CLI MVP (done)

- [x] Game master parser (PokeMiners format) and sample species data
- [x] Planner: per-family DP + cross-family grouped knapsack, shared items, trade evolutions
- [x] Scanner: frame extraction, OCR backends (RapidOCR / Tesseract), screen classification
      and parsing for item bag, detail page, storage grid, Pokédex
- [x] Merge with voting, conflict and gap warnings
- [x] Tests: brute-force-checked planner, parser unit tests, end-to-end on synthetic screens
- [x] Packaging, Makefile, CI
- [x] Linters and secret scanning in CI and `make lint`: ruff, pylint, mypy, bandit, codespell,
      gitleaks over the full git history
- [x] Dashboard: Pokémon sprites and artwork (PokeAPI), a "New" tag on evolutions that add a
      Pokédex entry, an inventory filter
- [ ] README version badges are typed by hand; update them with dependency upgrades

## Phase 2: Calibrate on real captures (next)

The scanner's tests use synthetic screens. Collect real ones and build a regression set.

Done from the first real recording (576×1296 phone screen, Paldea-heavy storage):

- [x] Match species against the full game master; the bundled sample silently dropped all of Paldea
- [x] Candy XL label wrapped onto two lines was read as regular candy
- [x] Evolve-button check on detail pages: the evolution's sprite is a dark silhouette if it was
      never caught, in colour if it was. A silhouette overrides a storage reading of that species.
      Checked on every detail frame of the recording: the 4 never-caught results (Espathra,
      Garganacl, Glimmora, Toedscruel) are real silhouettes. A dark-bodied sprite (Kilowattrel)
      was misread as a silhouette and is fixed (silhouettes are never near-black). A species
      counts as caught if any frame shows it in colour, which outvotes single-frame misreads

Next from that recording:

- [ ] **Pokédex recording with the silhouette test (next iteration).** Record a scroll through the
      Pokédex and read caught vs. never-caught for every species at once, with the same
      sprite-vs-background test the Evolve button uses. That gives a full "never caught" list
      without opening one detail page per family, and cross-checks every storage reading.
      `parse_pokedex` already guesses this from sprite saturation, but it has never seen a real
      Pokédex screen.
- [ ] Grey Evolve button (shown when the evolution is unaffordable): calibrate the silhouette
      test for it; it's reported as unknown for now
- [ ] Evolve-button misreads still possible on a single frame: a mostly green Pokémon on the green
      button (Floragato read as a silhouette once), and frames caught mid-swipe while the sprite
      is still fading in (Pawmo). Both were outvoted by other frames here; a green species seen on
      only one frame could still slip through. Ideas: skip frames where the sprite is unusually
      small, and require two frames to agree on "never caught"
- [ ] The opposite misread: Dragapult was read as **caught** (colour on the Evolve button) although
      the player never caught one; it had to be removed from the Pokédex list by hand. Because one
      colour frame outvotes any number of silhouettes, a false "caught" can't be outvoted. Weigh
      the votes instead of letting colour always win
- [ ] Red candy cost on the Evolve row means "can't afford": use it to sanity-check candy counts
- [ ] CP-to-name pairing in the storage grid: names get the CP of a neighbouring cell
      (4 Zacian and 3 Zamazenta carrying other Pokémon's CPs, a Kubfu with Kleavor's CP)
- [ ] Some detail pages read no candy count (Greavard); find out why
- [ ] End-to-end synthetic test fails with RapidOCR on macOS (RapidOCR 3.9, PP-OCRv6 models:
      Feebas and Chansey candy unread). It passes with Tesseract, and in CI's RapidOCR job on
      Linux. Already failing at the initial commit
- [ ] Scan speed: cache OCR per frame and merge batch recordings into one inventory, drop scroll
      frames that mostly overlap, collapse detail pages whose 3D model keeps animating

- [ ] Capture bag, storage, Pokédex and detail screens on 2–3 phones (different aspect
      ratios, light/dark backgrounds, at least one non-1080p device)
- [ ] Store them under `tests/real/` with hand-labelled ground truth JSON (blur trainer names)
- [ ] Measure per-field accuracy; tune OCR passes, thresholds (frame diff, Pokédex
      saturation, CP-to-name pairing) and the fuzzy-match cutoff
- [ ] Nicknamed Pokémon: fall back to the species line on the detail page, or sprite matching.
      The LLM readers are asked to identify the species from the sprite (untested)
- [ ] Read evolution items from the bag too (Metal Coat, Sinnoh Stone, lures…)
- [ ] Regional forms (Alolan, Galarian…) in names and the species table
- [ ] Non-English game languages (OCR language + localised species names)

### LLM screen readers

- [x] `--ocr llm` / `ollama` (local, free; default LLM) and `--ocr claude` (paid API) read frames
      as images and return a structured reading; Guardrails AI checks every value, with one retry
      and its usage telemetry switched off. Tests use fake clients
- [ ] First real run: install Ollama on an 8 GB Mac and measure `qwen3-vl:4b` against OCR for
      accuracy and seconds per frame
- [ ] Measure both readers and OCR per field on the hand-labelled captures above, and the real
      cost per frame for Claude, before recommending either
- [ ] Hybrid: OCR first, an LLM only for frames OCR is unsure about (Evolve-button sprite,
      conflicting CPs, missing candy counts), to cut time and cost
- [ ] Claude Batch API for long recordings (half price, results arrive later)

## Phase 3: Better recommendations

- [ ] Rarity from data rather than hand tiers (spawn/biome info, event boosts with expiry)
- [x] Collection-first value: only evolutions into a species you don't have count (owning a later
      stage covers earlier ones), the cheapest owned stage is evolved, and Rare Candy never pays
      for a repeat or gets spent just to use it up
- [x] One Pokémon planned per species (IVs ignored), so a later middle stage is never dropped;
      branching lines (Eevee, Kirlia, Charcadet) keep one copy per final evolution not owned yet
- [ ] Known limits of that rule: with `--repeat-value` above 0, extra copies of a species are no
      longer considered for repeat evolutions; a family with many unowned branches (8+ Eevees
      and lures, no Eeveelutions) takes about 6 s to plan
- [ ] Value model options: raid/PvP usefulness (PvPoke data), XP with Lucky Egg
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
