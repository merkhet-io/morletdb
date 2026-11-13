#!/usr/bin/env bash
# CedarDB range-scan fault, reproduction 2 (CEDARDB_RANGE_SCAN_FAULT.md): round 3's sequence, a MorletDB 0.3.10 load
# right before each fresh CedarDB load, (A) as is and (B) with 4 busy host threads to approach round 3's load
# average of about 5. Correctness only. Stops at the first failed read-back, keeping that container and volume.
set -uo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_RUN=b319-repro2 B3_READS_FRESH_PROCESS=1 B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1
mkdir -p "results/$B3_RUN"
pids=()
stress_on() { for _ in 1 2 3 4; do (while :; do :; done) & pids+=($!); done; }
stress_off() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null; pids=(); }
trap stress_off EXIT
r=100
for mode in A B; do
  [ $mode = B ] && stress_on
  for i in $(seq 1 10); do
    r=$((r+1)); { date +%FT%T; echo "mode $mode"; uptime; } >> "results/$B3_RUN/start.txt"
    "$V/.venv-b3/bin/python" run.py load morletdb-i1-sum D2 $r > "results/$B3_RUN/morlet_r$r.log" 2>&1 || echo "   morletdb r$r failed (not the subject)"
    docker rm -f b3-morletdb >/dev/null 2>&1
    echo "== $mode cedardb D2 r$r $(date +%H:%M:%S) $(sysctl -n vm.loadavg)"
    if "$V/.venv-b3/bin/python" run_cedar.py load D2 $r > "results/$B3_RUN/r$r.log" 2>&1; then echo "   pass"
    else echo "   FAILED: $(grep -A6 '"mismatches"' results/$B3_RUN/r$r.log | head -8 | tr -s ' \n' ' ')"; exit 0; fi
  done
  stress_off
done
echo "== done $(date +%H:%M:%S)"
