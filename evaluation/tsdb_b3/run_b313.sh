#!/usr/bin/env bash
# B3.13 (PROTOCOL.md, Amendment B3.13): every system beside the released MorletDB 0.3.6-alpha.1 in one session,
# alternating, reads from a fresh client process for every system; QuestDB's pair adds MorletDB with chunk summaries.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.6-alpha.1 B3_READS_FRESH_PROCESS=1
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json" ; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$2" != D2r1 ] || grep -q '"analytics' "$f"; }; }
# step <result name> <D> <r> <command...>
step() { local name=$1 d=$2 r=$3; shift 3
  if done_ok "$name" "$d$r"; then echo "skip $B3_RUN $name"; return; fi
  echo "== $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; "$@" || echo "FAILED $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; }
clean() { docker rm -f b3-questdb b3-tdengine b3-timescale b3-iotdb b3-crate b3-morletdb >/dev/null 2>&1 || true; }
morlet() { step "load_$1_$2_r$3" "$2" "$3" "$V/.venv-b3/bin/python" run.py load "$1" "$2" "$3"; clean; }
pair() { # pair <run> <system name prefix> <arm or ""> <python> <runner>
  export B3_RUN=$1
  for r in 1 2 3; do for d in D2 D1; do
    morlet morletdb-i1 "$d" "$r"
    if [ -n "$3" ]; then
      step "load_$2-$3_${d}_r$r" "$d" "$r" "$4" "$5" load "$3" "$d" "$r"
    else
      step "load_$2_${d}_r$r" "$d" "$r" "$4" "$5" load "$d" "$r"
    fi
    clean
  done; done
}
# QuestDB first, with the chunk-summaries arm in its rotation
export B3_RUN=b313-questdb
for r in 1 2 3; do for d in D2 D1; do
  morlet morletdb-i1 "$d" "$r"
  step "load_questdb_${d}_r$r" "$d" "$r" "$V/.venv-b3/bin/python" run.py load questdb "$d" "$r"; clean
  morlet morletdb-i1-sum "$d" "$r"
done; done
pair b313-tdengine  tdengine  default "$V/.venv-b3t/bin/python" run_tdengine.py
pair b313-timescale timescale ""      "$V/.venv-b3/bin/python"  run_timescale.py
pair b313-iotdb     iotdb     table   "$V/.venv-b3i/bin/python" run_iotdb.py
pair b313-crate     crate     array   "$V/.venv-b3c/bin/python" run_crate.py
pair b313-crate-row crate     row     "$V/.venv-b3c/bin/python" run_crate.py
echo "== B3.13 done $(date +%H:%M:%S)"
