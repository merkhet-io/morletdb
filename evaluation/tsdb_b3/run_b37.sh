#!/usr/bin/env bash
# B3.7 TimescaleDB stage (PROTOCOL.md, Amendment B3.7): every load in the frozen order; completed loads are skipped.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3/bin/python}
export B3_RUN=b37
R=evaluation/tsdb_b3/results/b37
done_ok() { [ -f "$R/load_timescale_$1_r$2.json" ] && grep -q '"correct": true' "$R/load_timescale_$1_r$2.json" && { [ "$1$2" != D21 ] || grep -q '"analytics_col"' "$R/load_timescale_$1_r$2.json"; }; }
run() { if done_ok "$@"; then echo "skip $*"; else echo "== $* $(date +%H:%M:%S)"; docker rm -f b3-timescale >/dev/null 2>&1 || true; "$PY" evaluation/tsdb_b3/run_timescale.py load "$@"; fi; }
for d in D2 D1; do for r in 1 2 3; do run "$d" "$r"; done; done
echo "== B3.7 done $(date +%H:%M:%S)"
