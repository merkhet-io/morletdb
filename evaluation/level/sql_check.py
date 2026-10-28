#!/usr/bin/env python3
"""morlet_level_track against the reference filter (kalman.py), on synthetic episodes.

  sql_check.py [OUT.json]    (seismic environment: numpy, psycopg; MORLET_DSN; python -I)

Loads one collection with two machines and two operating modes, irregular gaps, two episodes with
the same start time, and an all-zero episode (std 0, invalid under the log transform). Checks, for
machine M1 in mode a:
  - every output column equals the reference to 1e-9 relative (observed to 1e-12);
  - other machines and modes, later episodes and episodes before since_utc are left out;
  - the reference recursion equals direct Gaussian conditioning;
  - invalid configurations are rejected;
  - a role without access is refused, and row-level security hides episodes from the track;
  - repeated calls return identical rows.
"""
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "context" / "c5"))
import c5  # noqa: E402
import kalman  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_level_dev")
COLL = "lvl"
RATE, N = 1000.0, 2048
Q, R = 0.004, 0.01  # per hour, ln units squared
T0 = 1_700_000_000.0


def episodes():
    """(id, machine, mode, hours, signal). Level: ln std drifts, then steps; one spike; one zero."""
    rng = np.random.default_rng(3)
    eps, hours = [], 0.0
    for k in range(40):
        hours += float(rng.choice([0.5, 1.0, 1.0, 2.0, 7.0]))
        level = 0.002 * k + (0.4 if k >= 30 else 0.0)
        scale = math.exp(level + 0.1 * rng.standard_normal() + (0.8 if k == 20 else 0.0))
        x = rng.standard_normal(N) * scale
        mode = "b" if k % 3 == 2 else "a"
        eps.append((f"e{k}", "M1", mode, hours, x))
    eps.append(("same-time", "M1", "a", eps[12][3], rng.standard_normal(N) * 1.05))
    eps.append(("zero", "M1", "a", eps[25][3] + 0.25, np.zeros(N)))
    for k in range(10):
        eps.append((f"m2-{k}", "M2", "a", 1.0 + 3 * k, rng.standard_normal(N) * 5))
    return eps


def load(conn, eps):
    for t in ("_acq_channels", "_acq", "_labels", "_ctx_values", "_ctx_segments", "_ctx", "_chunks", "_segments", ""):
        conn.execute(f"DROP TABLE IF EXISTS {COLL}{t} CASCADE")
    conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLL,))
    for enable in ("morlet_enable_context", "morlet_enable_acquisitions"):
        conn.execute(f"SELECT {enable}(%s)", (COLL,))
    with conn.transaction():
        for eid, machine, mode, hours, x in eps:
            rid = f"{eid}/V"
            conn.execute("SELECT morlet_create_acquisition(%s,%s,%s,to_timestamp(%s))", (COLL, eid, machine, T0 + hours * 3600))
            conn.execute("SELECT morlet_start_recording(%s,%s,%s,'V','g','{}',512,256,'zscore','none')", (COLL, rid, RATE))
            conn.execute("SELECT morlet_append_chunk(%s,%s,0,%s::float8[])", (COLL, rid, x.tolist()))
            conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,'V')", (COLL, eid, rid))
            end_ns = int(round(N / RATE * 1e9))
            rows = [{"key": "mode", "value": mode, "t_start_ns": 0, "t_end_ns": end_ns},
                    {"key": "episode", "value": eid, "t_start_ns": 0, "t_end_ns": end_ns}]
            conn.execute("SELECT morlet_add_acquisition_context(%s,%s,'lvl',%s)", (COLL, eid, json.dumps(rows)))
            conn.execute("SELECT morlet_build_acquisition_segments(%s,%s,ARRAY['mode'],ARRAY['episode'])", (COLL, eid))


def segment(conn, eid):
    return conn.execute(f"SELECT segment_id FROM {COLL}_ctx_segments WHERE recording_id=%s", (f"{eid}/V",)).fetchone()[0]


