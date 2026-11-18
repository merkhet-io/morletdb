#!/usr/bin/env bash
# B3.19 (PROTOCOL.md, Amendment B3.19): interval statistics against CedarDB with MorletDB 0.3.10-alpha.1 (native fast
# path), three alternating D2 r1 rounds in one session; the start and power conditions of B3.16/B3.18.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1 B3_READS_FRESH_PROCESS=1
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
late() { [ "$(date +%H%M)" -ge "${B319_LATEST:-0700}" ] && [ "$(date +%H%M)" -lt 1800 ]; }
late && { echo "NOT STARTED: after ${B319_LATEST:-0700} (the owner's day); rerun on the next quiet evening"; exit 1; }
ready=0; for _ in $(seq 1 $(( ${B319_WAIT_MIN:-120} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; late && break; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine in time ($(uptime))"; exit 1; }
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
clean() { docker rm -f b3-cedardb b3-morletdb >/dev/null 2>&1 || true; }
for s in 1 2 3; do
  export B3_RUN=b319-cedardb-s$s
  [ -f "results/$B3_RUN/load_cedardb_D2_r1.json" ] && { echo "skip round $s"; continue; }
  [ "$s" = 1 ] || ! late || { echo "STOPPED before round $s: $(date +%H:%M) is the owner's day; rounds done are reported as such"; exit 1; }
  powered || { echo "STOPPED: not on AC power before round $s"; exit 1; }
  mkdir -p "results/$B3_RUN"; { date +%FT%T; uptime; docker ps --format '{{.Names}}'; } > "results/$B3_RUN/b319.start"
  echo "== $B3_RUN morletdb $(date +%H:%M:%S)"; snap morletdb
  "$V/.venv-b3/bin/python" run.py load morletdb-i1-sum D2 1 || echo "FAILED $B3_RUN morletdb"; snap morletdb; clean
  echo "== $B3_RUN cedardb $(date +%H:%M:%S)"; snap cedardb
  "$V/.venv-b3/bin/python" run_cedar.py load D2 1 || echo "FAILED $B3_RUN cedardb"; snap cedardb; clean
done
echo "== B3.19 done $(date +%H:%M:%S)"
