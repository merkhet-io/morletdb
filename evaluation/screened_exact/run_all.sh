#!/bin/sh
# E7 (PROTOCOL.md): update the galleries, then gate and two timed runs per gallery, in order.
set -u
cd "$(dirname "$0")/../.."
PSQL=~/.pgrx/17.11/pgrx-install/bin/psql
PY=.venv-seismic/bin/python
R=evaluation/screened_exact/results
BUILD="$(git rev-parse --short HEAD) lib $(shasum -a 256 ~/.pgrx/17.11/pgrx-install/lib/postgresql/pg_morlet.dylib | cut -c1-16)"
wait_load() {
  while :; do
    l=$(sysctl -n vm.loadavg | awk '{print $3}')
    awk -v l="$l" 'BEGIN{exit !(l<5)}' && break
    echo "waiting: 5-min load $l"; sleep 60
  done
}
for pair in "morlet_r2 N1 1000000" "morlet_e4_30 N2 1000000" "morlet_e4_61 N3 2000000"; do
  set -- $pair
  $PSQL -h localhost -p 28817 -d "$1" -qc "ALTER EXTENSION pg_morlet UPDATE TO '0.3.3'" || exit 1
  wait_load
  $PY -I evaluation/screened_exact/run_e7.py "$1" gate "$R/gate_$2.json" "$BUILD" "$3" w8 > "$R/gate_$2.log" 2>&1 || { echo "gate $2 failed"; exit 1; }
  for cfg in default w8; do
    wait_load
    $PY -I evaluation/screened_exact/run_e7.py "$1" run "$R/run_$2_$cfg.json" "$BUILD" "$3" "$cfg" > "$R/run_$2_$cfg.log" 2>&1 || { echo "run $2 $cfg failed"; exit 1; }
  done
done
echo done