COLS = ("episode", "episode_segment_id", "occurrence", "start_ns", "hours_since_previous", "observed", "y",
        "predicted_level", "predicted_level_var", "innovation", "innovation_var", "standardized_innovation",
        "level", "level_var", "status", "model")


def call(conn, seg, extra="", params=()):
    rows = conn.execute(f"SELECT {','.join(COLS)} FROM morlet_level_track(%s,%s,%s,%s{extra})",
                        (COLL, seg, Q, R, *params)).fetchall()
    return [dict(zip(COLS, r)) for r in rows]


def close(a, b, rel):
    if a is None or b is None or (isinstance(b, float) and math.isnan(b)):
        return (a is None) and (b is None or math.isnan(b))
    return abs(a - b) <= rel * max(abs(b), 1e-300)


def expected(eps, query_id, since_hours=-math.inf):
    q = next(e for e in eps if e[0] == query_id)
    sel = [e for e in eps if e[1] == "M1" and e[2] == "a" and (e[3] < q[3] or e[0] == query_id) and e[3] >= since_hours]
    # Order: start time, then segment id (= load order here).
    order = {e[0]: i for i, e in enumerate(eps)}
    sel.sort(key=lambda e: (e[3], order[e[0]] if e[0] != query_id else 1e9))
    obs = np.array([c5.simple_values(e[4], RATE)[2] for e in sel])
    y = np.where(obs > 0, np.log(np.where(obs > 0, obs, 1.0)), np.nan)
    hours = np.array([e[3] for e in sel])
    return sel, obs, y, hours, kalman.track(y, hours, Q, R)


def compare(rows, sel, obs, y, hours, ref):
    worst, problems = 0.0, []
    if len(rows) != len(sel):
        problems.append(f"{len(rows)} rows, expected {len(sel)}")
        return worst, problems
    for k, (r, e) in enumerate(zip(rows, sel)):
        if r["occurrence"] != {"episode": e[0]}:
            problems.append(f"row {k}: occurrence {r['occurrence']} != {e[0]}")
        if not close(r["observed"], float(obs[k]), 1e-12) and not (obs[k] == 0 and r["observed"] == 0):
            problems.append(f"row {k}: observed {r['observed']} != {obs[k]}")
        if r["status"] != ref["status"][k] or r["model"] != kalman.MODEL:
            problems.append(f"row {k}: status {r['status']} != {ref['status'][k]}")
        if not close(float(r["start_ns"]) / 3.6e12, hours[k] + T0 / 3600, 1e-12):
            problems.append(f"row {k}: start {r['start_ns']}")
        for col in ("predicted_level", "predicted_level_var", "innovation", "innovation_var", "level", "level_var"):
            a, b = r[col], float(ref[col][k])
            if not close(a, b, 1e-9):
                problems.append(f"row {k}: {col} {a} != {b}")
            elif a is not None:
                worst = max(worst, abs(a - b) / max(abs(b), 1e-300))
        if r["innovation"] is not None and not close(r["standardized_innovation"],
                                                      r["innovation"] / math.sqrt(r["innovation_var"]), 1e-12):
            problems.append(f"row {k}: standardized innovation")
    return worst, problems


