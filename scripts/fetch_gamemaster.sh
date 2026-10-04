#!/usr/bin/env bash
# Download the latest datamined Pokémon GO game master (PokeMiners) to data/latest.json.
# Use it with:  pogo-evo-planner plan inventory.json --species data/latest.json
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
url="https://raw.githubusercontent.com/PokeMiners/game_masters/master/latest/latest.json"
curl -fsSL --retry 3 -o data/latest.json.tmp "$url"
python3 - <<'EOF'
import json
from pogo_evo_planner.gamemaster import load_species
sp = load_species("data/latest.json.tmp")
assert len(sp) > 500, f"only {len(sp)} species parsed - format changed?"
print(f"parsed {len(sp)} species/forms")
EOF
mv data/latest.json.tmp data/latest.json
echo "saved data/latest.json"
