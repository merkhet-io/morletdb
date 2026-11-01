#!/usr/bin/env bash
# The report PDFs are built in research/paper/ (canonical) and copied to docs/ for the website.
#   research/paper/sync_site.sh           # copy
#   research/paper/sync_site.sh --check   # fail unless the copies are byte-identical (CI)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
status=0
for pair in "research/paper/morletdb.pdf:docs/morletdb.pdf" "research/paper/supplement.pdf:docs/morletdb-supplement.pdf"; do
  src=${pair%%:*}; dst=${pair#*:}
  if [ "${1:-}" = "--check" ]; then
    if cmp -s "$src" "$dst"; then echo "ok    $dst = $src"; else echo "STALE $dst differs from $src"; status=1; fi
  else
    cp "$src" "$dst" && echo "copied $src -> $dst"
  fi
done
exit $status
