#!/bin/sh
# X1 confirmation (PROTOCOL.md): three runs per configuration on the benchmark set, alternating. Run from the repo root.
set -e
PY=${PY:-../morletdb/.venv-b3/bin/python}
wait_load() { while [ "$(sysctl -n vm.loadavg | awk '{print int($3)}')" -ge 5 ]; do sleep 30; done; }
for r in 1 2 3; do
  for c in ref e1g e512; do
    wait_load
    $PY -I evaluation/edge_x1/x1.py $c morletdb:x1 $r
  done
done
