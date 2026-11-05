#!/usr/bin/env bash
# B3.9 (PROTOCOL.md, Amendment B3.9): each system beside MorletDB in one session, alternating; then B3.8 again.
# Keep the Mac awake with the lid open: caffeinate -i does not prevent clamshell sleep (B3.4 deviation 1).
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json" ; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$2" != D2r1 ] || grep -q '"analytics' "$f"; }; }
# step <result name> <D> <r> <command...>
step() { local name=$1 d=$2 r=$3; shift 3
  if done_ok "$name" "$d$r"; then echo "skip $B3_RUN $name"; return; fi
  echo "== $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; "$@"; snap "$name"; }
pair() { # pair <run> <system name prefix> <arm or ""> <python> <runner>
  export B3_RUN=$1
  for r in 1 2 3; do for d in D2 D1; do
    step "load_morletdb-033_${d}_r$r" "$d" "$r" "$V/.venv-b3/bin/python" run.py load morletdb-033 "$d" "$r"
    docker rm -f b3-tdengine b3-timescale b3-iotdb b3-crate >/dev/null 2>&1 || true
    if [ -n "$3" ]; then
      step "load_$2-$3_${d}_r$r" "$d" "$r" "$4" "$5" load "$3" "$d" "$r"
    else
      step "load_$2_${d}_r$r" "$d" "$r" "$4" "$5" load "$d" "$r"
    fi
  done; done
}
pair b39-tdengine  tdengine  default "$V/.venv-b3t/bin/python" run_tdengine.py
pair b39-timescale timescale ""      "$V/.venv-b3/bin/python"  run_timescale.py
pair b39-iotdb     iotdb     table   "$V/.venv-b3i/bin/python" run_iotdb.py
pair b39-crate     crate     array   "$V/.venv-b3c/bin/python" run_crate.py
pair b39-crate-row crate     row     "$V/.venv-b3c/bin/python" run_crate.py
echo "== B3.9 done $(date +%H:%M:%S)"
B3_RUN=b38z ./run_b38.sh
echo "== B3.9 and B3.8z done $(date +%H:%M:%S)"
