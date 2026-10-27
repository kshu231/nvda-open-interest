#!/bin/bash
# Gather the generated pages into _site/ for GitHub Pages: each symbol's dashboard,
# history and gamma exposure snapshot, with the first symbol's dashboard as the home
# page. The pages link to each other by file name, so each goes up under its own.
# A symbol with no pages yet (nothing saved for it) is left out.
set -euo pipefail
cd "$(dirname "$0")"
symbols=$(python3 -c 'import nvda_oi; print(*nvda_oi.SYMBOLS)')
mkdir -p _site
for symbol in $symbols; do
  for file in "dashboard_$symbol.html" "history_$symbol.html" "gex_$symbol.json"; do
    if [ -f "$file" ]; then cp "$file" _site/; fi
  done
done
cp "dashboard_${symbols%% *}.html" _site/index.html
ls -l _site
