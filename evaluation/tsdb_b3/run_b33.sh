#!/bin/sh
# B3.3 confirmation (PROTOCOL.md "Amendment B3.3"), benchmark set. Run from evaluation/tsdb_b3.
set -e
PY=../../.venv-b3/bin/python
export B3_RUN=b33
wait_load() { while [ "$(sysctl -n vm.loadavg | awk '{print int($3)}')" -ge 5 ]; do sleep 30; done; }
for r in 1 2 3; do
  wait_load
  B3_MORLET_IMAGE=morletdb:b33 $PY run.py load morletdb-033 D2 $r
  B3_MORLET_IMAGE=morletdb:b33 $PY run.py load morletdb-033-feat D2 $r
  B3_MORLET_IMAGE=morletdb:b33 $PY run.py load morletdb-033-search D2 $r
  B3_RUN=b33/reference B3_MORLET_IMAGE=morletdb:b33-dev $PY run.py load morletdb-033-feat D2 $r
done
B3_ACQ=1 $PY run.py load questdb D2 1
for s in morletdb-033 morletdb-033-feat morletdb-033-search; do
  B3_RUN=b33/profile B3_PROFILE=1 B3_MORLET_IMAGE=morletdb:b33 $PY run.py load $s D2 4
done
B3_RUN=b33/profile B3_PROFILE=1 B3_MORLET_IMAGE=morletdb:b33-dev $PY run.py load morletdb-033-feat D2 4
