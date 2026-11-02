#!/usr/bin/env python3
"""E7 (PROTOCOL.md): E3's harness on one gallery, with the screened exact path (the product default in
pg_morlet 0.3.3) and the full exact path (morlet.exact_screen = off) as separate arms.

  .venv-seismic/bin/python -I evaluation/screened_exact/run_e7.py <db> gate|run <out.json> <build> <max_candidates> default|w8

gate: E3's gate (its in-database list is the screened path), identity of the screened lists with E4's
      stored lists (bit for bit), and identity of the full path's lists with the screened ones.
run:  arms X-PG (screened), X-FULL, P-PG, X-MAT32R; w8 sets the envelope table's parallel_workers to 8.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "fast_exact"))
import run_e6  # noqa: E402  (E6's runner: E3's harness, the cap and worker helpers)

e3, e1 = run_e6.e3, run_e6.e3.e1


def arm_full(conn, rid, s):
    conn.execute("SET morlet.exact_screen = off")
    try:
        return e1.ARMS["EX-8"](conn, rid, s)
    finally:
        conn.execute("RESET morlet.exact_screen")


e3.ARMS["X-FULL"] = arm_full


def full_gate(db, out, cap):
    """The full path's distance lists against the gate's screened lists, bit for bit."""
    import psycopg
    lists = json.loads(Path(out).read_text())["lists"]
    bad = []
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
        c.execute("SET max_parallel_workers_per_gather = 8")
        c.execute("SET morlet.exact_screen = off")
        for key, rec in lists.items():
            rid, s = key.rsplit(":", 1)
            got = [(r, int(st), float(d)) for r, st, d in c.execute(
                "SELECT recording_id, start_sample, distance FROM morlet_search_envelope_interval(%s, %s, %s, %s,"
                " '{}'::jsonb, 10, 0.075, true, true, %s)", (e3.COLL, rid, int(s), int(s) + e3.W, cap)).fetchall()]
            if got != [tuple(x) for x in rec["ref"]]:
                bad.append(key)
    return {"queries": len(lists), "identical": len(lists) - len(bad), "differ": bad}


if __name__ == "__main__":
    db, mode, out, build, cap, config = sys.argv[1:7]
    e3.DB = db
    run_e6.set_cap(int(cap))
    if mode == "gate":
        e3.main("gate", out, build, config)
        res = json.loads(Path(out).read_text())
        res["identity_with_measured_build"] = run_e6.identity_gate(db, out)
        res["identity_with_full_path"] = full_gate(db, out, int(cap))
        res["passed"] = bool(res["passed"] and not res["identity_with_measured_build"]["differ"]
                             and not res["identity_with_full_path"]["differ"])
        Path(out).write_text(json.dumps(res, indent=1))
        for k in ("identity_with_measured_build", "identity_with_full_path"):
            print(k, {a: b for a, b in res[k].items() if a != "differ"}, "differ", len(res[k]["differ"]))
        print("passed", res["passed"], flush=True)
        sys.exit(0 if res["passed"] else 1)
    e3.TIMED = ["X-PG", "X-FULL", "P-PG", "X-MAT32R"]
    reloptions = run_e6.workers(db, 8 if config == "w8" else None)
    try:
        e3.main("run", out, build, config)
    finally:
        after = run_e6.workers(db, None)
    res = json.loads(Path(out).read_text())
    res["envelope_table_reloptions"] = {"during": reloptions, "after": after}
    Path(out).write_text(json.dumps(res, indent=1))
