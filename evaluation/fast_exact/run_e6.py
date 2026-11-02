#!/usr/bin/env python3
"""E6 (PROTOCOL.md): E3's harness on one gallery with the E6 library installed.

  .venv-seismic/bin/python -I evaluation/fast_exact/run_e6.py <db> gate|run <out.json> <build> <max_candidates> default|w8

gate: E3's gate, then identity of the in-database exact lists with E4's stored lists (bit for bit).
run:  arms X-PG, P-PG, X-MAT32R; w8 sets the envelope table's parallel_workers to 8 for the run.
"""
import inspect
import json
import sys
import textwrap
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "strong_exact"))
import e3  # noqa: E402

E4 = HERE.parent / "scaling_crossover" / "results"
GALLERY = {"morlet_r2": "N1", "morlet_e4_30": "N2", "morlet_e4_61": "N3"}


def set_cap(cap):
    if cap == 1_000_000:
        return
    old, new = "true, true, 1000000)", f"true, true, {cap})"
    e3.e1.EXACT_SQL = e3.e1.EXACT_SQL.replace(old, new)
    src = textwrap.dedent(inspect.getsource(e3.list_gate))
    assert old in src and e3.e1.EXACT_SQL.endswith(new)
    exec(compile(src.replace(old, new), e3.__file__, "exec"), e3.__dict__)


def identity_gate(db, out):
    new = json.loads(Path(out).read_text())["lists"]
    old = json.loads((E4 / f"gate_{GALLERY[db]}.json").read_text())["lists"]
    bad = [q for q in old if [tuple(x) for x in old[q]["ref"]] != [tuple(x) for x in new[q]["ref"]]]
    return {"queries": len(old), "identical": len(old) - len(bad), "differ": bad}


def workers(db, n):
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
        if n is None:
            c.execute("ALTER TABLE public.r2_envelopes RESET (parallel_workers)")
        else:
            c.execute(f"ALTER TABLE public.r2_envelopes SET (parallel_workers = {int(n)})")
        return c.execute("SELECT reloptions FROM pg_class WHERE oid = 'public.r2_envelopes'::regclass").fetchone()[0]


if __name__ == "__main__":
    db, mode, out, build, cap, config = sys.argv[1:7]
    e3.DB = db
    set_cap(int(cap))
    if mode == "gate":
        e3.main("gate", out, build, config)
        res = json.loads(Path(out).read_text())
        res["identity_with_measured_build"] = identity_gate(db, out)
        res["passed"] = bool(res["passed"] and not res["identity_with_measured_build"]["differ"])
        Path(out).write_text(json.dumps(res, indent=1))
        print("identity", {k: v for k, v in res["identity_with_measured_build"].items() if k != "differ"},
              "differ", len(res["identity_with_measured_build"]["differ"]), "passed", res["passed"], flush=True)
        sys.exit(0 if res["passed"] else 1)
    e3.TIMED = ["X-PG", "P-PG", "X-MAT32R"]
    reloptions = workers(db, 8 if config == "w8" else None)
    try:
        e3.main("run", out, build, config)
    finally:
        after = workers(db, None)
    res = json.loads(Path(out).read_text())
    res["envelope_table_reloptions"] = {"during": reloptions, "after": after}
    Path(out).write_text(json.dumps(res, indent=1))
