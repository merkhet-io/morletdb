set -eu
D=/Users/zin/sophia/morletdb/data/paderborn/zenodo-15845309
cd /Users/zin/sophia/morletdb-eval
for f in $D/archives/*.rar; do b=$(basename $f .rar); [ -d $D/extracted/$b ] || bsdtar -xf $f -C $D/extracted; done
echo extracted $(ls $D/extracted | wc -l)
PY="/Users/zin/sophia/morletdb/.venv-research/bin/python -I evaluation/decision/bearings.py"
$PY verify --archives $D/archives --out /Users/zin/sophia/morletdb/data/decision-runs/e6bf274
$PY prepare --extracted $D/extracted --out /Users/zin/sophia/morletdb/data/decision-runs/e6bf274
$PY features --out /Users/zin/sophia/morletdb/data/decision-runs/e6bf274
$PY evaluate --out /Users/zin/sophia/morletdb/data/decision-runs/e6bf274 --role dev
echo DEV_DONE
