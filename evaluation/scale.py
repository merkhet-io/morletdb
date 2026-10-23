#!/usr/bin/env python3
"""Measure exact SQL retrieval beyond the default budget using isolated fixtures.

This is feature-only search, not raw ingest or waveform relevance evaluation.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import time
import uuid

import numpy as np
import psycopg
from psycopg import sql


def percentiles(values):
    ordered = sorted(values)
    return {"median_s": statistics.median(ordered),
            "p95_s": ordered[max(0, math.ceil(len(ordered)*0.95)-1)],
            "min_s": ordered[0], "max_s": ordered[-1]}


def run(sizes, repeats, timeout_ms):
    if not sizes or any(not 1 <= size <= 1000000 for size in sizes) or repeats < 1 or timeout_ms < 1:
        raise ValueError("sizes 1..1000000, positive repeats and timeout required")
    query = [math.sin(i/8) for i in range(512)]
    report = {"report_version": 1, "fixture": "synthetic feature perturbations, no raw archive",
              "scope": "exact-search SQL integration; excludes extraction/backfill/ingest",
              "repeats": repeats, "timeout_ms": timeout_ms, "seed": 73,
              "platform": platform.platform(), "python": platform.python_version(),
              "numpy": np.__version__, "cases": []}
    with psycopg.connect(os.environ.get("DATABASE_URL", ""), autocommit=True) as conn:
        conn.execute("SELECT set_config('statement_timeout',%s,false)", (str(timeout_ms),))
        report["postgres_version"] = conn.execute("SHOW server_version").fetchone()[0]
        report["settings"] = {name: conn.execute(sql.SQL("SHOW {}").format(sql.Identifier(name))).fetchone()[0]
                              for name in ("work_mem", "shared_buffers", "max_parallel_workers_per_gather")}
        report["extractor_version"] = conn.execute("SELECT morlet_extractor_version()").fetchone()[0]
        features = np.asarray(conn.execute("SELECT morlet_features(morlet_normalize(%s::float8[],'zscore'),4096)",
                                           (query,)).fetchone()[0])
        order = sizes.copy()
        random.Random(73).shuffle(order)
        for size in order:
            schema = "morlet_scale_"+uuid.uuid4().hex
            case = {"candidates": size, "status": "running"}
            try:
                conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                conn.execute("SELECT morlet_create_stream_collection(%s,'signals')", (schema,))
                collection = schema+".signals"
                conn.execute("SELECT morlet_start_recording(%s::regclass,'population',4096,'synthetic','a.u.')", (collection,))
                # Bypass append/extraction ONLY in this disposable benchmark: the
                # fixture has no raw chunks and cannot be used for interval reads.
                indices = np.arange(size, dtype=np.int64)
                deltas = ((indices*7919) % size)/size
                vectors = features[None, :] + deltas[:, None]
                reference_distances = np.sqrt(np.mean((vectors-features)**2, axis=1))
                oracle = np.lexsort((indices, reference_distances))[:min(10, size)]
                setup_started = time.perf_counter()
                with conn.cursor().copy(sql.SQL("COPY {}.signals_segments(recording_id,start_sample,features) FROM STDIN")
                                        .format(sql.Identifier(schema))) as copy:
                    for index in range(size):
                        copy.write_row(("population", index*512, vectors[index].tolist()))
                conn.execute(sql.SQL("ANALYZE {}.signals_segments").format(sql.Identifier(schema)))
                case["feature_fixture_copy_s"] = time.perf_counter()-setup_started
                case["relation_bytes"] = conn.execute("SELECT pg_total_relation_size(%s::regclass)",
                                                       (schema+".signals_segments",)).fetchone()[0]
                if size > 4096:
                    try:
                        conn.execute("SELECT * FROM morlet_search_streams(%s::regclass,%s::float8[],4096,'synthetic','a.u.')",
                                     (collection, query)).fetchall()
                    except psycopg.errors.RaiseException as exc:
                        if "exceeds 4096 segments" not in str(exc):
                            raise
                        case["default_budget_rejected"] = True
                    else:
                        raise AssertionError("default candidate budget was bypassed")
                durations = []
                for iteration in range(repeats+1):
                    started = time.perf_counter()
                    matches = conn.execute("SELECT * FROM morlet_search_streams(%s::regclass,%s::float8[],4096,'synthetic','a.u.','{}','zscore',10,%s)",
                                           (collection, query, size)).fetchall()
                    elapsed = time.perf_counter()-started
                    actual = [row[1]//512 for row in matches]
                    if actual != oracle.tolist():
                        raise AssertionError(f"SQL ranking differs from independent oracle: {actual}")
                    np.testing.assert_allclose([row[3] for row in matches], reference_distances[oracle], rtol=1e-10, atol=1e-12)
                    if iteration:
                        durations.append(elapsed)
                    else:
                        case["first_query_s"] = elapsed
                case.update(status="complete", latency=percentiles(durations), raw_query_s=durations,
                            oracle_topk_agrees=True)
            except psycopg.errors.QueryCanceled:
                case.update(status="timeout", error="statement_timeout exceeded")
            finally:
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            report["cases"].append(case)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="10000,100000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("choose a new report path")
    result = run([int(n) for n in args.sizes.split(",")], args.repeats, args.timeout_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps(result, indent=2))
    if any(case["status"] != "complete" for case in result["cases"]):
        raise SystemExit(1)
