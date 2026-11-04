#!/usr/bin/env bash
# B3.5 Apache IoTDB stage (PROTOCOL.md, Amendment B3.5): every load in the frozen order; completed loads are skipped.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3i/bin/python}
export B3_RUN=b35
R=evaluation/tsdb_b3/results/b35
done_ok() { [ -f "$R/load_iotdb-$1_$2_r$3.json" ] && grep -q '"correct": true' "$R/load_iotdb-$1_$2_r$3.json" && { [ "$2$3" != D21 ] || grep -q '"analytics"' "$R/load_iotdb-$1_$2_r$3.json"; }; }
run() { if done_ok "$@"; then echo "skip $*"; else echo "== $* $(date +%H:%M:%S)"; "$PY" evaluation/tsdb_b3/run_iotdb.py load "$@"; fi; }
for arm in table table-zstd; do
  for d in D2 D1; do for r in 1 2 3; do run "$arm" "$d" "$r"; done; done
done
echo "== B3.5 done $(date +%H:%M:%S)"
