#!/usr/bin/env python3
"""C7 product check (PROTOCOL.md, "Product check"): H-PIN through pg_morlet's public API.

  sql_check.py export DATA_DIR OUT   (compression environment: numpy; python -I)
  sql_check.py synth OUT             synthetic snapshots in the same format, to test the check itself
  sql_check.py check OUT             (seismic environment: numpy, psycopg; MORLET_DSN)

export takes Bearing1_1, Bearing2_1 and Bearing3_1 (one per condition) and, for each, the 30 pinned
snapshots plus snapshots 30, the middle one and the last one. Queries: snapshots 0 and 29 (pinned,
scored against the other 29) and the three later ones (scored against the pin). check loads each
snapshot as its own acquisition (origin_utc = snapshot time, one raw-only recording, context key
condition, occurrence = snapshot); pinned recordings carry the metadata {"c7_pin": true}. It
compares morlet_compare_summary(..., metadata_filter => '{"c7_pin": true}', before_only => false,
min_history => 5) with the harness's H-PIN score.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c7  # noqa: E402
c5 = c7.c5

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_c7_check")
COLL = "c7chk"
BEARINGS = ("Bearing1_1", "Bearing2_1", "Bearing3_1")
CHUNK = 32768  # morlet_append_chunk accepts 1..32768 samples
PIN_FILTER = {"c7_pin": True}


def picks(n):
    return sorted(set(range(c7.PIN)) | {c7.PIN, n // 2, n - 1}), [0, c7.PIN - 1, c7.PIN, n // 2, n - 1]


def write(out, arrays, meta):
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "check.npz", **arrays)
    (out / "check.json").write_text(json.dumps(meta, indent=1))
    print({b: m["queries"] for b, m in meta.items()})


def expected(values, kept, queries):
    """Harness H-PIN scores of the queries. values are the kept snapshots' rows; the pin is the first 30
    of them, so H-PIN on the kept rows equals H-PIN on the whole bearing."""
    score, _ = c7.pinned_scores(values, c5.SCORE)
    return {str(q): float(score[kept.index(q)]) for q in queries}


def export(data_dir, out):
    arrays, meta = {}, {}
    for b in BEARINGS:
        files = c7.snapshot_files(c7.bearing_dir(Path(data_dir), b))
        kept, queries = picks(len(files))
        xs = {i: c7.load_snapshot(files[i])[0] for i in kept}
        vals = np.array([c5.simple_values(xs[i], c7.RATE) for i in kept])
        for i in kept:
            arrays[f"{b}|{i}"] = xs[i]
        meta[b] = {"rate": c7.RATE, "snapshots": kept, "queries": queries,
                   "expected": expected(vals, kept, queries)}
    write(Path(out), arrays, meta)


def synth(out):
    """Pinned noise, then a louder, more impulsive tail: the harness and the product must agree on it."""
    rng = np.random.default_rng(7)
    arrays, meta = {}, {}
    for k, b in enumerate(BEARINGS):
        n = 80 + 20 * k
        kept, queries = picks(n)
        vals = []
        for i in kept:
            x = rng.standard_normal(c7.SAMPLES) * (1.0 if i < c7.PIN else 1.4)
            if i >= c7.PIN:
                x[rng.integers(0, c7.SAMPLES, 40)] *= 6
            arrays[f"{b}|{i}"] = x
            vals.append(c5.simple_values(x, c7.RATE))
        meta[b] = {"rate": c7.RATE, "snapshots": kept, "queries": queries,
                   "expected": expected(np.array(vals), kept, queries)}
    write(Path(out), arrays, meta)


def check(out):
    import psycopg
    out = Path(out)
    meta = json.loads((out / "check.json").read_text())
    data = np.load(out / "check.npz")
    report = []
    with psycopg.connect(DSN, autocommit=True) as conn:
        for b, m in meta.items():
            for t in ("_acq_channels", "_acq", "_labels", "_ctx_values", "_ctx_segments", "_ctx", "_chunks",
                      "_segments", ""):
                conn.execute(f"DROP TABLE IF EXISTS {COLL}{t} CASCADE")
            conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLL,))
            for enable in ("morlet_enable_context", "morlet_enable_acquisitions"):
                conn.execute(f"SELECT {enable}(%s)", (COLL,))
            with conn.transaction():
                for i in m["snapshots"]:
                    acq, rid = f"{b}#{i}", f"{b}#{i}/H"
                    x = data[f"{b}|{i}"]
                    md = json.dumps(PIN_FILTER if i < c7.PIN else {})
                    conn.execute("SELECT morlet_create_acquisition(%s,%s,%s,to_timestamp(%s))",
                                 (COLL, acq, b, i * c7.INTERVAL_S))
                    conn.execute("SELECT morlet_start_recording(%s,%s,%s,'H','g',%s::jsonb,512,256,'zscore','none')",
                                 (COLL, rid, m["rate"], md))
                    for s in range(0, len(x), CHUNK):
                        conn.execute("SELECT morlet_append_chunk(%s,%s,%s,%s::float8[])",
                                     (COLL, rid, s, x[s:s + CHUNK].tolist()))
                    conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,'H')", (COLL, acq, rid))
                    end_ns = int(round(len(x) / m["rate"] * 1e9))
                    rows = [{"key": k, "value": v, "t_start_ns": 0, "t_end_ns": end_ns}
                            for k, v in (("condition", b[7]), ("snapshot", str(i)))]
                    conn.execute("SELECT morlet_add_acquisition_context(%s,%s,'c7',%s)", (COLL, acq, json.dumps(rows)))
                    conn.execute("SELECT morlet_build_acquisition_segments(%s,%s,ARRAY['condition'],ARRAY['snapshot'])",
                                 (COLL, acq))
            worst, ok, n_hist = 0.0, True, {}
            for q in m["queries"]:
                seg = conn.execute(f"SELECT segment_id FROM {COLL}_ctx_segments WHERE recording_id=%s",
                                   (f"{b}#{q}/H",)).fetchone()[0]
                score, status, hist_n = conn.execute(
                    "SELECT score, status, hist_n FROM morlet_compare_summary(%s,%s,metadata_filter=>%s::jsonb,"
                    "before_only=>false,min_history=>5)", (COLL, seg, json.dumps(PIN_FILTER))).fetchone()
                exp = m["expected"][str(q)]
                ok &= status == "ok" and hist_n == (c7.PIN - 1 if q < c7.PIN else c7.PIN)
                n_hist[str(q)] = hist_n
                worst = max(worst, abs(score - exp) / max(abs(exp), 1e-300))
            report.append({"bearing": b, "queries": m["queries"], "history_n": n_hist,
                           "max_relative_score_error": float(worst), "pass": bool(ok and worst <= 1e-9)})
    (out / "sql_check.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    sys.exit(0 if all(r["pass"] for r in report) else 1)


if __name__ == "__main__":
    {"export": lambda: export(Path(sys.argv[2]), sys.argv[3]),
     "synth": lambda: synth(sys.argv[2]),
     "check": lambda: check(sys.argv[2])}[sys.argv[1]]()
