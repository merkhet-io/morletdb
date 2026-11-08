#!/usr/bin/env bash
# L1-beta (PROTOCOL.md): every point three times, in a fresh container each, on a quiet, powered Mac.
set -euo pipefail
cd "$(dirname "$0")"
PY=${L1_PYTHON:-python3}
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
ready=0; for _ in $(seq 1 $(( ${L1_WAIT_MIN:-180} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine in time ($(uptime))"; exit 1; }
mkdir -p results/machine; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; docker ps --format '{{.Names}}'; } > results/l1.start
for r in 1 2 3; do
  for p in $($PY -I l1.py list); do
    f="results/${p}_r${r}.json"
    if [ -f "$f" ]; then echo "skip $p r$r"; continue; fi
    powered || { echo "STOPPED: not on AC power before $p r$r"; exit 1; }
    { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -6 || true; } >> "results/machine/${p}.txt"
    echo "== $p r$r $(date +%H:%M:%S)"
    $PY -I l1.py "$p" "$r" || echo "FAILED $p r$r"
  done
done
echo "== L1-beta done $(date +%H:%M:%S)"
