#!/usr/bin/env bash
# B3.14 (PROTOCOL.md, Amendment B3.14): QuestDB pair with a build of main (parallel decode) and the released image
# in the same session. Keep the Mac awake with the lid open on AC power (caffeinate does not prevent clamshell sleep).
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
BUILD=morletdb:b314
REL=ghcr.io/merkhet-io/morletdb:0.3.6-alpha.1
export B3_READS_FRESH_PROCESS=1
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json" ; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$2" != D2r1 ] || grep -q '"analytics' "$f"; }; }
step() { local name=$1 d=$2 r=$3; shift 3
  if done_ok "$name" "$d$r"; then echo "skip $B3_RUN $name"; return; fi
  echo "== $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; "$@" || echo "FAILED $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; }
clean() { docker rm -f b3-questdb b3-morletdb >/dev/null 2>&1 || true; }
morlet() { # morlet <run dir> <image> <arm> <D> <r>
  export B3_RUN=$1 B3_MORLET_IMAGE=$2
  step "load_$3_$4_r$5" "$4" "$5" "$V/.venv-b3/bin/python" run.py load "$3" "$4" "$5"; clean; }
for r in 1 2 3; do for d in D2 D1; do
  morlet b314-questdb "$BUILD" morletdb-i1 "$d" "$r"
  export B3_RUN=b314-questdb
  step "load_questdb_${d}_r$r" "$d" "$r" "$V/.venv-b3/bin/python" run.py load questdb "$d" "$r"; clean
  morlet b314-questdb "$BUILD" morletdb-i1-sum "$d" "$r"
  morlet b314-released "$REL" morletdb-i1 "$d" "$r"
done; done
echo "== B3.14 done $(date +%H:%M:%S)"
