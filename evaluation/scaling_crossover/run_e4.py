#!/usr/bin/env python3
"""E4 (PROTOCOL.md): E3's harness on one gallery database, unchanged in method.

  .venv-seismic/bin/python -I evaluation/scaling_crossover/run_e4.py <db> gate|run <out.json> <68594d7 checkout> [max_candidates]

max_candidates (default 1000000, as in E1 and E3) is the eligibility cap the harness passes to the in-database
exact search. The function raises above it and otherwise returns the same list. N3 has 1,026,010 eligible
windows, so it runs with 2000000 (RESULTS.md, deviations).
"""
import inspect
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strong_exact"))
import e3  # noqa: E402

if __name__ == "__main__":
    db, mode, out, build = sys.argv[1:5]
    cap = int(sys.argv[5]) if len(sys.argv) > 5 else 1_000_000
    if cap != 1_000_000:
        old, new = "true, true, 1000000)", f"true, true, {cap})"
        assert old in e3.e1.EXACT_SQL
        e3.e1.EXACT_SQL = e3.e1.EXACT_SQL.replace(old, new)
        src = textwrap.dedent(inspect.getsource(e3.list_gate))
        assert old in src
        exec(compile(src.replace(old, new), e3.__file__, "exec"), e3.__dict__)
    e3.DB = db
    e3.main(mode, out, build, "default")
