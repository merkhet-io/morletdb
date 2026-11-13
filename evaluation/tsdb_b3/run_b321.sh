#!/usr/bin/env bash
# B3.21 (PROTOCOL.md, Amendment B3.21): GreptimeDB beside the released MorletDB 0.3.12-alpha.1
# in one session, alternating, reads from a fresh client process; the start and power conditions of B3.16.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.12-alpha.1 B3_READS_FRESH_PROCESS=1
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
ready=0; for _ in $(seq 1 $(( ${B321_WAIT_MIN:-180} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine in time ($(uptime))"; exit 1; }
mkdir -p results/b321-greptimedb; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; docker ps --format '{{.Names}}'; } > results/b321-greptimedb/b321.start
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json" ; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$2" != D2r1 ] || grep -q '"analytics' "$f"; }; }
step() { local name=$1 d=$2 r=$3; shift 3
  if done_ok "$name" "$d$r"; then echo "skip $B3_RUN $name"; return; fi
  powered || { echo "STOPPED: not on AC power before $B3_RUN $name"; exit 1; }
  h=$(date +%H); [ "$h" -ge 7 ] && [ "$h" -lt 20 ] && { echo "STOPPED: no load starts after 07:00 ($B3_RUN $name)"; exit 0; }
  echo "== $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; "$@" || echo "FAILED $B3_RUN $name $(date +%H:%M:%S)"; snap "$name"; }
clean() { docker rm -f b3-greptimedb b3-morletdb >/dev/null 2>&1 || true; }
pair() { # pair <run> <system> <runner>
  export B3_RUN=$1
  for r in 1 2 3; do for d in D2 D1; do
    step "load_morletdb-i1-sum_${d}_r$r" "$d" "$r" "$V/.venv-b3/bin/python" run.py load morletdb-i1-sum "$d" "$r"; clean
    step "load_$2_${d}_r$r" "$d" "$r" "$V/.venv-b3/bin/python" "$3" load "$d" "$r"; clean
  done; done
}
pair b321-greptimedb greptimedb run_greptime.py
echo "== B3.21 done $(date +%H:%M:%S)"
