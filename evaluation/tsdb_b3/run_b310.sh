#!/usr/bin/env bash
# B3.10 (PROTOCOL.md, Amendment B3.10): single-connection ingestion after I1 (#233), beside QuestDB and the
# released MorletDB, in one session. For r = 1..3 and D2 then D1: morletdb-i1, questdb, morletdb-033.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3/bin/python}
export B3_RUN=${B3_RUN:-b310}
I1_IMAGE=${I1_IMAGE:-morletdb:b310}
REL_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1
R=results/$B3_RUN
mkdir -p "$R/machine"
snap() { { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -8 || true; } >> "$R/machine/$1_$2_r$3.txt"; }
done_ok() { [ -f "$R/load_$1_$2_r$3.json" ] && grep -q '"correct": true' "$R/load_$1_$2_r$3.json"; }
image_for() { case "$1" in morletdb-i1) echo "$I1_IMAGE" ;; *) echo "$REL_IMAGE" ;; esac; }
run() {
  if done_ok "$@"; then echo "skip $*"; return; fi
  echo "== $* $(date +%H:%M:%S)"
  snap "$@"
  B3_MORLET_IMAGE=$(image_for "$1") "$PY" run.py load "$@"
  snap "$@"
}
for r in 1 2 3; do
  for d in D2 D1; do
    run morletdb-i1 "$d" "$r"
    run questdb "$d" "$r"
    run morletdb-033 "$d" "$r"
  done
done
echo "== B3.10 done $(date +%H:%M:%S)"
