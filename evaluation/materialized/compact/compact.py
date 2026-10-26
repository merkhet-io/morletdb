#!/usr/bin/env python3
"""R1b: compact materialized envelopes (PROTOCOL.md in this directory).

  derive  for a period already loaded by ../r1.py, store each candidate envelope
          representation in its own table; measure encode cost, insert cost and size
  run     query E (exhaustive raw, as in R1) and every candidate; report latency, data
          access, agreement with E and task relevance; on the development period, apply
          the selection rule

Run with the seismic environment (numpy, scipy, psycopg).
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent), str(HERE.parent.parent / "decision")]
import r1  # noqa: E402
import timing  # noqa: E402

DEV_YEAR = 2023
RECALL_BAR, ORDER_BAR = 0.99, 0.95


def envelope(x, step):
    """timing.envelope with a configurable decimation step (10 reproduces it exactly)."""
    from scipy.signal import hilbert
    from retrieval import normalize
    env = np.abs(hilbert(normalize(x, "zscore")))
    env = np.convolve(env, np.ones(50) / 50, mode="same")[::step]
    s = env.std()
    return (env - env.mean()) / (s if s > 0 else 1.0)


def zscore(v):
    s = v.std()
    return (v - v.mean()) / (s if s > 0 else 1.0)


def enc_f4(v):
    return v.astype("<f4").tobytes()


def dec_f4(b):
    return np.frombuffer(b, "<f4").astype(float)


def enc_f16(v):
    return v.astype("<f2").tobytes()


def dec_f16(b):
    return zscore(np.frombuffer(b, "<f2").astype(float))


def enc_u8(v):
    lo, hi = float(v.min()), float(v.max())
    q = np.zeros(len(v), np.uint8) if hi <= lo else np.round((v - lo) / (hi - lo) * 255).astype(np.uint8)
    return struct.pack("<ff", lo, hi) + q.tobytes()


def dec_u8(b):
    lo, hi = struct.unpack_from("<ff", b)
    return zscore(lo + np.frombuffer(b, np.uint8, offset=8).astype(float) * ((hi - lo) / 255))


# name: (decimation step, max lag in envelope samples = 3 s, encoder, decoder). Fixed before any run.
REPS = {
    "f4": (10, 30, enc_f4, dec_f4),
    "f16": (10, 30, enc_f16, dec_f16),
    "u8": (10, 30, enc_u8, dec_u8),
    "u8-d20": (20, 15, enc_u8, dec_u8),
}


def table(year, rep):
    return f"r1_y{year}_env_{rep.replace('-', '_')}"


def derive(year, out: Path):
    t, chunks, _, _ = r1.names(year)
    report = {"year": year, "provenance": r1.provenance(), "reps": {}}
    with psycopg.connect(r1.DSN, autocommit=True) as conn:
        ids = [r[0] for r in conn.execute(f"SELECT recording_id FROM {t} ORDER BY recording_id").fetchall()]
        for rep in REPS:
            conn.execute(f"DROP TABLE IF EXISTS {table(year, rep)}")
            conn.execute(f"CREATE TABLE {table(year, rep)} (recording_id text PRIMARY KEY REFERENCES {t} "
                         "ON DELETE CASCADE, envelope bytea NOT NULL)")
            conn.execute(f"COMMENT ON TABLE {table(year, rep)} IS 'xc-envelope-{rep}'")
        cost = {rep: {"envelope_cpu_s": 0.0, "encode_cpu_s": 0.0, "insert_s": 0.0} for rep in REPS}
        for rid in ids:
            raw = np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s,%s,0,4096)", (t, rid),
                                          binary=True).fetchone()[0])
            for rep, (step, _, enc, _) in REPS.items():
                t1 = time.perf_counter()
                v = envelope(raw, step)
                t2 = time.perf_counter()
                b = enc(v)
                t3 = time.perf_counter()
                conn.execute(f"INSERT INTO {table(year, rep)} VALUES (%s,%s)", (rid, b))
                t4 = time.perf_counter()
                c = cost[rep]
                c["envelope_cpu_s"] += t2 - t1
                c["encode_cpu_s"] += t3 - t2
                c["insert_s"] += t4 - t3
        conn.execute(f"VACUUM ANALYZE {chunks}")
        raw_size = conn.execute("SELECT pg_total_relation_size(%s::regclass)", (chunks,)).fetchone()[0]
        report["raw_chunk_table_bytes"] = raw_size
        report["windows"] = len(ids)
        for rep in REPS:
            conn.execute(f"VACUUM ANALYZE {table(year, rep)}")
            size = conn.execute("SELECT pg_total_relation_size(%s::regclass)", (table(year, rep),)).fetchone()[0]
            payload = conn.execute(f"SELECT avg(octet_length(envelope)) FROM {table(year, rep)}").fetchone()[0]
            report["reps"][rep] = {"table_bytes": size, "fraction_of_raw_chunks": size / raw_size,
                                   "payload_bytes_per_window": float(payload),
                                   "generation_ms_per_window": {k: 1e3 * v / len(ids) for k, v in cost[rep].items()}}
    (out / f"derive-{year}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["reps"], indent=1))


def run(year, out: Path):
    t, chunks, _, _ = r1.names(year)
    with psycopg.connect(r1.DSN, autocommit=True) as conn:
        rows = conn.execute(f"SELECT recording_id, metadata FROM {t} ORDER BY recording_id").fetchall()
        ids = np.array([r[0] for r in rows])
        meta = {}
        for k in rows[0][1]:
            vals = [r[1][k] for r in rows]
            numeric = all(v is None or isinstance(v, (int, float)) for v in vals)
            meta[k] = np.array([np.nan if v is None else v for v in vals], float) if numeric else np.array(vals)
        ev = np.where(meta["kind"] == "event")[0]
        mask = timing.gallery_mask(meta, ev, np.arange(len(ids)), True)
        queries = [(q, list(ids[np.where(mask[i])[0]])) for i, q in enumerate(ev) if mask[i].sum() >= timing.TOP_K]
        hypo = dict(zip(ids, meta["hypo_km"].astype(float)))
        methods = ["E", *REPS]

        def query(method, q, elig):
            stats = {"bytes": 8 * 4096, "decoded": 4096, "rows": 1}
            raw = np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s,%s,0,4096)", (t, ids[q]),
                                          binary=True).fetchone()[0])
            if method == "E":
                qe = timing.envelope(raw)[None]
                got = r1.fetch_arrays(conn, f"SELECT recording_id, morlet_decode_chunk(encoded) FROM {chunks} "
                                            "WHERE recording_id = ANY(%s)", elig)
                cand = list(got)
                g = np.array([timing.envelope(got[c]) for c in cand])
                stats["bytes"] += 8 * 4096 * len(cand)
                stats["decoded"] += 4096 * len(cand)
                lag = 30
            else:
                step, lag, _, dec = REPS[method]
                qe = envelope(raw, step)[None]
                got = conn.execute(f"SELECT recording_id, envelope FROM {table(year, method)} "
                                   "WHERE recording_id = ANY(%s)", (elig,), binary=True).fetchall()
                cand = [r[0] for r in got]
                g = np.array([dec(bytes(r[1])) for r in got])
                stats["bytes"] += sum(len(r[1]) for r in got)
            stats["rows"] += len(cand)
            d = timing.xcorr_distances(qe, g, lag)[0]
            return r1.topk(cand, d), stats

        for q, elig in queries:  # untimed warm-up pass
            for m in methods:
                query(m, q, elig)
        load_before = os.getloadavg()
        res = {m: {"ms": [], "bytes": [], "decoded": [], "rows": [], "top": []} for m in methods}
        for q, elig in queries:
            for m in methods:
                t0 = time.perf_counter()
                top, stats = query(m, q, elig)
                res[m]["ms"].append(1e3 * (time.perf_counter() - t0))
                res[m]["top"].append(top)
                for k in ("bytes", "decoded", "rows"):
                    res[m][k].append(stats[k])
        load_after = os.getloadavg()
    events = [meta["event"][q] for q, _ in queries]
    summary = {"year": year, "queries": len(queries), "events": len(set(events)),
               "load_average_before_after_timed_pass": [load_before, load_after],
               "methods": {}, "provenance": r1.provenance()}
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
            "latency_ms": {p: float(np.percentile(r["ms"], int(p[1:]))) for p in ("p50", "p95", "p99")},
            "bytes_fetched_mean": float(np.mean(r["bytes"])), "samples_decoded_mean": float(np.mean(r["decoded"])),
            "rows_read_mean": float(np.mean(r["rows"])),
            "recall10_vs_E": float(np.mean(rec)), "identical_set": float(np.mean([x == 1.0 for x in rec])),
            "identical_order": float(np.mean([a == b for a, b in zip(r["top"], res["E"]["top"])])),
            "median_abs_dkm_mean_over_events": float(np.mean(list(per_event[m].values())))}
    for m in REPS:
        b = timing.bootstrap(per_event[m], per_event["E"])
        summary["methods"][m]["relevance_vs_E"] = {"m_minus_e_km": -b["diff_km"],
                                                   "ci95": [-b["ci95"][1], -b["ci95"][0]], "events": b["events"]}
    derived = json.loads((out / f"derive-{year}.json").read_text())["reps"]
    if year == DEV_YEAR:
        ok = [m for m in REPS if summary["methods"][m]["recall10_vs_E"] >= RECALL_BAR
              and summary["methods"][m]["identical_order"] >= ORDER_BAR]
        pick = min(ok, key=lambda m: derived[m]["table_bytes"]) if ok else None
        summary["selected"] = {"rep": pick, "qualifying": ok,
                               "rule": "smallest table among reps with recall@10 >= 0.99 and identical order >= 0.95"}
    (out / f"run-{year}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "provenance"}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["derive", "run"])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    (derive if a.command == "derive" else run)(a.year, a.out)


if __name__ == "__main__":
    main()
