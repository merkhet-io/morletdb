set -eu
cd /Users/zin/sophia/morletdb-eval
py() { /Users/zin/sophia/morletdb/.venv-seismic/bin/python -I evaluation/decision/timing.py "$@"; }
D=/Users/zin/sophia/morletdb/data/seismic-timing
py prepare --year 2023 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d --data $D
py features --year 2023 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d
py evaluate --year 2023 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d
py catalog --year 2024 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d
py windows --year 2024 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d
py fetch --year 2024 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d --data $D
py prepare --year 2024 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d --data $D
py features --year 2024 --out /Users/zin/sophia/morletdb/data/timing-runs/012bb3d
echo READY_FOR_HELDOUT
