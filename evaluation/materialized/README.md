# R1: materialized envelope representation

- [PROTOCOL.md](PROTOCOL.md): frozen protocol, with the development-run summary.
- [results/eval-2025/RESULTS.md](results/eval-2025/RESULTS.md): the single 2025
  evaluation. Decision: **narrow** (exact agreement, p95 at 0.10×, storage 27.9% > 25%).
- `r1.py`: harness (`load`, `run`), using pg_morlet 0.2.0 encoded storage and the
  timing test's window pipeline.

```sh
# Windows come from evaluation/decision/timing.py (catalog, plan, fetch, prepare) for the year.
createdb morlet_r1 && psql -d morlet_r1 -c 'CREATE EXTENSION pg_morlet'
python evaluation/materialized/r1.py load --year 2023 --windows data/timing-runs/012bb3d --out data/r1-runs/dev
python evaluation/materialized/r1.py run  --year 2023 --windows data/timing-runs/012bb3d --out data/r1-runs/dev
# Evaluation (after the freeze, once):
python evaluation/materialized/r1.py load --year 2025 --windows data/r1-runs/eval --out data/r1-runs/eval
python evaluation/materialized/r1.py run  --year 2025 --windows data/r1-runs/eval --out data/r1-runs/eval --n 200
```
