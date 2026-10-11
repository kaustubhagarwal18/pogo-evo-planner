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
      was misread as a silhouette and is fixed (silhouettes are never near-black)
- [x] Evolve-button check rebuilt on 39 real buttons (`tests/real/evolve_buttons/`): the sprite is
      judged against its button whatever the button's colour (grey, pink and blue-grey buttons were
      "unknown" before); frames where the sprite hasn't loaded are skipped (they read as "caught",
      which is how Dragapult was wrongly marked caught); each species is decided by a majority of
      frames; hint text ("…to evolve") and MEGA EVOLVE buttons are ignored
- [x] Pokédex filtered to "Seen": every entry is never caught. Tried on real screenshots; the
      status bar and the "Caught: N" count are no longer read as dex numbers
- [x] Candy label whose name wraps onto its own line ("GREAVARD" above "CANDY") is read

Next from that recording:

- [ ] Unfiltered Pokédex: caught vs. only-seen from sprite colour has still never seen a real
      screen; the "Seen" filter covers never-caught, but not species never seen at all
- [ ] A species seen on only one frame is still decided by that frame; capture each detail page
      for a second or two
- [ ] Red candy cost on the Evolve row means "can't afford": use it to sanity-check candy counts
- [ ] CP-to-name pairing in the storage grid: names get the CP of a neighbouring cell
      (4 Zacian and 3 Zamazenta carrying other Pokémon's CPs, a Kubfu with Kleavor's CP)
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

### The `evolvenew` flow

- [x] `pogo-evo-planner evolvenew`: detail pages of the Pokémon the game's `evolvenew` search
      lists, plus a Rare Candy count, give the plan in plain words (evolve now, use Rare Candy,
      not yet, Mega only). Detail pages count as owned, one copy per CP; only evolutions into
      never-caught species are planned; `--candy` / `--item` fix or add counts. Tried on 7 real
      detail screenshots
- [x] Tried on a real 30 s recording of the `evolvenew` grid (210 Pokémon, 75 species, 54 Mega
      only), the Pokédex "Seen" screenshots and the bag: lists the 36 missing species and which
      families' candy counts it needs; forms of one Pokédex entry are listed once
- [ ] **Known bug:** the grid shows nicknames, not species. A Victreebel listed for Mega
      Victreebel and nicknamed "99%Bellsprou…" is read as a Bellsprout, so Weepinbell is reported
      missing though it's owned; a Houndoom whose name the floating buttons half-hide is read as
      "Houndour". Fixes to weigh: cross-check with a caught list from the Pokédex (an evolution
      that's already caught means the reading was wrong), skip names with digits or symbols, skip
      names in the strip behind the floating buttons
- [ ] Does swiping between detail pages stay inside the `evolvenew` results? Then one swipe-through
      recording could give the candy counts too
- [ ] Read evolution items (apples, stones) from the bag screenshot instead of `--item`

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
