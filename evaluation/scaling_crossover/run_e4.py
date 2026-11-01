#!/usr/bin/env python3
"""E4 (PROTOCOL.md): E3's harness on one gallery database, unchanged in method.

  .venv-seismic/bin/python -I evaluation/scaling_crossover/run_e4.py <db> gate|run <out.json> <68594d7 checkout>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strong_exact"))
import e3  # noqa: E402

if __name__ == "__main__":
    db, mode, out, build = sys.argv[1:5]
    e3.DB = db
    e3.main(mode, out, build, "default")
