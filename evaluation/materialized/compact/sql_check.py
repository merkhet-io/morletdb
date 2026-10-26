#!/usr/bin/env python3
"""Engineering check: pg_morlet's materialized envelopes (SQL API) vs the evaluated Python f16.

Loads a period's windows into a fresh encoded collection (raw only), builds envelopes with
morlet_build_envelopes, then for every R1 query calls morlet_search_envelopes (same station via
the metadata filter, top 100) and applies the R1 gallery exclusion client-side. The top 10 is
compared with the f16 ranking of R1b (evaluation/materialized/compact/compact.py on the R1b branch) computed in Python on the same windows.

This reuses an already-consumed period; it checks the implementation, not the method.
Usage: sql_check.py YEAR WINDOWS_DIR OUT  (seismic environment; MORLET_DSN for the database)
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent), str(HERE.parent.parent / "decision")]
import r1  # noqa: E402
import timing  # noqa: E402


def f16_stored(env):
    """The evaluated f16 representation (R1b compact.py: enc_f16 then dec_f16)."""
    v = env.astype("<f2").astype(float)
    s = v.std()
    return (v - v.mean()) / (s if s > 0 else 1.0)

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_envelope_check")


def main():
    year, run_dir, out = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    w = np.load(run_dir / f"windows-{year}.npz")
    x = w["x"]
    meta = {k: w[k] for k in w.files if k != "x"}
    ids = np.array([f"w{i:05d}" for i in range(len(x))])
    coll = f"envcheck_{year}"
    report = {"year": year, "windows": len(x), "provenance": r1.provenance()}
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {coll}_envelopes, {coll}_chunks, {coll}_segments, {coll}")
        conn.execute("SELECT morlet_create_stream_collection('public',%s)", (coll,))
        conn.execute("SELECT morlet_enable_envelopes(%s)", (coll,))
        with conn.transaction():
            for i in range(len(x)):
                m = {k: (v[i].item() if hasattr(v[i], "item") else v[i]) for k, v in meta.items()}
                m = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in m.items()}
                conn.execute("SELECT morlet_start_recording(%s,%s,100,'HHZ','counts',%s,512,256,'zscore','none')",
                             (coll, ids[i], json.dumps(m)))
                conn.execute("SELECT morlet_append_chunk(%s,%s,0,%s::float8[])", (coll, ids[i], x[i].tolist()))
        t0 = time.perf_counter()
        built = sum(conn.execute("SELECT morlet_build_envelopes(%s,%s,4096,4096)", (coll, r)).fetchone()[0] for r in ids)
        report["build"] = {"windows": built, "ms_per_window": 1e3 * (time.perf_counter() - t0) / len(ids)}
        conn.execute(f"VACUUM ANALYZE {coll}_envelopes")
        report["storage"] = dict(zip(
            ("chunk_table_bytes", "envelope_table_bytes"),
            conn.execute("SELECT chunk_table_bytes,envelope_table_bytes FROM morlet_collection_storage(%s)",
                         (coll,)).fetchone()))
        report["storage"]["envelope_fraction_of_chunks"] = (report["storage"]["envelope_table_bytes"]
                                                            / report["storage"]["chunk_table_bytes"])
        ev = np.where(meta["kind"] == "event")[0]
        mask = timing.gallery_mask(meta, ev, np.arange(len(ids)), True)
        queries = [(q, set(ids[np.where(mask[i])[0]])) for i, q in enumerate(ev) if mask[i].sum() >= timing.TOP_K]
        # Python reference: compact's f16 representation, scored exactly as in R1b.
        stored = {ids[i]: f16_stored(timing.envelope(x[i])) for i in range(len(x))}
        same_set = same_order = 0
        short, ms = 0, []
        worst = 0.0
        for q, elig in queries:
            t1 = time.perf_counter()
            rows = conn.execute("SELECT recording_id,distance FROM morlet_search_envelopes(%s,%s,100,'HHZ','counts',%s,100)",
                                (coll, x[q].tolist(), json.dumps({"station": str(meta["station"][q])}))).fetchall()
            ms.append(1e3 * (time.perf_counter() - t1))
            got = [(r, d) for r, d in rows if r in elig][:timing.TOP_K]
            if len(got) < timing.TOP_K:
                short += 1
            cand = sorted(elig)
            qe = timing.envelope(x[q])[None]
            d = timing.xcorr_distances(qe, np.array([stored[c] for c in cand]), 30)[0]
            ref = r1.topk(cand, d)
            dist = dict(zip(cand, d))
            worst = max(worst, max(abs(dist[r] - dd) for r, dd in got))
            same_set += {r for r, _ in got} == set(ref)
            same_order += [r for r, _ in got] == ref
        report["queries"] = len(queries)
        report["agreement_with_python_f16"] = {"identical_set": same_set / len(queries),
                                               "identical_order": same_order / len(queries),
                                               "queries_with_fewer_than_10_eligible_in_top100": short,
                                               "max_abs_distance_difference": worst}
        report["search_latency_ms"] = {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95)),
                                       "note": "morlet_search_envelopes, same-station filter, top 100, server-side scoring"}
    (out / f"sql-check-{year}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "provenance"}, indent=1))


if __name__ == "__main__":
    main()
