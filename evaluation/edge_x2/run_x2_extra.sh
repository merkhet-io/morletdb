#!/bin/sh
# X2 deviation 1: three fanless runs added after run_x2.sh (searchable 4, raw 4, searchable 5), same gate.
set -e
PY=${PY:-$HOME/venv-x2/bin/python}
while pgrep -f run_x2.sh >/dev/null; do sleep 30; done
gate() {
  while [ "$(awk '{print int($2 * 100)}' /proc/loadavg)" -ge 100 ]; do sleep 30; done
  while [ "$(vcgencmd measure_temp | sed "s/temp=\([0-9]*\).*/\1/")" -ge 55 ]; do sleep 30; done
}
for spec in "searchable 4" "raw 4" "searchable 5"; do
  set -- $spec
  gate
  X1_STUDY=x2 X1_ARM=$1 $PY -I evaluation/edge_x1/x1.py e512 morletdb:x1 $2
done
