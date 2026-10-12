# Changelog

Each version's changes and known issues. Versions follow [Semantic Versioning](https://semver.org/).
Before 1.0, a new minor version may change commands or file formats.

## [Unreleased]

### Added

- A browser app (`web/`), published on GitHub Pages. Open an `inventory.json` or `evolvenew.json`,
  try the example, or type an inventory in. Then edit it and watch the plan and the dashboard
  update. It asks for the candy counts and evolution items the plan is missing. The planner's
  Python runs in the page through Pyodide, so the inventory never leaves the browser; the browser
  keeps it between visits.
- `make web` builds the browser app into `site/`; `make serve` also serves it locally.
- The `pages` workflow builds the site with the latest game master and publishes it: on pushes
  to `main`, every Monday, and by hand.

### Fixed

- A Sinnoh Stone listed as `ITEM_SINNOH_STONE`, as `docs/CAPTURING.md` showed, was never used. The
  game data's id is `ITEM_GEN4_EVOLUTION_STONE`, and the bundled sample data had the same wrong id,
  so the demo only worked with the sample species. Inventory files now take evolution items by the
  names players use ("Sinnoh Stone", "Tart Apple") as well as by id, as `--item` already did, so
  files written the old way work. The same item listed under two names is an error rather than a
  guess, and the text report and the dashboard name any item no evolution uses.
- The text report calls evolution items by the names players know: "needs Sinnoh Stone", not
  "needs Gen4 Evolution Stone".

### Known issues

- The browser app doesn't read screenshots or recordings yet. Make the inventory with `scan` or
  `evolvenew`, then open it in the page.
- The first visit downloads about 6 MB of Python from jsDelivr; later visits use the browser's
  cache.

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
