#!/usr/bin/env bash
# B3.20 (PROTOCOL.md, Amendment B3.20): B3.17's parallel-writer track, MorletDB 0.3.10-alpha.1 (B3.10's client path)
# against CedarDB v2026-11-04, in one session. B3.16's start and power conditions; no load starts after 07:00.
set -uo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1 B3_RUN=b320-parallel
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
# the next 07:00 (the owner's day): no load starts after it
if [ "$(date +%H%M)" -lt "${B320_LATEST:-0700}" ]; then DEADLINE=$(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B320_LATEST:-0700}" +%s)
else DEADLINE=$(( $(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B320_LATEST:-0700}" +%s) + 86400 )); fi
late() { [ "$(date +%s)" -ge "$DEADLINE" ]; }
echo "waiting for a quiet, powered machine until $(date -r $DEADLINE +%FT%H:%M) ($(date +%H:%M))"
ready=0; while ! late; do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine before $(date -r $DEADLINE +%H:%M) ($(uptime))"; exit 1; }
mkdir -p "results/$B3_RUN"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; docker ps --format '{{.Names}}'; } > "results/$B3_RUN/b320.start"
snap() { mkdir -p "results/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "results/$B3_RUN/machine/$1.txt"; }
done_ok() { local f="results/$B3_RUN/$1.json"; [ -f "$f" ] && grep -q '"correct": true' "$f"; }
step() { local name=$1; shift
  if done_ok "$name"; then echo "skip $name"; return; fi
  late && { echo "STOPPED before $name: $(date +%H:%M) is the owner's day; loads done are reported as such"; exit 1; }
  powered || { echo "STOPPED: not on AC power with the lid open before $name $(date +%H:%M:%S)"; exit 1; }
  echo "== $name $(date +%H:%M:%S)"; snap "$name"; "$V/.venv-b3/bin/python" "$@" || echo "FAILED $name $(date +%H:%M:%S)"; snap "$name"; }
clean() { docker rm -f b3-cedardb b3-morletdb >/dev/null 2>&1 || true; }
voided=()
for r in 1 2 3; do for wd in "4 D2" "8 D2" "8 D1"; do
  set -- $wd; w=$1; d=$2
  step "parallel_morletdb-i1_${d}_w${w}_r$r" parallel.py morletdb-i1 "$d" "$w" "$r"; clean
  step "parallel_cedardb_${d}_w${w}_r$r" parallel.py cedardb "$d" "$w" "$r"; clean
  done_ok "parallel_cedardb_${d}_w${w}_r$r" || voided+=("$w $d $r")
done; done
# a CedarDB load whose correctness check failed is void; it is replaced once, at the end, by rep r+3, after a
# MorletDB load as in the main sequence (PROTOCOL.md, B3.20)
for v in "${voided[@]+"${voided[@]}"}"; do
  set -- $v; w=$1; d=$2; r=$(( $3 + 3 ))
  echo "replacing void CedarDB load w$w $d r$3 by r$r"
  step "parallel_morletdb-i1_${d}_w${w}_r$r" parallel.py morletdb-i1 "$d" "$w" "$r"; clean
  step "parallel_cedardb_${d}_w${w}_r$r" parallel.py cedardb "$d" "$w" "$r"; clean
done
echo "== B3.20 done $(date +%H:%M:%S)"
