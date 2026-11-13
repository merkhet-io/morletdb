#!/usr/bin/env bash
# B7 benchmark run after the freeze (dd756e6): stress vector, then rounds 1-3; machine state before each step.
set -uo pipefail
cd "$(dirname "$0")"
PY=/Users/zin/sophia/morletdb/.venv-b3/bin/python
step() { { echo "== $1 $(date +%FT%T)"; uptime; docker ps --format '{{.Names}}'; } >> results/machine/state.txt; }
step stress; $PY b7.py stress > results/stress.log 2>&1; echo "stress exit $?"
for r in 1 2 3; do
  step "round $r"; $PY b7.py round $r > results/round$r.log 2>&1; rc=$?; echo "round $r exit $rc $(date +%T)"
  [ $rc -eq 0 ] || break
done
step end
