# C8: context sensitivity of history neighbours

- [PROTOCOL.md](PROTOCOL.md): frozen protocol (2026-11-16) and changes after freeze.
- [RESULTS.md](RESULTS.md): results (descriptive measurement).
- `c8.py`: harness (`export-bosch`, `load-bosch`, `load-phm`, `query`, `check`, `analyse`);
  `test_c8.py`: synthetic checks; `compose.yaml`: the database (released image, own project and port).
- `results/c8.json`: every number of RESULTS.md; `results/check_*.json`: product checks;
  `results/load_*.json`: versions and load times; `results/SHA256SUMS`: neighbour files and the Bosch export list; `results/phm_inputs_SHA256SUMS`: every
  PHM 2010 file read.

```sh
C=/path/to/compression-venv/bin/python   # numpy, h5py
S=/path/to/seismic-venv/bin/python       # numpy, pandas, psycopg
OUT=data/c8-run
$S -I evaluation/context/c8/test_c8.py
docker compose -p c8ctx -f evaluation/context/c8/compose.yaml up -d --wait
$C -I evaluation/context/c8/c8.py export-bosch data/bosch/repo data/bosch/SHA256SUMS $OUT
$S -I evaluation/context/c8/c8.py load-bosch $OUT
$S -I evaluation/context/c8/c8.py load-phm data/phm2010 $OUT
for d in bosch phm; do
  $S -I evaluation/context/c8/c8.py query $d $OUT
  $S -I evaluation/context/c8/c8.py check $d $OUT
done
$S -I evaluation/context/c8/c8.py analyse $OUT evaluation/context/c8/results
docker compose -p c8ctx -f evaluation/context/c8/compose.yaml down -v
```

Add `--dev` to every step for the development subset (separate collections and files).
