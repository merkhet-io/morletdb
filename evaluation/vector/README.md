# VR1: pg_morlet features and text vectors in pgvector

- [PROTOCOL.md](PROTOCOL.md): frozen protocol and changes after freeze.
- [RESULTS.md](RESULTS.md): results; outputs in `results/`.
- `vr1.py`: loader and checks; `posthoc_ties.py`: post-hoc duplicate analysis (decides nothing).

Needs pgvector 0.8.0 built against the same PostgreSQL (`make PG_CONFIG=... && make install`
in a checkout of `github.com/pgvector/pgvector` at tag `v0.8.0`) and PTB-XL 1.0.3 under
`data/ptb-xl/1.0.3` (see `research/multiscale_memory/README.md`).

```sh
createdb -h localhost -p 28817 morlet_vector_check
psql -h localhost -p 28817 -d morlet_vector_check -c 'CREATE EXTENSION pg_morlet' -c 'CREATE EXTENSION vector'
export MORLET_DSN="host=localhost port=28817 dbname=morlet_vector_check"
P=.venv-seismic/bin/python   # numpy, psycopg
$P -I evaluation/vector/vr1.py load data/ptb-xl/1.0.3
$P -I evaluation/vector/vr1.py check evaluation/vector/results
$P -I evaluation/vector/posthoc_ties.py evaluation/vector/results
```
