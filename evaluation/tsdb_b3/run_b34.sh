#!/usr/bin/env bash
# B3.4 CrateDB stage (PROTOCOL.md, Amendment B3.4): every load in the frozen order; completed loads are skipped.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3c/bin/python}
export B3_RUN=b34
done_ok() { [ -f "evaluation/tsdb_b3/results/b34/load_crate-$1_$2_r$3.json" ] && grep -q '"correct": true' "evaluation/tsdb_b3/results/b34/load_crate-$1_$2_r$3.json" && { [ "$2$3" != D21 ] || grep -q '"analytics"' "evaluation/tsdb_b3/results/b34/load_crate-$1_$2_r$3.json"; }; }
run() { if done_ok "$@"; then echo "skip $*"; else echo "== $* $(date +%H:%M:%S)"; "$PY" evaluation/tsdb_b3/run_crate.py load "$@"; fi; }
for arm in array row; do
  for d in D2 D1; do for r in 1 2 3; do run "$arm" "$d" "$r"; done; done
done
run row-best D2 1
echo "== B3.4 done $(date +%H:%M:%S)"
