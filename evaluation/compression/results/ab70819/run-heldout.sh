set -eu
export OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd /Users/zin/sophia/morletdb-compress
PY=/Users/zin/sophia/morletdb/.venv-compress/bin/python
for d in ims cx paderborn; do $PY -I evaluation/compression/bench.py rd --dataset $d --role heldout --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo "RD_DONE $d"; done
$PY -I evaluation/compression/h2.py paderborn --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo H2P_DONE
$PY -I evaluation/compression/h2.py ims --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo H2I_DONE
$PY -I evaluation/compression/h3.py --timing-run /Users/zin/sophia/morletdb/data/timing-runs/012bb3d --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo H3_DONE
$PY -I evaluation/compression/h6.py run --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo H6_DONE
$PY -I evaluation/compression/costs.py --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo COSTS_DONE
$PY -I evaluation/compression/report.py --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; echo ALL_DONE
