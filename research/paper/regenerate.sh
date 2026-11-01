#!/usr/bin/env bash
# Regenerate the report's numbers, tables and figures from STORED results, then build both PDFs.
# Needs no database and runs no benchmark. Figure 4 (cases.pdf) is the exception: it needs the
# held-out database and is regenerated separately (see research/paper/cases/cases.py).
#   research/paper/regenerate.sh            # PY_PAPER defaults to .venv-paper/bin/python
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY_PAPER=${PY_PAPER:-$ROOT/.venv-paper/bin/python}
cd "$ROOT"
python3 research/paper/gen_numbers.py                                    # tables, macros, README block
python3 evaluation/consolidated/e1_distribution.py evaluation/consolidated/results >/dev/null
"$PY_PAPER" -I research/paper/figures.py                                 # Figures 2 and 3 (deterministic PDFs)
(cd research/paper && tectonic -X compile morletdb.tex && tectonic -X compile supplement.tex)
echo "regenerated from stored results; compare with git diff"
