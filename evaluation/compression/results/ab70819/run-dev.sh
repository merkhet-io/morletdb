set -eu
export OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd /Users/zin/sophia/morletdb-compress
py() { /Users/zin/sophia/morletdb/.venv-compress/bin/python -I evaluation/compression/bench.py "$@"; }
for d in ims cx paderborn; do py rd --dataset $d --role dev --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819; done
py operating --out /Users/zin/sophia/morletdb/data/compress-runs/ab70819
echo DEV_DONE
