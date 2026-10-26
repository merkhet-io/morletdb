# Decision milestone

| File | Contents |
|---|---|
| [ASSESSMENT.md](ASSESSMENT.md) | What existing reports establish, per hypothesis |
| [DATASET.md](DATASET.md) | Paderborn KAt verification record, source and licence decisions |
| [PLAN.md](PLAN.md) | Precommitted H2 retrieval experiment (frozen); result in `results/e6bf274/` |
| [PLAN_TIMING.md](PLAN_TIMING.md) | Precommitted timing test (frozen); result in `results/timing-012bb3d/` |
| `bearings.py`, `paderborn_mat.py` | Harness: verify, prepare, features, evaluate |
| `timing.py`, `test_timing.py` | Timing test harness (GEOFON + ComCat) and its synthetic controls |
| `test_bearings.py` | Synthetic controls (leakage, task masks, metrics); not evidence |

## Reproduce

```sh
cargo build --locked --release -p morlet-core --example features
D=data/paderborn/zenodo-15845309          # git-ignored; CC BY-NC 4.0, not redistributed
# Archives from https://zenodo.org/records/15845309 (31 of 32; KA08 missing), checked against
# the record's MD5s; SHA-256s are recorded in $D/SHA256SUMS.archives.
mkdir -p $D/extracted && for f in $D/archives/*.rar; do bsdtar -xf $f -C $D/extracted; done
PY=".venv/bin/python -I evaluation/decision/bearings.py"   # numpy, scipy
OUT=data/decision-runs/<commit>
$PY verify   --archives $D/archives --out $OUT
$PY prepare  --extracted $D/extracted --out $OUT
$PY features --out $OUT
$PY evaluate --out $OUT --role dev       # development bearings; sets no-match thresholds
$PY evaluate --out $OUT --role heldout   # once
python -m unittest discover -s evaluation/decision -p 'test_*.py'
```

Cite Lessmeier et al. (2016) and KAt, Paderborn University, when reporting results.
