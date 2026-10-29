# C7: pinned healthy baseline

- [PROTOCOL.md](PROTOCOL.md): frozen protocol and changes after freeze.
- `c7.py`: harness (`values`, `run`, `paderborn`); `test_c7.py`: synthetic checks.
- `sql_check.py`: product check of H-PIN through `metadata_filter` (`export`, `synth`, `check`).

```sh
P=.venv-compress/bin/python   # numpy
$P -I evaluation/context/c7/test_c7.py
# After the download (data/xjtu-sy/extracted), once:
$P -I evaluation/context/c7/c7.py values data/xjtu-sy/extracted data/xjtu-sy/run
$P -I evaluation/context/c7/sql_check.py export data/xjtu-sy/extracted data/xjtu-sy/run/check
MORLET_DSN="host=localhost port=28817 dbname=morlet_c7_check" \
  .venv-seismic/bin/python -I evaluation/context/c7/sql_check.py check data/xjtu-sy/run/check
$P -I evaluation/context/c7/c7.py run data/xjtu-sy/run
# Only after the XJTU-SY verdicts are committed (consumed data, exploratory):
$P -I evaluation/context/c7/c7.py paderborn data/paderborn-rtf/run
```
