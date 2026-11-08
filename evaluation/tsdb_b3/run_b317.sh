#!/usr/bin/env bash
# B3.17 (PROTOCOL.md, Amendment B3.17): B3.2's parallel-writer track with the released 0.3.9-alpha.1 and B3.10's
# client path, against QuestDB 10.1.0, in one session. Same machine conditions as B3.16.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1 B3_RUN=b317-parallel
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
ready=0; for _ in $(seq 1 $(( ${B317_WAIT_MIN:-180} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine with Docker in time ($(uptime))"; ps -Ao pcpu,comm -r | head -6 || true; exit 1; }
mkdir -p "results/$B3_RUN"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; docker ps --format '{{.Names}}'; } > "results/$B3_RUN/b317.start"
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json"; [ -f "$f" ] && grep -q '"correct": true' "$f"; }
step() { local name=$1; shift
  if done_ok "$name"; then echo "skip $name"; return; fi
  powered || { echo "STOPPED: not on AC power with the lid open before $name $(date +%H:%M:%S)"; exit 1; }
  echo "== $name $(date +%H:%M:%S)"; snap "$name"; "$@" || echo "FAILED $name $(date +%H:%M:%S)"; snap "$name"; }
clean() { docker rm -f b3-questdb b3-morletdb >/dev/null 2>&1 || true; }
for r in 1 2 3; do for wd in "4 D2" "8 D2" "8 D1"; do
  set -- $wd; w=$1; d=$2
  step "parallel_morletdb-i1_${d}_w${w}_r$r" "$V/.venv-b3/bin/python" parallel.py morletdb-i1 "$d" "$w" "$r"; clean
  step "parallel_questdb_${d}_w${w}_r$r" "$V/.venv-b3/bin/python" parallel.py questdb "$d" "$w" "$r"; clean
done; done
echo "== B3.17 done $(date +%H:%M:%S)"
