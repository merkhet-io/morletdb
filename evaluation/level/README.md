# L1: level track

- [PROTOCOL.md](PROTOCOL.md): frozen protocol and changes after freeze.
- [RESULTS.md](RESULTS.md): synthetic results, verdict **unclear** (gap advantage holds).
- [WALKTHROUGH.md](WALKTHROUGH.md): one track in SQL, with output.
- `kalman.py`: reference filter (written from the model), batch Gaussian conditioning,
  likelihood calibration, EMA and rolling-median baselines.
- `sql_check.py`: `morlet_level_track` against the reference (exact), cut-offs, invalid
  configuration, access and row-level security, repeatability.
- `evaluate.py`: the L1 comparison.

```sh
createdb morlet_level_check && psql -d morlet_level_check -c 'CREATE EXTENSION pg_morlet'   # SQL 0.3.1 from this checkout
MORLET_DSN="host=localhost port=28817 dbname=morlet_level_check" \
  .venv-seismic/bin/python -I evaluation/level/sql_check.py evaluation/level/results/sql_check.json
.venv-compress/bin/python -I evaluation/level/evaluate.py evaluation/level/results/l1.json
```
