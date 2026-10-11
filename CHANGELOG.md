# Changelog

Each version's changes and known issues. Versions follow [Semantic Versioning](https://semver.org/).
Before 1.0, a new minor version may change commands or file formats.

## [0.1.0] - Unreleased

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

[0.1.0]: https://github.com/kaustubhagarwal18/pogo-evo-planner/releases/tag/v0.1.0
