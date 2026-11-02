#!/usr/bin/env python3
"""Export recordings to the MorletDB exchange format (Parquet), or verify a collection against an export.

  python tools/ingest/morlet_export.py OUT.parquet --collection public.signals [--metadata-filter '{"asset":"a1"}']
                                                   [--recording ID ...]
  python tools/ingest/morlet_export.py --verify OUT.parquet --collection public.copy

Format (morlet-exchange, version 1; docs/exchange-format.md): one row per chunk of at most 32,768 samples, rows
ascending by recording_id and start_sample, columns recording_id, sample_rate_hz, channel, unit, metadata (JSON
text), sample_quantum, extractor, start_sample, samples (list of float64). This is the chunk layout
parquet_import.py reads. The file's key-value metadata "morletdb.exchange" holds the format version, the source,
the export time and, per recording, the sample count, the window/stride/normalization it was stored with and a
SHA-256 of its samples (little-endian float64 in order), so an import can be checked sample for sample.

Samples are read with morlet_read_interval, i.e. the values as stored (on their quantum grid), not the source
file the recording came from, nor the compressed chunks. Reads run in a read-only transaction; memory is bounded
by one batch of rows.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

FORMAT, VERSION = "morlet-exchange", 1
CHUNK = 32768
BATCH_ROWS = 32
KEY = b"morletdb.exchange"
SCHEMA = pa.schema([("recording_id", pa.string()), ("sample_rate_hz", pa.float64()), ("channel", pa.string()),
                    ("unit", pa.string()), ("metadata", pa.string()), ("sample_quantum", pa.float64()),
                    ("extractor", pa.string()), ("start_sample", pa.int64()), ("samples", pa.list_(pa.float64()))])


def release():
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
    except OSError:
        return None


def connect(dsn):
    conn = psycopg.connect(dsn, options="-c default_transaction_read_only=on")
    conn.read_only = True
    return conn


def checked(conn, collection):
    if conn.execute("SELECT to_regclass(%s)", (collection,)).fetchone()[0] is None:
        raise ValueError(f"collection {collection} does not exist (or is not visible)")
    return collection


def recordings(conn, collection, flt, ids):
    sql = (f"SELECT recording_id, sample_rate_hz, channel, unit, metadata, sample_quantum, checkpoint - 'buffer' "
           f"FROM {collection} WHERE metadata @> %s::jsonb")
    args = [json.dumps(flt)]
    if ids:
        sql += " AND recording_id = ANY(%s)"
        args.append(list(ids))
    rows = conn.execute(sql + " ORDER BY recording_id COLLATE \"C\"", args).fetchall()
    if ids and len(rows) != len(set(ids)):
        missing = sorted(set(ids) - {r[0] for r in rows})
        raise ValueError(f"recordings not found: {', '.join(missing)}")
    return rows


def read(conn, collection, rid, start, end):
    return np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s::regclass, %s, %s, %s)",
                                   (collection, rid, start, end)).fetchone()[0], dtype="<f8")


def export(dsn, collection, out, flt=None, ids=None):
    out = Path(out)
    tmp = out.with_name(out.name + ".part")
    manifest = {"format": FORMAT, "version": VERSION, "source_collection": collection,
                "exported_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "morletdb_release": release(), "values": "samples as stored (morlet_read_interval), float64",
                "recordings": {}}
    with connect(dsn) as conn:
        c = checked(conn, collection)
        recs = recordings(conn, c, flt or {}, ids)
        writer = pq.ParquetWriter(tmp, SCHEMA, compression="zstd")
        try:
            batch = []
            for rid, rate, channel, unit, meta, quantum, ck in recs:
                n = int(ck["end"])
                digest = hashlib.sha256()
                for a in range(0, n, CHUNK):
                    x = read(conn, c, rid, a, min(a + CHUNK, n))
                    digest.update(x.tobytes())
                    batch.append({"recording_id": rid, "sample_rate_hz": rate, "channel": channel, "unit": unit,
                                  "metadata": json.dumps(meta, sort_keys=True), "sample_quantum": quantum,
                                  "extractor": ck.get("extractor_version"), "start_sample": a,
                                  "samples": x.tolist()})
                    if len(batch) >= BATCH_ROWS:
                        writer.write_batch(pa.RecordBatch.from_pylist(batch, SCHEMA))
                        batch = []
                manifest["recordings"][rid] = {
                    "samples": n, "sha256": digest.hexdigest(),
                    "stored_with": {k: ck.get(k) for k in ("extractor_version", "window", "stride", "normalization")}}
            if batch:
                writer.write_batch(pa.RecordBatch.from_pylist(batch, SCHEMA))
            writer.add_key_value_metadata({KEY: json.dumps(manifest, sort_keys=True).encode()})
        finally:
            writer.close()
    tmp.replace(out)
    return {"file": str(out), "recordings": len(manifest["recordings"]),
            "samples": sum(r["samples"] for r in manifest["recordings"].values()), "bytes": out.stat().st_size}


def manifest_of(path):
    meta = pq.ParquetFile(path).metadata.metadata or {}
    if KEY not in meta:
        raise ValueError(f"{path} has no {KEY.decode()} metadata: not a MorletDB exchange file")
    m = json.loads(meta[KEY])
    if m.get("format") != FORMAT or m.get("version") != VERSION:
        raise ValueError(f"unsupported exchange format {m.get('format')} version {m.get('version')}")
    return m


def verify(dsn, collection, path):
    """Every recording in the file exists in the collection with the same sample count and sample SHA-256."""
    m = manifest_of(path)
    problems, ok = [], 0
    with connect(dsn) as conn:
        c = checked(conn, collection)
        have = {r[0]: int(r[6]["end"]) for r in recordings(conn, c, {}, None)}
        for rid, want in sorted(m["recordings"].items()):
            if rid not in have:
                problems.append(f"{rid}: missing")
                continue
            if have[rid] != want["samples"]:
                problems.append(f"{rid}: {have[rid]} samples, expected {want['samples']}")
                continue
            digest = hashlib.sha256()
            for a in range(0, have[rid], CHUNK):
                digest.update(read(conn, c, rid, a, min(a + CHUNK, have[rid])).tobytes())
            if digest.hexdigest() != want["sha256"]:
                problems.append(f"{rid}: samples differ")
            else:
                ok += 1
    return {"file": str(path), "collection": collection, "recordings_identical": ok, "problems": problems}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out", nargs="?", type=Path, help="exchange file to write")
    p.add_argument("--collection", required=True)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--metadata-filter", default="{}", help="JSON object matched by containment")
    p.add_argument("--recording", action="append", default=[], help="export only these recordings (repeatable)")
    p.add_argument("--verify", type=Path, help="check the collection against this exchange file instead")
    opts = p.parse_args(argv)
    try:
        if opts.verify:
            report = verify(opts.dsn, opts.collection, opts.verify)
            print(json.dumps(report, indent=2))
            return 1 if report["problems"] else 0
        if not opts.out:
            raise ValueError("give an output file, or --verify FILE")
        flt = json.loads(opts.metadata_filter)
        if not isinstance(flt, dict):
            raise ValueError("--metadata-filter must be a JSON object")
        print(json.dumps(export(opts.dsn, opts.collection, opts.out, flt, opts.recording), indent=2))
        return 0
    except (ValueError, OSError, psycopg.Error) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
