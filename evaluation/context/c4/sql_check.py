#!/usr/bin/env python3
"""C4 product check (PROTOCOL.md, "Product check"): three queries through pg_morlet's public API.

  sql_check.py export DATASET_DIR C4_JSON OUT   (compression environment: numpy, pyarrow; python -I)
  sql_check.py check OUT                        (seismic environment: numpy, psycopg; MORLET_DSN)

export picks the queries (for each of the three largest eligible classes, the earliest query
window that is the first window of its instance), and writes the raw samples of each query and
its same-context references to OUT/check.npz with OUT/check.json. check loads every window as its
own acquisition (origin_utc = window start, one raw-only recording per channel, context
state + instance as occurrence) and compares morlet_history_neighbors, per channel, with the
harness's single-channel distances.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c4  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_c4_check")
COLL = "c4chk"


def export(dataset_dir, c4_json, out):
    import pyarrow.parquet as pq
    res = json.loads(Path(c4_json).read_text())
    rows = res["rows"]
    counts = Counter(r["label"] for r in rows)
    classes = sorted(res["eligible_classes"], key=lambda c: (-counts[c], c))[:3]
    windows, _, _ = c4.load(dataset_dir)
    first = {}
    for w in windows:
        first.setdefault(w["instance"], w["start"])
    picks = []
    for c in classes:
        cand = [r for r in rows if r["label"] == c and first[r["instance"]] == r["start"]]
        picks.append(min(cand, key=lambda r: (r["start"], r["instance"])))
    needed, meta = {}, {"queries": []}
    for q in picks:
        refs = [w for w in windows if w["label"] is not None and w["instance"] != q["instance"]
                and w["start"] < q["start"] and w["context"] == q["context"]]
        ids = [(q["instance"], q["start"])] + [(w["instance"], w["start"]) for w in refs]
        meta["queries"].append({"label": q["label"], "context": q["context"], "windows": [list(i) for i in ids]})
        for inst, start in ids:
            needed.setdefault(inst, set()).add(start)
    arrays = {}
    for inst, starts in needed.items():
        t = pq.read_table(Path(dataset_dir) / f"{inst}.parquet")
        names = t.column_names
        ts_name = "timestamp" if "timestamp" in names else next(n for n in names if n.startswith("__index"))
        ts = t.column(ts_name).cast("timestamp[s]").to_numpy().astype("datetime64[s]").astype(np.int64)
        for start in starts:
            i = int(np.flatnonzero(ts == start)[0])
            for ch in c4.CHANNELS:
                if ch in names:
                    x = t.column(ch).to_numpy(zero_copy_only=False).astype(float)[i:i + c4.WINDOW]
                    if c4.channel_values(x) is not None:
                        arrays[f"{inst}|{start}|{ch}"] = x
    Path(out).mkdir(parents=True, exist_ok=True)
    np.savez_compressed(Path(out) / "check.npz", **arrays)
    (Path(out) / "check.json").write_text(json.dumps(meta, indent=1))
    print({"queries": [(q["label"], q["instance"], q["start"]) for q in picks], "arrays": len(arrays)})


def harness_channel(qx, refs):
    """Single-channel distances as c4.neighbours restricted to one channel (no shared-channel minimum)."""
    V = np.array([c4.channel_values(x) for x in refs.values()])
    qv = np.array(c4.channel_values(qx))
    med = np.median(V, axis=0)
    use = med != 0
    d = np.sqrt(np.mean(((qv[use] - V[:, use]) / np.abs(med[use])) ** 2, axis=1))
    return dict(zip(refs, d))


def check(out):
    import psycopg
    meta = json.loads((Path(out) / "check.json").read_text())
    data = np.load(Path(out) / "check.npz")
    by_window = {}
    for key in data.files:
        inst, start, ch = key.split("|")
        by_window.setdefault((inst, int(start)), {})[ch] = data[key]
    report = []
    with psycopg.connect(DSN, autocommit=True) as conn:
        for qm in meta["queries"]:
            for t in ("_acq_channels", "_acq", "_labels", "_ctx_values", "_ctx_segments", "_ctx", "_chunks",
                      "_segments", ""):
                conn.execute(f"DROP TABLE IF EXISTS {COLL}{t} CASCADE")
            conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLL,))
            for enable in ("morlet_enable_context", "morlet_enable_acquisitions"):
                conn.execute(f"SELECT {enable}(%s)", (COLL,))
            with conn.transaction():
                for inst, start in map(tuple, qm["windows"]):
                    acq = f"{inst}#{start}"
                    conn.execute("SELECT morlet_create_acquisition(%s,%s,%s,to_timestamp(%s))",
                                 (COLL, acq, inst.split("/")[1][:10], start))
                    for ch, x in by_window.get((inst, start), {}).items():
                        rid = f"{acq}/{ch}"
                        conn.execute("SELECT morlet_start_recording(%s,%s,1.0,%s,'si','{}',512,256,'zscore','none')",
                                     (COLL, rid, ch))
                        conn.execute("SELECT morlet_append_chunk(%s,%s,0,%s::float8[])", (COLL, rid, x.tolist()))
                        conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,%s)", (COLL, acq, rid, ch))
                    end_ns = c4.WINDOW * 10 ** 9
                    rows = [{"key": "state", "value": qm["context"], "t_start_ns": 0, "t_end_ns": end_ns},
                            {"key": "instance", "value": inst, "t_start_ns": 0, "t_end_ns": end_ns}]
                    conn.execute("SELECT morlet_add_acquisition_context(%s,%s,'c4',%s)", (COLL, acq, json.dumps(rows)))
                    conn.execute("SELECT morlet_build_acquisition_segments(%s,%s,ARRAY['state'],ARRAY['instance'])",
                                 (COLL, acq))
            qinst, qstart = map(str, qm["windows"][0])
            worst, order_ok, n_ch = 0.0, True, 0
            for ch, qx in by_window[(qinst, int(qstart))].items():
                refs = {f"{i}#{s}/{ch}": w[ch] for (i, s), w in by_window.items()
                        if ch in w and (i, s) != (qinst, int(qstart))}
                if not refs:
                    continue
                expected = harness_channel(qx, refs)
                seg = conn.execute(f"SELECT segment_id FROM {COLL}_ctx_segments WHERE recording_id=%s",
                                   (f"{qinst}#{qstart}/{ch}",)).fetchone()[0]
                got = conn.execute("SELECT recording_id, distance FROM morlet_history_neighbors(%s,%s,ARRAY['state'],"
                                   "before_only=>true,top_k=>1000,score_values=>ARRAY['std','crest','kurtosis','p75'])",
                                   (COLL, seg)).fetchall()
                if {r for r, _ in got} != set(expected):
                    order_ok = False
                for r, d in got:
                    if r in expected:
                        worst = max(worst, abs(d - expected[r]) / max(abs(expected[r]), 1e-300))
                exp_order = sorted(expected, key=lambda r: (expected[r], r))
                order_ok &= [r for r, _ in got] == exp_order
                n_ch += 1
            report.append({"label": qm["label"], "query": f"{qinst}#{qstart}", "references": len(qm["windows"]) - 1,
                           "channels": n_ch, "max_relative_distance_error": worst, "same_ids_and_order": order_ok,
                           "pass": order_ok and worst <= 1e-9})
    (Path(out) / "sql_check.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    sys.exit(0 if all(r["pass"] for r in report) else 1)


if __name__ == "__main__":
    if sys.argv[1] == "export":
        export(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        check(sys.argv[2])
