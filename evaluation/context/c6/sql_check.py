#!/usr/bin/env python3
"""C6 product check (PROTOCOL.md, "Product check"): resemblance from pg_morlet's own distances.

  sql_check.py export DATA_DIR C5_OUT OUT   (compression environment: numpy, scipy; python -I; reads C5_OUT/values)
  sql_check.py check OUT                    (seismic environment: numpy, psycopg; MORLET_DSN)

export takes two primary bearings, B03 (128 kHz) and B10 (64 kHz); a bearing with no scored
measurement is replaced by the next primary bearing of the same rate. For each, it writes channel A
of the 150 library measurements and of three scored measurements (first, middle, last), with times
and contexts, to OUT/check.npz and OUT/check.json. check loads, per query, the library plus that
query into a fresh collection (one acquisition per measurement, as C5, plus a context key
`bearing`, so that matching on it alone searches all conditions) and compares
morlet_history_neighbors(..., match_keys => ['bearing'], before_only => true, top_k => 1000)
distances, and the resemblance S
computed from them, with the harness.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c6  # noqa: E402
from c6 import c5  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_c6_check")
COLL = "c6chk"
CHUNK = 65536


def scored(d):
    v, ctx = d["values"][0], d["context"]
    lib, lc = v[:c6.LIBRARY], ctx[:c6.LIBRARY]
    sc = c6.scales(lib)
    out = {}
    for i in range(c6.LIBRARY, len(v)):
        r = c6.resemblance(v[i], ctx[i], lib, lc, sc)
        if r:
            out[i] = r["S"]
    return out


def export(data_dir, c5_out, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    arrays, meta = {}, {}
    for first in ("B03", "B10"):
        rate = c5.rate_hz(first)
        for b in [first] + [p for p in c5.PRIMARY if p > first and c5.rate_hz(p) == rate]:
            d = dict(np.load(Path(c5_out) / "values" / f"{b}.npz"))
            s = scored(d)
            if s:
                break
        else:
            sys.exit(f"no scored measurement at {rate} Hz")
        keys = sorted(s)
        queries = sorted({keys[0], keys[len(keys) // 2], keys[-1]})
        files = c5.measurement_files(Path(data_dir), b)
        for i in list(range(c6.LIBRARY)) + queries:
            arrays[f"{b}|{i}"] = c5.load_mat(files[i])[0]
        meta[b] = {"rate": rate, "queries": queries, "expected_S": {str(q): s[q] for q in queries},
                   "t": d["t"].tolist(), "context": d["context"].tolist()}
    np.savez_compressed(out / "check.npz", **arrays)
    (out / "check.json").write_text(json.dumps(meta))
    print({b: m["queries"] for b, m in meta.items()})


def load(conn, b, m, data, indices):
    for t in ("_acq_channels", "_acq", "_labels", "_ctx_values", "_ctx_segments", "_ctx", "_chunks", "_segments", ""):
        conn.execute(f"DROP TABLE IF EXISTS {COLL}{t} CASCADE")
    conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLL,))
    for enable in ("morlet_enable_context", "morlet_enable_acquisitions"):
        conn.execute(f"SELECT {enable}(%s)", (COLL,))
    with conn.transaction():
        for i in indices:
            acq, rid = f"{b}#{i}", f"{b}#{i}/A"
            x = data[f"{b}|{i}"]
            conn.execute("SELECT morlet_create_acquisition(%s,%s,%s,to_timestamp(%s))", (COLL, acq, b, m["t"][i]))
            conn.execute("SELECT morlet_start_recording(%s,%s,%s,'A','V','{}',512,256,'zscore','none')",
                         (COLL, rid, m["rate"]))
            for s in range(0, len(x), CHUNK):
                conn.execute("SELECT morlet_append_chunk(%s,%s,%s,%s::float8[])", (COLL, rid, s, x[s:s + CHUNK].tolist()))
            conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,'A')", (COLL, acq, rid))
            end_ns = int(round(len(x) / m["rate"] * 1e9))
            speed, stat, dyn, exc = m["context"][i].strip("()").replace("'", "").split(", ")
            rows = [{"key": k, "value": v, "t_start_ns": 0, "t_end_ns": end_ns}
                    for k, v in (("bearing", b), ("speed", speed), ("static", stat), ("dynamic", dyn),
                                 ("excitation", exc), ("measurement", str(i)))]
            conn.execute("SELECT morlet_add_acquisition_context(%s,%s,'c6',%s)", (COLL, acq, json.dumps(rows)))
            conn.execute("SELECT morlet_build_acquisition_segments(%s,%s,"
                         "ARRAY['bearing','speed','static','dynamic','excitation'],ARRAY['measurement'])", (COLL, acq))


def check(out):
    import psycopg
    out = Path(out)
    meta = json.loads((out / "check.json").read_text())
    data = np.load(out / "check.npz")
    report = []
    with psycopg.connect(DSN, autocommit=True) as conn:
        for b, m in meta.items():
            ctx = np.array(m["context"])
            for q in m["queries"]:
                load(conn, b, m, data, list(range(c6.LIBRARY)) + [q])
                seg = conn.execute(f"SELECT segment_id FROM {COLL}_ctx_segments WHERE recording_id=%s",
                                   (f"{b}#{q}/A",)).fetchone()[0]
                got = conn.execute("SELECT recording_id, distance, vals FROM morlet_history_neighbors(%s,%s,"
                                   "match_keys=>ARRAY['bearing'],before_only=>true,top_k=>1000)", (COLL, seg)).fetchall()
                idx = [int(r.split("#")[1].split("/")[0]) for r, _, _ in got]
                lib = np.array([vals for _, _, vals in got])
                qv = np.array(conn.execute("SELECT vals FROM morlet_comparable_segment(%s,%s)",
                                           (COLL, seg)).fetchone()[0])
                expected = c6.distances(qv, lib, c6.scales(lib))
                sql_d = np.array([dist for _, dist, _ in got])
                worst = float(np.max(np.abs(sql_d - expected) / np.maximum(np.abs(expected), 1e-300)))
                # S from the product's distances and the library contexts
                D = {}
                for c in sorted(set(ctx[idx].tolist())):
                    sel = ctx[idx] == c
                    if sel.sum() >= c6.MIN_MEMBERS:
                        D[c] = float(np.median(sql_d[sel]))
                own = ctx[q]
                s_sql = float(np.log(D[own] / min(v for c, v in D.items() if c != own)))
                exp_s = m["expected_S"][str(q)]
                report.append({"bearing": b, "query": q, "library": len(got),
                               "same_library": sorted(idx) == list(range(c6.LIBRARY)),
                               "max_relative_distance_error": worst, "S": s_sql, "S_harness": exp_s,
                               "pass": bool(sorted(idx) == list(range(c6.LIBRARY)) and worst <= 1e-9
                                            and abs(s_sql - exp_s) <= 1e-9)})
    (out / "sql_check.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    sys.exit(0 if all(r["pass"] for r in report) else 1)


if __name__ == "__main__":
    if sys.argv[1] == "export":
        export(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        check(sys.argv[2])
