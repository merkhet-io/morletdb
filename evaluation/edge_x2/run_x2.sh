#!/bin/sh
# X2 confirmation (PROTOCOL.md): on the Raspberry Pi, from the repository copy's root.
# Three rounds of (searchable, raw), config e512, benchmark set. Before every run: 5-minute load below 1 and
# SoC temperature below 55 C (cool-down), so every run starts from the same state.
set -e
PY=${PY:-$HOME/venv-x2/bin/python}
gate() {
  while [ "$(awk '{print int($2 * 100)}' /proc/loadavg)" -ge 100 ]; do sleep 30; done
  while [ "$(vcgencmd measure_temp | sed "s/temp=\([0-9]*\).*/\1/")" -ge 55 ]; do sleep 30; done
}
for r in 1 2 3; do
  for arm in searchable raw; do
    gate
    X1_STUDY=x2 X1_ARM=$arm $PY -I evaluation/edge_x1/x1.py e512 morletdb:x1 $r
  done
done
