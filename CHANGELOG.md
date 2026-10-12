# Changelog

Each version's changes and known issues. Versions follow [Semantic Versioning](https://semver.org/).
Before 1.0, a new minor version may change commands or file formats.

## [Unreleased]

### Added

- A browser app (`web/`), published on GitHub Pages. It opens with an example inventory and its
  plan, and offers two ways to replace it: upload screenshots (the `evolvenew` way or your whole
  collection) or upload an `inventory.json` or `evolvenew.json`. You can also type an inventory
  in. Then edit it and watch the plan and the dashboard update. It asks for the candy
  counts, evolution items and Rare Candy the plan is missing. Everything runs in the page: the
  planner's and scanner's own Python through Pyodide, and tesseract.js reading the text with the
  `--ocr tesseract` reader's model. Screenshots and the inventory never leave the browser; the
  browser keeps the inventory between visits, and **Reset** removes it, with the settings, and
  starts again from the example.
- `scan()` takes `read_text`, words read elsewhere in place of OCR, and `ocr.py` has the pieces
  the browser shares with the Tesseract reader: `tesseract_passes`, `words_from_tsv` (with or
  without a header row) and `merge_passes`.
- `make web` builds the browser app into `site/`; `make serve` also serves it locally.
- The `pages` workflow builds the site with the latest game master and publishes it: on pushes
  to `main`, every Monday, and by hand.

### Changed

- The Python code is formatted with `ruff format` (`make format`), and `make lint` and CI check it.
- The browser app's Python methods are `@json_api` methods: one JSON object in, JSON out, and an
  `InputError` for a mistake the player can fix.
- Variables have descriptive names throughout the Python and the browser app (`specimen`, `family`,
  `species_id` rather than `s`, `f`, `sp`), with no change in behaviour.

### Fixed

- One readable line instead of a traceback when:
  - an output file can't be written (a dashboard or inventory path in a folder that doesn't exist);
  - Tesseract itself fails on a screen;
  - Ctrl-C stops a command, which now exits with code 130.

  `make web` also names a species file it can't read.
- In the browser app:
  - a screenshot that can't be read is marked failed with its reason, and the others are still read;
  - planning no longer stops for good after a failed plan call;
  - the scan's temporary files are removed even when merging the screenshots fails.
- A Sinnoh Stone listed as `ITEM_SINNOH_STONE`, as `docs/CAPTURING.md` showed, was never used. The
  game data's id is `ITEM_GEN4_EVOLUTION_STONE`, and the bundled sample data had the same wrong id,
  so the demo only worked with the sample species. Inventory files now take evolution items by the
  names players use ("Sinnoh Stone", "Tart Apple") as well as by id, as `--item` already did, so
  files written the old way work. The same item listed under two names is an error rather than a
  guess, and the text report and the dashboard name any item no evolution uses.
- The text report calls evolution items by the names players know: "needs Sinnoh Stone", not
  "needs Gen4 Evolution Stone".

### Known issues

- The browser app doesn't read recordings yet, and reads screenshots with Tesseract only, not
  RapidOCR. Use `scan` or `evolvenew` for recordings.
- The first visit downloads about 6 MB of Python from jsDelivr, and reading screenshots the first
  time about 17 MB more; later visits use the browser's cache. How long that takes depends on the
  CDN: one test took 65 s, mostly a slow jsDelivr edge serving NumPy.
- Reading new screenshots replaces the open inventory, edits included: download it first to keep
  it.

## [0.1.1] - Unreleased

### Fixed

- `plan`, `dashboard` and `scan --plan` no longer spend Rare Candy on a family whose candy count
  is missing. A missing count was read as 0, so the plan could spend Rare Candy on the whole cost
  of an evolution. Those families now get none, and the text report names them. `evolvenew`
  already worked this way.
- One-line errors instead of tracebacks for:
  - a missing or broken inventory, species table, rarity tiers or Pokémon CSV;
  - a missing screenshot or recording (a missing recording used to crash).
- `evolvenew` takes `--llm-model` and `--llm-max-frames`, like `scan`. With `--ocr claude`, its
  error said to raise `--llm-max-frames`, which it didn't have.

### Changed

- Tests also run on Python 3.11. They run on pull requests (including from forks) and pushes to
  `main`, not on every branch push.
- Linter versions are pinned, so `make lint` runs the same versions as CI.

## [0.1.0] - 2026-10-11

The first release. Run it from a clone of this repository, as the README's quick start shows. It
isn't published on PyPI.

### Added

- `pogo-evo-planner evolvenew`, the quick way in. It reads three things:
  - a screen recording or screenshots of the game's `evolvenew` search;
  - screenshots of the Pokédex filtered to "Seen";
  - your item bag.

  It lists the species you're missing. It then asks for the candy counts and evolution items it
  needs, and plans where your Rare Candy goes. It also checks that it read every Pokémon the search
  found.
- `pogo-evo-planner scan` reads screenshots and recordings of the item bag, storage, Pokédex and
  Pokémon pages into an `inventory.json` you can review and edit.
- `pogo-evo-planner plan` plans evolutions and Rare Candy use from an inventory:
  - Only evolutions into species you don't own count.
  - Rare Candy is only spent where it completes one of those evolutions.
  - Families are valued by buddy distance and rarity.
  - The choice is checked against brute force in the tests.
- `pogo-evo-planner dashboard` shows the plan as one HTML page with Pokémon pictures, best use of
  Rare Candy first.
- Screen readers: RapidOCR (recommended) or Tesseract. Optionally, a vision LLM reads the screens
  instead: Ollama (local, free) or Claude (paid). Guardrails AI checks every LLM reading.
- Clear error messages instead of a crash when a file can't be read, no OCR engine is installed,
  or the packages for reading screens are missing.

### Known issues

- **Nicknamed Pokémon:** OCR reads the name under each Pokémon, not its picture. A nickname that
  looks like another species is read as that species. For example, a renamed Doublade was read
  as a Honedge, which hid that Aegislash is missing.
- **Pokémon with several evolutions:** `evolvenew` treats every branch as new. It can suggest
  Charcadet → Armarouge even when you own an Armarouge.
- **Phones and languages:** the scanner has only been tuned on one phone (576×1296 screen) in
  English. Other screen sizes and languages may read worse.
- **LLM readers:** the Ollama and Claude readers haven't been tried on real screenshots yet.
- **Game data:** commands look for `data/latest.json` in the folder you run them from. Anywhere
  else, they fall back to a sample of about 65 species and silently drop every other species.

[0.1.1]: https://github.com/kaustubhagarwal18/pogo-evo-planner/releases/tag/v0.1.1
[0.1.0]: https://github.com/kaustubhagarwal18/pogo-evo-planner/releases/tag/v0.1.0
