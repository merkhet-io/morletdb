# S1: explainable sleep staging by retrieval

- [PROTOCOL.md](PROTOCOL.md) (frozen), [RESULTS.md](RESULTS.md), `split.json`, outputs in `results/`.
- `s1.py`: harness; `test_s1.py`: synthetic checks.

```sh
P=.venv-seismic/bin/python   # numpy, scipy, psycopg
export MORLET_DSN="host=localhost port=28817 dbname=morlet_sleep"   # pg_morlet 0.3.1
D=data/sleep-edfx/1.0.0/sleep-cassette   # PhysioNet sleep-edfx 1.0.0, checksum-verified
$P -I evaluation/sleep/test_s1.py
$P -I evaluation/sleep/s1.py features $D data/sleep-edfx/run development
$P -I evaluation/sleep/s1.py check data/sleep-edfx/run $D
$P -I evaluation/sleep/s1.py select data/sleep-edfx/run
$P -I evaluation/sleep/s1.py evaluate $D data/sleep-edfx/run      # reads held-out subjects; once
```