def main():
    import psycopg
    eps = episodes()
    report = {"checks": {}}
    with psycopg.connect(DSN, autocommit=True) as conn:
        load(conn, eps)
        last_a = [e for e in eps if e[1] == "M1" and e[2] == "a"]
        query = max(last_a, key=lambda e: e[3])[0]
        rows = call(conn, segment(conn, query))
        sel, obs, y, hours, ref = expected(eps, query)
        worst, problems = compare(rows, sel, obs, y, hours, ref)
        report["checks"]["track equals reference"] = {"episodes": len(rows), "max_relative_error": worst,
                                                      "statuses": sorted(set(r["status"] for r in rows)),
                                                      "problems": problems, "pass": not problems}
        # A mid-track query sees only earlier episodes; since_utc cuts the start.
        mid = "e15"
        rows = call(conn, segment(conn, mid), ",since_utc=>to_timestamp(%s)", (T0 + 10 * 3600,))
        sel, obs, y, hours, ref = expected(eps, mid, since_hours=10.0)
        _, problems = compare(rows, sel, obs, y, hours, ref)
        report["checks"]["earlier episodes and since_utc only"] = {"episodes": len(rows), "problems": problems,
                                                                   "pass": not problems}
        # Recursion equals direct conditioning (valid episodes only).
        _, _, y_all, h_all, ref_all = expected(eps, query)
        v = np.isfinite(y_all)
        bm, bv = kalman.batch_posterior(y_all[v], h_all[v], Q, R)
        err = max(np.max(np.abs(bm - ref_all["level"][v])), np.max(np.abs(bv - ref_all["level_var"][v]) / bv))
        report["checks"]["recursion equals batch conditioning"] = {"max_error": float(err), "pass": bool(err < 1e-6)}
        # Determinism.
        a = call(conn, segment(conn, query))
        b = call(conn, segment(conn, query))
        report["checks"]["repeat calls identical"] = {"pass": a == b}
        # Invalid configuration.
        bad = []
        for extra, params in ((",value_name=>%s", ("nope",)), (",transform=>%s", ("sqrt",)),
                              (",max_candidates=>%s", (0,)), (",max_candidates=>%s", (5,))):
            try:
                call(conn, segment(conn, query), extra, params)
                bad.append(extra)
            except psycopg.Error:
                pass
        for q, r in ((-1.0, R), (Q, 0.0), (float("nan"), R), (Q, float("inf"))):
            try:
                conn.execute("SELECT * FROM morlet_level_track(%s,%s,%s,%s)", (COLL, segment(conn, query), q, r)).fetchall()
                bad.append((q, r))
            except psycopg.Error:
                pass
        report["checks"]["invalid configuration rejected"] = {"accepted": [str(x) for x in bad], "pass": not bad}
        # Access: a role without privileges is refused; row-level security hides episodes.
        conn.execute("DROP ROLE IF EXISTS lvl_reader")
        conn.execute("CREATE ROLE lvl_reader")
        seg = segment(conn, query)
        refused = False
        with conn.transaction():
            conn.execute("SET LOCAL ROLE lvl_reader")
            try:
                with conn.transaction():
                    conn.execute("SELECT * FROM morlet_level_track(%s,%s,%s,%s)", (COLL, seg, Q, R)).fetchall()
            except psycopg.Error:
                refused = True
        tables = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE %s", (COLL + "%",)).fetchall()]
        for t in tables:
            conn.execute(f"GRANT SELECT ON {t} TO lvl_reader")
        conn.execute(f"ALTER TABLE {COLL}_ctx_segments ENABLE ROW LEVEL SECURITY")
        conn.execute(f"DROP POLICY IF EXISTS hide_early ON {COLL}_ctx_segments")
        conn.execute(f"CREATE POLICY hide_early ON {COLL}_ctx_segments TO lvl_reader "
                     "USING (recording_id NOT IN ('e0/V','e1/V','e3/V'))")
        with conn.transaction():
            conn.execute("SET LOCAL ROLE lvl_reader")
            seen = [r[0] for r in conn.execute("SELECT occurrence->>'episode' FROM morlet_level_track(%s,%s,%s,%s)",
                                               (COLL, seg, Q, R)).fetchall()]
        owner = [r["occurrence"]["episode"] for r in call(conn, seg)]
        conn.execute(f"ALTER TABLE {COLL}_ctx_segments DISABLE ROW LEVEL SECURITY")
        hidden = {"e0", "e1", "e3"}
        rls_ok = not (hidden & set(seen)) and set(owner) - set(seen) == hidden & set(owner)
        report["checks"]["access and row-level security"] = {"refused_without_grant": refused,
                                                             "owner_episodes": len(owner), "reader_episodes": len(seen),
                                                             "pass": bool(refused and rls_ok)}
    report["pass"] = all(c["pass"] for c in report["checks"].values())
    text = json.dumps(report, indent=1)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n")
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
