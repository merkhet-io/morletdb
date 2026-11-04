#!/bin/sh
# X2 deviations 1-2: the remaining runs, gate at 60 C (below the soft temperature limit).
set -e
PY=${PY:-$HOME/venv-x2/bin/python}
gate() {
  while [ "$(awk '{print int($2 * 100)}' /proc/loadavg)" -ge 100 ]; do sleep 30; done
  while [ "$(vcgencmd measure_temp | sed "s/temp=\([0-9]*\).*/\1/")" -ge 60 ]; do sleep 30; done
}
for spec in "raw 2" "searchable 3" "raw 3" "searchable 4" "raw 4" "searchable 5"; do
  set -- $spec
  gate
  X1_STUDY=x2 X1_ARM=$1 $PY -I evaluation/edge_x1/x1.py e512 morletdb:x1 $2
done
