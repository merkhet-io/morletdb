#!/usr/bin/env python3
"""R1: materialized envelopes next to compressed raw recordings (PROTOCOL.md).

  load   store a period's windows in an encoded pg_morlet collection, derive and store the
         envelope and ST representations, measure generation and update cost
  run    query methods E (exhaustive raw), M (materialized), C (cascade) and report
         storage, data access, latency, agreement with E and task relevance

Run with the seismic environment (numpy, scipy, psycopg). Windows come from the timing
test's pipeline (evaluation/decision/timing.py: catalog, plan, fetch, prepare).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "decision")]
import timing  # noqa: E402
from retrieval import baseline_features  # noqa: E402

REPRESENTATION = "xc-envelope-v1"
N_GRID = (20, 50, 100, 200)
RECALL_BAR = 0.95
DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_r1")


def names(year):
    t = f"r1_y{year}"
    return t, f"{t}_chunks", f"{t}_envelopes", f"{t}_st"


def st_vector(x):
    return np.hstack([timing.spectrogram_bins(x, timing.RATE),
                      baseline_features(x, timing.RATE, "zscore")["statistics"]])


def provenance():
    def git(*a):
        r = subprocess.run(["git", *a], capture_output=True, text=True, cwd=HERE)
        return r.stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
            "machine": platform.machine(), "os": platform.platform(),
            "cpu": subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()}


def load(year, run_dir: Path, out: Path):
    w = np.load(run_dir / f"windows-{year}.npz")
    x = w["x"]
    if not np.array_equal(x, np.round(x)):
        raise ValueError("windows are not integer counts")
    meta = {k: w[k] for k in w.files if k != "x"}
    t, chunks, env_t, st_t = names(year)
    report = {"year": year, "windows": len(x), "provenance": provenance()}
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {env_t}, {st_t}, {chunks}, {t}_segments, {t}")
        conn.execute("SELECT morlet_create_stream_collection('public',%s)", (t,))
        t0 = time.perf_counter()
        with conn.transaction():
            for i in range(len(x)):
                m = {k: (v[i].item() if hasattr(v[i], "item") else v[i]) for k, v in meta.items()}
                m = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in m.items()}
                conn.execute("SELECT morlet_start_recording(%s,%s,100,'HHZ','counts',%s,2048,512,'zscore',"
                             "'morlet-energy-lowrate-v1',1)", (t, f"w{i:05d}", json.dumps(m)))
                conn.execute("SELECT morlet_append_chunk(%s,%s,0,%s::float8[])", (t, f"w{i:05d}", x[i].tolist()))
        report["raw_append_s"] = time.perf_counter() - t0
        conn.execute(f"CREATE TABLE {env_t} (recording_id text PRIMARY KEY REFERENCES {t} ON DELETE CASCADE,"
                     " representation text NOT NULL, envelope real[] NOT NULL)")
        conn.execute(f"CREATE TABLE {st_t} (recording_id text PRIMARY KEY REFERENCES {t} ON DELETE CASCADE,"
                     " st real[] NOT NULL)")
        # Generation reads raw back from the database, as a backfill would.
        cost = {"read_s": 0.0, "envelope_cpu_s": 0.0, "st_cpu_s": 0.0, "insert_s": 0.0}
        for i in range(len(x)):
            rid = f"w{i:05d}"
            t1 = time.perf_counter()
            raw = np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s,%s,0,4096)", (t, rid),
                                          binary=True).fetchone()[0])
            t2 = time.perf_counter()
            env = timing.envelope(raw)
            t3 = time.perf_counter()
            st = st_vector(raw)
            t4 = time.perf_counter()
            with conn.transaction():
                conn.execute(f"INSERT INTO {env_t} VALUES (%s,%s,%s::real[])", (rid, REPRESENTATION, env.tolist()))
                conn.execute(f"INSERT INTO {st_t} VALUES (%s,%s::real[])", (rid, st.tolist()))
            t5 = time.perf_counter()
            cost["read_s"] += t2 - t1
            cost["envelope_cpu_s"] += t3 - t2
            cost["st_cpu_s"] += t4 - t3
            cost["insert_s"] += t5 - t4
        report["generation_ms_per_window"] = {k: 1e3 * v / len(x) for k, v in cost.items()}
        # Update cost: one new window = append raw + derive + insert, timed for 200 windows.
        conn.execute("DROP TABLE IF EXISTS r1_update_chunks, r1_update_segments, r1_update_envelopes, r1_update")
        conn.execute("SELECT morlet_create_stream_collection('public','r1_update')")
        conn.execute("CREATE TABLE r1_update_envelopes (recording_id text PRIMARY KEY, representation text, envelope real[])")
        per = []
        for i in range(min(200, len(x))):
            t1 = time.perf_counter()
            with conn.transaction():
                conn.execute("SELECT morlet_start_recording('r1_update',%s,100,'HHZ','counts','{}',2048,512,'zscore',"
                             "'morlet-energy-lowrate-v1',1)", (f"u{i}",))
                conn.execute("SELECT morlet_append_chunk('r1_update',%s,0,%s::float8[])", (f"u{i}", x[i].tolist()))
                conn.execute("INSERT INTO r1_update_envelopes VALUES (%s,%s,%s::real[])",
                             (f"u{i}", REPRESENTATION, timing.envelope(x[i]).tolist()))
            per.append(1e3 * (time.perf_counter() - t1))
        report["update_ms_per_window"] = {"p50": float(np.percentile(per, 50)), "p95": float(np.percentile(per, 95)), "n": len(per)}
        conn.execute("DROP TABLE r1_update_envelopes, r1_update_chunks, r1_update_segments, r1_update")
        for rel in (chunks, env_t, st_t, f"{t}_segments", t):
            conn.execute(f"VACUUM ANALYZE {rel}")
        size = {rel: conn.execute("SELECT pg_total_relation_size(%s::regclass)", (rel,)).fetchone()[0]
                for rel in (t, chunks, f"{t}_segments", env_t, st_t)}
        report["storage_bytes"] = size
        report["original_float64_bytes"] = int(x.size * 8)
        report["derived_fraction_of_raw_chunks"] = {"envelopes": size[env_t] / size[chunks], "st": size[st_t] / size[chunks]}
    (out / f"load-{year}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "provenance"}, indent=1))


def fetch_arrays(conn, sql, ids):
    rows = conn.execute(sql, (ids,), binary=True).fetchall()
    return {r[0]: np.asarray(r[1], float) for r in rows}


def topk(ids, dist, k=timing.TOP_K):
    order = np.lexsort((np.asarray(ids), dist))
    return [ids[j] for j in order[:k]]


def run(year, out: Path, dev_year=2023, n_fixed=None):
    t, chunks, env_t, st_t = names(year)
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(f"SELECT recording_id, metadata FROM {t} ORDER BY recording_id").fetchall()
        ids = np.array([r[0] for r in rows])
        meta = {}
        for k in rows[0][1]:
            vals = [r[1][k] for r in rows]
            numeric = all(v is None or isinstance(v, (int, float)) for v in vals)
            meta[k] = np.array([np.nan if v is None else v for v in vals], float) if numeric else np.array(vals)
        # ST standardization uses development-period statistics only.
        _, _, _, dev_st = names(dev_year)
        dev = np.array([r[0] for r in conn.execute(f"SELECT st FROM {dev_st}").fetchall()], float)
        mu, sd = dev.mean(0), dev.std(0)
        sd[sd == 0] = 1
        ev = np.where(meta["kind"] == "event")[0]
        mask = timing.gallery_mask(meta, ev, np.arange(len(ids)), True)
        queries = [(q, list(ids[np.where(mask[i])[0]])) for i, q in enumerate(ev) if mask[i].sum() >= timing.TOP_K]
        hypo = dict(zip(ids, meta["hypo_km"].astype(float)))
        n_list = N_GRID if n_fixed is None else (n_fixed,)
        methods = ["E", "M"] + [f"C{n}" for n in n_list]

        def query(method, q, elig):
            stats = {"bytes": 0, "decoded": 4096, "rows": 1}
            raw = np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s,%s,0,4096)", (t, ids[q]),
                                          binary=True).fetchone()[0])
            stats["bytes"] += 8 * 4096
            qe = timing.envelope(raw)[None]
            if method == "E":
                got = fetch_arrays(conn, f"SELECT recording_id, morlet_decode_chunk(encoded) FROM {chunks} "
                                         "WHERE recording_id = ANY(%s)", elig)
                cand = list(got)
                g = np.array([timing.envelope(got[c]) for c in cand])
                stats["bytes"] += 8 * 4096 * len(cand)
                stats["decoded"] += 4096 * len(cand)
            else:
                cand = elig
                if method.startswith("C"):
                    n = int(method[1:])
                    st = fetch_arrays(conn, f"SELECT recording_id, st FROM {st_t} WHERE recording_id = ANY(%s)", elig)
                    cid = list(st)
                    qs = (st_vector(raw) - mu) / sd
                    d = timing.vector_distances(qs[None], (np.array([st[c] for c in cid]) - mu) / sd)[0]
                    stats["bytes"] += 4 * 51 * len(cid)
                    stats["rows"] += len(cid)
                    cand = topk(cid, d, n)
                got = fetch_arrays(conn, f"SELECT recording_id, envelope FROM {env_t} WHERE recording_id = ANY(%s)", cand)
                cand = list(got)
                g = np.array([got[c] for c in cand])
                stats["bytes"] += 4 * g.shape[1] * len(cand)
            stats["rows"] += len(cand)
            d = timing.xcorr_distances(qe, g)[0]
            return topk(cand, d), stats

        for q, elig in queries:  # untimed warm-up pass
            for m in methods:
                query(m, q, elig)
        res = {m: {"ms": [], "bytes": [], "decoded": [], "rows": [], "top": []} for m in methods}
        for q, elig in queries:
            for m in methods:
                t0 = time.perf_counter()
                top, stats = query(m, q, elig)
                res[m]["ms"].append(1e3 * (time.perf_counter() - t0))
                res[m]["top"].append(top)
                for k in ("bytes", "decoded", "rows"):
                    res[m][k].append(stats[k])
    events = [meta["event"][q] for q, _ in queries]
    summary = {"year": year, "queries": len(queries), "events": len(set(events)), "methods": {}, "provenance": provenance()}
    per_event = {}
    for m in methods:
        r = res[m]
        rec = [len(set(a) & set(b)) / timing.TOP_K for a, b in zip(r["top"], res["E"]["top"])]
        dkm = [float(np.median([abs(hypo[c] - hypo[ids[q]]) for c in top])) for (q, _), top in zip(queries, r["top"])]
        groups = {}
        for e, v in zip(events, dkm):
            groups.setdefault(e, []).append(v)
        per_event[m] = {e: float(np.mean(v)) for e, v in groups.items()}
        summary["methods"][m] = {
            "latency_ms": {"p50": float(np.percentile(r["ms"], 50)), "p95": float(np.percentile(r["ms"], 95))},
            "bytes_fetched_mean": float(np.mean(r["bytes"])), "samples_decoded_mean": float(np.mean(r["decoded"])),
            "rows_read_mean": float(np.mean(r["rows"])),
            "recall10_vs_E": float(np.mean(rec)), "identical_set": float(np.mean([x == 1.0 for x in rec])),
            "identical_order": float(np.mean([a == b for a, b in zip(r["top"], res["E"]["top"])])),
            "median_abs_dkm_mean_over_events": float(np.mean(list(per_event[m].values())))}
    for m in methods[1:]:
        # Positive diff_km: m has the smaller |dd| than E. Report m - E as well for the decision rule.
        b = timing.bootstrap(per_event[m], per_event["E"])
        summary["methods"][m]["relevance_vs_E"] = {"m_minus_e_km": -b["diff_km"],
                                                   "ci95": [-b["ci95"][1], -b["ci95"][0]], "events": b["events"]}
    if n_fixed is None:
        ok = [n for n in N_GRID if summary["methods"][f"C{n}"]["recall10_vs_E"] >= RECALL_BAR]
        summary["chosen_N"] = {"N": ok[0] if ok else N_GRID[-1], "meets_bar": bool(ok), "rule": "smallest N with recall@10 >= 0.95"}
    (out / f"run-{year}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({m: {k: v for k, v in s.items()} for m, s in summary["methods"].items()}, indent=1))
    if "chosen_N" in summary:
        print("chosen N:", summary["chosen_N"])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["load", "run"])
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--windows", type=Path, required=True, help="directory with windows-<year>.npz")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--n", type=int, help="fixed cascade N (evaluation run)")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.command == "load":
        load(a.year, a.windows, a.out)
    else:
        run(a.year, a.out, n_fixed=a.n)


if __name__ == "__main__":
    main()
