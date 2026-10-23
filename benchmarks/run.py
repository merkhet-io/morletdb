#!/usr/bin/env python3
"""Bounded prototype benchmark. No credentials are written to the report."""
import argparse
import hashlib
import json
import math
import os
import platform
import random
import statistics
import threading
import time
import uuid
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - start


def summary(values):
    ordered = sorted(values)
    return {"count": len(values), "median_ms": statistics.median(values) * 1000,
            "p95_ms": ordered[math.ceil(.95 * len(ordered)) - 1] * 1000,
            "min_ms": ordered[0] * 1000, "max_ms": ordered[-1] * 1000}


def measure(fn, repeats):
    for _ in range(3):
        fn()
    return summary([timed(fn)[1] for _ in range(repeats)])


def dataset(rows, samples, seed):
    rng = random.Random(seed)
    result = []
    for row in range(rows):
        phase = rng.random() * math.tau
        frequency = 8 + row % 31
        signal = [(1 + row / rows) * math.sin(math.tau * frequency * i / 512 + phase)
                  + .1 * rng.uniform(-1, 1) + (1.5 if samples // 2 <= i < samples // 2 + 16 else 0)
                  for i in range(samples)]
        result.append({"id": row + 1, "sample_rate_hz": 4096., "channel": "vibration",
                       "unit": "g", "asset": f"asset-{row % 4}", "samples": signal})
    return result


def parquet_baseline(records, directory, repeats):
    db = duckdb.connect()
    db.execute("SET threads=2")
    table = pa.Table.from_pylist(records)
    path = directory / "raw.parquet"
    _, write_s = timed(lambda: pq.write_table(table, path, compression="zstd"))
    db.read_parquet(str(path)).create_view("raw")
    actual = db.execute("SELECT sum(len(samples)) FROM raw").fetchone()[0]
    assert actual == sum(len(r["samples"]) for r in records)
    # Force sample decoding/aggregation, rather than a metadata-only row count.
    scan = lambda: db.execute("SELECT sum(list_sum(samples)) FROM raw").fetchone()
    _, first_scan_s = timed(scan)
    return db, {"raw_parquet_bytes": path.stat().st_size, "parquet_write_s": write_s,
                "first_sample_scan_s": first_scan_s, "warm_sample_scan": measure(scan, repeats)}


def copy_records(conn, relation, records, cohort="base"):
    with conn.cursor().copy(sql.SQL("COPY {} (sample_rate_hz,channel,unit,metadata,samples) FROM STDIN").format(relation)) as copy:
        for record in records:
            copy.write_row((record["sample_rate_hz"], record["channel"], record["unit"],
                            Jsonb({"asset": record["asset"], "cohort": cohort}), record["samples"]))


def postgres_baseline(args, records, db, directory):
    schema = "morlet_bench_" + uuid.uuid4().hex
    raw_name, indexed_name = f"{schema}.raw", f"{schema}.indexed"
    raw, indexed = sql.Identifier(schema, "raw"), sql.Identifier(schema, "indexed")
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        conn.execute("SET statement_timeout='120s'")
        version = conn.execute("SELECT morlet_version(),morlet_extractor_version(),version()").fetchone()
        settings = {name: conn.execute(sql.SQL("SHOW {}").format(sql.Identifier(name))).fetchone()[0]
                    for name in ("shared_buffers", "work_mem", "max_parallel_workers_per_gather", "jit")}
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            for name in ("raw", "indexed"):
                conn.execute("SELECT morlet_create_collection(%s,%s)", (schema, name))
            _, raw_s = timed(lambda: copy_records(conn, raw, records))
            raw_size = conn.execute("SELECT pg_total_relation_size(%s::regclass)", (raw_name,)).fetchone()[0]
            assert conn.execute(sql.SQL("SELECT sum(cardinality(samples)) FROM {}").format(raw)).fetchone()[0] == len(records)*args.samples
            _, extraction_s = timed(lambda: conn.execute("SELECT morlet_enable_segments(%s,512,256,'zscore')", (raw_name,)))
            conn.execute("SELECT morlet_enable_segments(%s,512,256,'zscore')", (indexed_name,))
            _, synchronous_s = timed(lambda: copy_records(conn, indexed, records))
            for relation in (raw, indexed):
                conn.execute(sql.SQL("ANALYZE {}").format(relation))
            sizes = {label: conn.execute("SELECT pg_total_relation_size(%s::regclass)", (name,)).fetchone()[0]
                     for label, name in (("backfilled_total_bytes", raw_name), ("synchronous_total_bytes", indexed_name))}
            query = records[0]["samples"][:512]
            query_features = conn.execute("SELECT morlet_features(morlet_normalize(%s::float8[],'zscore'),4096)", (query,)).fetchone()[0]
            # Export the SAME stored features, without re-extracting in a different engine.
            extracted = conn.execute(sql.SQL("SELECT id,metadata->>'asset',morlet_grid FROM {} ORDER BY id").format(indexed)).fetchall()
            feature_rows = [{"recording_id": row_id, "asset": asset, "start_sample": seg["start_sample"], "features": seg["features"]}
                            for row_id, asset, grid in extracted for seg in grid["segments"]]
            assert len(feature_rows) == len(records)*(1+(args.samples-512)//256)
            feature_path = directory / "features.parquet"
            pq.write_table(pa.Table.from_pylist(feature_rows), feature_path, compression="zstd")
            db.read_parquet(str(feature_path)).create_view("features")
            retrieval_sql = sql.SQL("""SELECT r.id,(s->>'start_sample')::int,
              public.morlet_feature_vector_distance(ARRAY(SELECT jsonb_array_elements_text(s->'features')::float8),%s::float8[]) AS distance
              FROM {} r CROSS JOIN LATERAL jsonb_array_elements(r.morlet_grid->'segments') s
              WHERE r.metadata @> %s::jsonb ORDER BY distance,r.id,(s->>'start_sample')::int LIMIT 10""").format(indexed)
            duck_sql = """SELECT recording_id,start_sample,
              sqrt(list_sum(list_transform(list_zip(features,?::DOUBLE[]), x -> (x[1]-x[2])*(x[1]-x[2])))/15) AS distance
              FROM features WHERE (? IS NULL OR asset=?) ORDER BY distance,recording_id,start_sample LIMIT 10"""
            comparisons = {}
            for label, asset in (("all", None), ("asset", "asset-0")):
                filt = {"cohort": "base", **({"asset": asset} if asset else {})}
                pg_query = lambda: conn.execute(retrieval_sql, (query_features, Jsonb(filt))).fetchall()
                duck_query = lambda: db.execute(duck_sql, [query_features, asset, asset]).fetchall()
                pg_hits, duck_hits = pg_query(), duck_query()
                assert [(r[0],r[1]) for r in pg_hits] == [(r[0],r[1]) for r in duck_hits], "retrieval identities disagree"
                assert all(math.isclose(p[2],d[2],rel_tol=1e-9,abs_tol=1e-10) for p,d in zip(pg_hits,duck_hits)), "distances disagree"
                comparisons[label] = {"candidates": sum(asset is None or r["asset"] == asset for r in feature_rows),
                                      "postgres": measure(pg_query,args.repeats), "duckdb_parquet": measure(duck_query,args.repeats),
                                      "identical_top_k": True}
            api = lambda: conn.execute("SELECT * FROM morlet_search_features(%s,%s::float8[],4096,'vibration','g',%s::jsonb,'zscore',10,NULL,NULL,NULL,false)",
                                       (indexed_name,query,Jsonb({"cohort":"base"}))).fetchall()
            api_latency = measure(api,args.repeats)
            # A second connection commits feature-generating writes while the
            # main connection searches a fixed cohort in the same relation.
            ready, start, stop = threading.Event(), threading.Event(), threading.Event()
            writer_times, writer_errors, writer_commits = [], [], []
            def writer():
                try:
                    with psycopg.connect(args.dsn,autocommit=True) as writer_conn:
                        writer_conn.execute("SET statement_timeout='120s'")
                        ready.set()
                        start.wait()
                        while not stop.is_set():
                            _, elapsed = timed(lambda: copy_records(writer_conn,indexed,records[:1],"writer"))
                            writer_times.append(elapsed)
                            writer_commits.append(time.perf_counter())
                except Exception as exc:
                    # Avoid serializing connection errors containing endpoint details.
                    writer_errors.append(type(exc).__name__)
                    ready.set()
            thread = threading.Thread(target=writer)
            thread.start()
            ready.wait(timeout=30)
            if not ready.is_set():
                stop.set(); start.set(); thread.join(); raise RuntimeError("writer connection timed out")
            start.set()
            mixed_start = time.perf_counter()
            try:
                mixed = summary([timed(api)[1] for _ in range(args.repeats)])
                mixed_end = time.perf_counter()
            finally:
                stop.set(); thread.join()
            assert not writer_errors, f"writer failed: {writer_errors}"
            return {"server": version[2], "extension_version": version[0], "extractor_version": version[1],
                    "settings": settings, "copy_raw_s": raw_s, "raw_total_bytes_before_features": raw_size, "backfill_features_s": extraction_s,
                    "copy_with_features_s": synchronous_s, "raw_samples_per_s": len(records)*args.samples/raw_s,
                    "synchronous_samples_per_s": len(records)*args.samples/synchronous_s, **sizes,
                    "feature_parquet_bytes": feature_path.stat().st_size, "stored_segments": len(feature_rows),
                    "precomputed_feature_retrieval": comparisons, "api_including_query_extraction": api_latency,
                    "mixed_api": mixed, "mixed_writer_commits_during_search": sum(mixed_start <= t <= mixed_end for t in writer_commits),
                    "mixed_writer_commit_latency": summary(writer_times) if writer_times else None}
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""), help="Postgres DSN; defaults to libpq environment")
    parser.add_argument("--parquet-only", action="store_true")
    parser.add_argument("--rows", type=int, default=64)
    parser.add_argument("--samples", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--output", type=Path, default=Path("benchmark-output"))
    args = parser.parse_args()
    if not (1 <= args.rows <= 256 and 512 <= args.samples <= 32768 and args.repeats >= 5):
        parser.error("rows must be 1..256, samples 512..32768, repeats >=5")
    if args.rows*(1+(args.samples-512)//256) > 4096:
        parser.error("dataset exceeds the prototype's 4096-segment search budget")
    args.output.mkdir(parents=True,exist_ok=False)
    records = dataset(args.rows,args.samples,args.seed)
    fingerprint = hashlib.sha256(json.dumps(records,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    db, parquet = parquet_baseline(records,args.output,args.repeats)
    report = {"benchmark_version": 1, "revision": os.environ.get("GITHUB_SHA", "unspecified"), "build_profile": os.environ.get("MORLET_BUILD_PROFILE","unspecified"),
              "environment": {"platform":platform.platform(),"cpu_count":os.cpu_count(),"python":platform.python_version(),
                              "duckdb":duckdb.__version__,"pyarrow":pa.__version__,"psycopg":psycopg.__version__,"duckdb_threads":2},
              "dataset": {"rows":args.rows,"samples_per_row":args.samples,"seed":args.seed,"sha256":fingerprint,
                          "logical_float64_bytes":args.rows*args.samples*8,"sample_rate_hz":4096,
                          "window_size":512,"stride":256,"normalization":"zscore"}, "parquet":parquet,
              "limitations": ["bounded synthetic single-channel dataset; not a scale benchmark",
                              "warm queries, three warmups; first scan is not a controlled cold-cache measurement",
                              "COPY timing includes client serialization, transport and commit; Parquet timing is local file creation",
                              "DuckDB features are exported from Postgres; extraction is not compared across engines",
                              "retrieval uses identical precomputed features and no overlap suppression/source exclusion",
                              "Postgres JSONB scan versus DuckDB typed columns; results compare these implementations, not engine limits",
                              "sizes have different contents/overheads; feature Parquet excludes raw samples",
                              "mixed test uses one writer and fixed-cohort searches; no HTAP isolation or async freshness claim"]}
    if not args.parquet_only:
        report["postgres"] = postgres_baseline(args,records,db,args.output)
    db.close()
    (args.output/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
