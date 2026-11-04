#!/usr/bin/env bash
# B3.8 (PROTOCOL.md, Amendment B3.8): QuestDB and the current MorletDB in one session, alternating.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3/bin/python}
export B3_RUN=b38 B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1
R=results/b38
done_ok() { [ -f "$R/load_$1_$2_r$3.json" ] && grep -q '"correct": true' "$R/load_$1_$2_r$3.json"; }
run() { if done_ok "$@"; then echo "skip $*"; else echo "== $* $(date +%H:%M:%S)"; "$PY" run.py load "$@"; fi; }
for r in 1 2 3; do
  for d in D2 D1; do
    run morletdb-033 "$d" "$r"
    run questdb "$d" "$r"
  done
done
echo "== B3.8 done $(date +%H:%M:%S)"
