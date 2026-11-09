#!/usr/bin/env bash
# J3, in order (PROTOCOL.md). LAYA_PY: the Python of the environment with laya 0.4.30 (default ~/.cache/morlet-j3/venv);
# ANALYSIS_PY: a Python with numpy, scipy and scikit-learn.
set -euo pipefail
cd "$(dirname "$0")"
LAYA_PY=${LAYA_PY:-$HOME/.cache/morlet-j3/venv/bin/python}
ANALYSIS_PY=${ANALYSIS_PY:-python3}
R=results
uptime | tee $R/load_before.txt
$LAYA_PY decide.py fit $R/fit.json
K=$(python3 -c "import json;print(json.load(open('$R/fit.json'))['k'])")
python3 compact.py "$K" $R/states.json
for arm in laya laya-td jev; do
  [ -f $R/check_$arm.json ] || $LAYA_PY decide.py check $arm $R/check_$arm.json
done
for arm in laya laya-td jev; do $LAYA_PY decide.py run $arm $R/states.json $R/calls_$arm.jsonl; done
uptime | tee $R/load_after.txt
$ANALYSIS_PY analyze.py $R $R/result.json > /dev/null
python3 -c "import json;d=json.load(open('$R/result.json'));print(d['decision'])"
