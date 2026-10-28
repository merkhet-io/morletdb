#!/usr/bin/env python3
"""C5 product check (PROTOCOL.md, "Product check"): H-CTX through pg_morlet's public API.

  sql_check.py export DATA_DIR RUN/check   (compression environment: numpy, scipy; python -I; reads RUN/values)
  sql_check.py check RUN/check             (seismic environment: numpy, psycopg; MORLET_DSN)

export takes, for B02, B03 and B10 (128 kHz and 64 kHz, vibration- and temperature-ended), the
first 3 measurements that H-CTX scores, and writes channel A of every measurement up to the last
of them, with times and contexts, to OUT/check.npz and OUT/check.json. check loads each
measurement as its own acquisition (origin_utc = measurement time, one raw-only recording,
context keys speed/static/dynamic/excitation, occurrence = measurement) and compares
morlet_compare_summary(..., before_only => true, min_history => 5) with the harness score.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c5  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_c5_check")
COLL = "c5chk"
BEARINGS = ("B02", "B03", "B10")
QUERIES = 3
CHUNK = 65536


def export(data_dir, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    arrays, meta = {}, {}
    for b in BEARINGS:
        d = dict(np.load(out.parent / "values" / f"{b}.npz"))
        score, _ = c5.history_scores(d["values"][0], d["context"], c5.SCORE, True)
        queries = [int(i) for i in np.flatnonzero(np.isfinite(score))[:QUERIES]]
        files = c5.measurement_files(Path(data_dir), b)
        last = queries[-1]
        for i in range(last + 1):
            arrays[f"{b}|{i}"] = c5.load_mat(files[i])[0]
        meta[b] = {"rate": float(d["rate"][0]), "queries": queries,
                   "expected": {str(i): float(score[i]) for i in range(last + 1)},
                   "t": d["t"][:last + 1].tolist(), "context": d["context"][:last + 1].tolist()}
    np.savez_compressed(out / "check.npz", **arrays)
    (out / "check.json").write_text(json.dumps(meta, indent=1))
    print({b: (m["queries"], len(m["t"])) for b, m in meta.items()})


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
                for i, (t, ctx) in enumerate(zip(m["t"], m["context"])):
                    acq, rid = f"{b}#{i}", f"{b}#{i}/A"
                    x = data[f"{b}|{i}"]
                    conn.execute("SELECT morlet_create_acquisition(%s,%s,%s,to_timestamp(%s))", (COLL, acq, b, t))
                    conn.execute("SELECT morlet_start_recording(%s,%s,%s,'A','V','{}',512,256,'zscore','none')",
                                 (COLL, rid, m["rate"]))
                    for s in range(0, len(x), CHUNK):
                        conn.execute("SELECT morlet_append_chunk(%s,%s,%s,%s::float8[])",
                                     (COLL, rid, s, x[s:s + CHUNK].tolist()))
                    conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,'A')", (COLL, acq, rid))
                    end_ns = int(round(len(x) / m["rate"] * 1e9))
                    speed, stat, dyn, exc = ctx.strip("()").replace("'", "").split(", ")
                    rows = [{"key": k, "value": v, "t_start_ns": 0, "t_end_ns": end_ns}
                            for k, v in (("speed", speed), ("static", stat), ("dynamic", dyn), ("excitation", exc),
                                         ("measurement", str(i)))]
                    conn.execute("SELECT morlet_add_acquisition_context(%s,%s,'c5',%s)", (COLL, acq, json.dumps(rows)))
                    conn.execute("SELECT morlet_build_acquisition_segments(%s,%s,"
                                 "ARRAY['speed','static','dynamic','excitation'],ARRAY['measurement'])", (COLL, acq))
            worst, statuses_ok = 0.0, True
            for i in range(len(m["t"])):
                seg = conn.execute(f"SELECT segment_id FROM {COLL}_ctx_segments WHERE recording_id=%s",
                                   (f"{b}#{i}/A",)).fetchone()[0]
                score, status = conn.execute("SELECT score, status FROM morlet_compare_summary(%s,%s,"
                                             "before_only=>true,min_history=>5)", (COLL, seg)).fetchone()
                exp = m["expected"][str(i)]
                if np.isnan(exp):
                    statuses_ok &= status == "insufficient_history"
                else:
                    statuses_ok &= status == "ok"
                    worst = max(worst, abs(score - exp) / max(abs(exp), 1e-300))
            report.append({"bearing": b, "queries": m["queries"], "segments": len(m["t"]),
                           "max_relative_score_error": float(worst), "same_scored_segments": bool(statuses_ok),
                           "pass": bool(statuses_ok and worst <= 1e-9)})
    (out / "sql_check.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    sys.exit(0 if all(r["pass"] for r in report) else 1)


if __name__ == "__main__":
    if sys.argv[1] == "export":
        export(Path(sys.argv[2]), sys.argv[3])
    else:
        check(sys.argv[2])
