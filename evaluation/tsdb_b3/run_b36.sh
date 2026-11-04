#!/usr/bin/env bash
# B3.6 TDengine stage (PROTOCOL.md, Amendment B3.6): every load in the frozen order; completed loads are skipped.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3t/bin/python}
export B3_RUN=b36
R=evaluation/tsdb_b3/results/b36
done_ok() { [ -f "$R/load_tdengine-$1_$2_r$3.json" ] && grep -q '"correct": true' "$R/load_tdengine-$1_$2_r$3.json" && { [ "$2$3" != D21 ] || grep -q '"analytics"' "$R/load_tdengine-$1_$2_r$3.json"; }; }
run() { if done_ok "$@"; then echo "skip $*"; else echo "== $* $(date +%H:%M:%S)"; docker rm -f b3-tdengine >/dev/null 2>&1 || true; "$PY" evaluation/tsdb_b3/run_tdengine.py load "$@"; fi; }
for arm in default zstd tsz; do
  for d in D2 D1; do for r in 1 2 3; do run "$arm" "$d" "$r"; done; done
done
echo "== B3.6 done $(date +%H:%M:%S)"
