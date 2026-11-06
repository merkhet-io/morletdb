#!/usr/bin/env bash
# B3.16 (PROTOCOL.md, Amendment B3.16): B3.15 with the corrected harness. QuestDB pair with the released 0.3.7-alpha.1
# reading with compressed transfer (morletdb-i2) and with raw transfer (morletdb-i1), in one session. Keep the Mac
# awake on AC power, the lid open or closed without causing sleep. MORLETDB_NATIVE_LIB must point to the decoder from the release's macOS wheel.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
REL=ghcr.io/merkhet-io/morletdb:0.3.7-alpha.1
: "${MORLETDB_NATIVE_LIB:?set MORLETDB_NATIVE_LIB to libmorlet_ffi.dylib from the 0.3.7-alpha.1 macOS wheel}"
export B3_READS_FRESH_PROCESS=1 MORLETDB_NATIVE_LIB
# The frozen start condition, enforced here (attempt 1 started on a busy machine): wait up to B316_WAIT_MIN minutes
# (default 60) for a 1-minute load average below 3 and no process except Docker's VM above half a core, else refuse.
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
# Machine condition (attempt 2 slept on a closed lid on battery): AC power, and the lid open or closed with an external
# display so that closing it does not sleep (deviation before attempt 3), checked at the start and before every load;
# a violation stops the whole session.
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
ready=0; for _ in $(seq 1 $(( ${B316_WAIT_MIN:-60} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine with Docker in time ($(uptime))"; ps -Ao pcpu,comm -r | head -6 || true; exit 1; }
mkdir -p results/b316-questdb; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } > results/b316-questdb/b316.start
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json" ; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$2" != D2r1 ] || grep -q '"analytics' "$f"; }; }
step() { local name=$1 d=$2 r=$3; shift 3
  if done_ok "$name" "$d$r"; then echo "skip $B3_RUN $name"; return; fi
  powered || { echo "STOPPED: not on AC power with the lid open before $B3_RUN $name $(date +%H:%M:%S)"; exit 1; }
  echo "== $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; "$@" || echo "FAILED $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; }
clean() { docker rm -f b3-questdb b3-morletdb >/dev/null 2>&1 || true; }
morlet() { # morlet <run dir> <image> <arm> <D> <r>
  export B3_RUN=$1 B3_MORLET_IMAGE=$2
  step "load_$3_$4_r$5" "$4" "$5" "$V/.venv-b3/bin/python" run.py load "$3" "$4" "$5"; clean; }
for r in 1 2 3; do for d in D2 D1; do
  morlet b316-questdb "$REL" morletdb-i2 "$d" "$r"
  export B3_RUN=b316-questdb
  step "load_questdb_${d}_r$r" "$d" "$r" "$V/.venv-b3/bin/python" run.py load questdb "$d" "$r"; clean
  morlet b316-questdb "$REL" morletdb-i1 "$d" "$r"
done; done
echo "== B3.16 done $(date +%H:%M:%S)"
