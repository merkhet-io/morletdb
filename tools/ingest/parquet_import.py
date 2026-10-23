#!/usr/bin/env python3
"""Stream local Parquet into caller-owned pg_morlet continuous recordings."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

import pyarrow.parquet as pq
import psycopg
from psycopg.types.json import Jsonb
from psycopg.types.composite import CompositeInfo, register_composite


@dataclass(frozen=True)
class Config:
    layout: str = "chunks"
    chunk_size: int = 4096
    batch_rows: int = 64
    commit_chunks: int = 16
    window: int = 512
    stride: int = 256
    normalization: str = "zscore"
    rpc_chunks: int = 1

    def validate(self):
        if self.layout not in ("chunks", "samples"):
            raise ValueError("layout must be chunks or samples")
        if not (512 <= self.window <= 4096 and 1 <= self.stride <= 32768):
            raise ValueError("invalid window or stride")
        if not (1 <= self.chunk_size <= min(32768, 256*self.stride)):
            raise ValueError("chunk_size must be 1..min(32768,256*stride)")
        if not (1 <= self.batch_rows <= 1024 and 1 <= self.commit_chunks <= 256):
            raise ValueError("batch_rows must be 1..1024 and commit_chunks 1..256")
        if not 1 <= self.rpc_chunks <= 64:
            raise ValueError("rpc_chunks must be 1..64")
        if self.rpc_chunks * self.chunk_size > 262144:
            raise ValueError("rpc_chunks * chunk_size must not exceed 262144 samples")
        if self.normalization not in ("none", "demean", "zscore"):
            raise ValueError("invalid normalization")


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number in [{low},{high}]")
    return float(value)


def metadata(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    json.dumps(value, allow_nan=False)
    return value


def column_map(config, mapping):
    required = ["recording_id", "start_sample", "sample_rate_hz", "channel", "unit",
                "samples" if config.layout == "chunks" else "value"]
    allowed = set(required) | {"metadata"}
    if set(mapping) - allowed or any(not isinstance(v, str) or not v for v in mapping.values()):
        raise ValueError("column map has unknown fields or invalid column names")
    columns = {key: key for key in allowed}
    if config.layout == "samples":
        columns["start_sample"] = "sample_index"
    columns.update(mapping)
    if len(set(columns.values())) != len(columns):
        raise ValueError("source columns must map to distinct fields")
    return required, columns


def parquet_rows(path, config, mapping):
    required, columns = column_map(config, mapping)
    parquet = pq.ParquetFile(path)
    names = set(parquet.schema_arrow.names)
    missing = [columns[key] for key in required if columns[key] not in names]
    if "metadata" in (mapping or {}) and columns["metadata"] not in names:
        missing.append(columns["metadata"])
    if missing:
        raise ValueError(f"missing required Parquet columns: {missing}")
    selected = [name for name in columns.values() if name in names]
    for batch in parquet.iter_batches(batch_size=config.batch_rows, columns=selected):
        for row in batch.to_pylist():
            yield {key: row[name] for key, name in columns.items() if name in row}


def chunks(path, config, mapping=None):
    """One recording buffer, canonical chunks independent of Arrow batch/row size."""
    config.validate()
    current, spec, pending, pending_start, expected = None, None, [], 0, 0
    for row_number, row in enumerate(parquet_rows(path, config, mapping or {}), 1):
        try:
            recording = row["recording_id"]
            if not isinstance(recording, str) or not recording:
                raise ValueError("recording_id must be a nonempty string")
            start = row["start_sample"]
            if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 2**63-1:
                raise ValueError("sample offset must be a nonnegative bigint")
            rate = number(row["sample_rate_hz"], "sample_rate_hz", 128, 200000)
            if any(not isinstance(row[key], str) or not row[key] for key in ("channel", "unit")):
                raise ValueError("channel and unit must be nonempty strings")
            row_spec = (rate, row["channel"], row["unit"], metadata(row.get("metadata", {})))
            values = row["samples"] if config.layout == "chunks" else [row["value"]]
            if not isinstance(values, list) or not 1 <= len(values) <= 32768:
                raise ValueError("sample arrays must contain 1..32768 values")
            values = [number(value, "sample", -1e9, 1e9) for value in values]
            if start + len(values) > 2**63-1:
                raise ValueError("sample offset overflow")
            if current != recording:
                if current is not None and recording <= current:
                    raise ValueError("rows must be grouped in ascending recording_id order")
                if pending:
                    yield current, spec, pending_start, pending
                current, spec, pending, pending_start, expected = recording, row_spec, [], start, start
            if row_spec != spec:
                raise ValueError("metadata/acquisition settings changed within a recording")
            if start != expected:
                raise ValueError("sample offsets must be contiguous (no gaps or overlaps)")
            expected = start + len(values)
            pending.extend(values)
            while len(pending) >= config.chunk_size:
                yield current, spec, pending_start, pending[:config.chunk_size]
                pending = pending[config.chunk_size:]
                pending_start += config.chunk_size
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"row {row_number}: {exc}") from exc
    if pending:
        yield current, spec, pending_start, pending


class ImportFailure(RuntimeError):
    def __init__(self, report):
        super().__init__(report["error"])
        self.report = report


def import_files(paths, collection, config=Config(), mapping=None, dsn="", validate_only=False):
    wall_started = time.perf_counter()
    config.validate()
    paths = [Path(path) for path in paths]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("provide existing local Parquet files")
    files, signatures = [], []
    for path in paths:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024*1024), b""):
                digest.update(block)
        after = path.stat()
        signature = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if signature != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError("source file changed while hashing")
        signatures.append(signature)
        files.append({"name": path.name, "sha256": digest.hexdigest(), "bytes":after.st_size})
    report = {"status": "running", "files": files, "config": config.__dict__,
              "collection":collection, "columns":mapping or {},
              "validated_chunk_submissions": 0, "validated_sample_submissions": 0,
              "committed_chunk_submissions": 0, "committed_sample_submissions": 0,
              "validate_only": validate_only, "rpc_calls":0}
    started = time.perf_counter()
    report["preflight_s"] = started-wall_started
    conn, staged_chunks, staged_samples = None, 0, 0
    pending, pending_recording, ensured, chunk_factory = [], None, None, None

    def flush():
        nonlocal staged_chunks, staged_samples, pending, ensured
        if not pending:
            return
        if config.rpc_chunks == 1:
            offset, values = pending[0]
            conn.execute("SELECT morlet_append_chunk(%s::regclass,%s,%s,%s::float8[])",
                         (collection,pending_recording,offset,values))
        else:
            payload=[chunk_factory(offset,values) for offset,values in pending]
            conn.execute("SELECT morlet_append_chunks(%s::regclass,%s,%s::public.morlet_input_chunk[])",
                         (collection,pending_recording,payload))
        report["rpc_calls"] += 1
        staged_chunks += len(pending)
        staged_samples += sum(len(values) for _,values in pending)
        pending=[]
        if staged_chunks >= config.commit_chunks:
            conn.commit()
            report["committed_chunk_submissions"] += staged_chunks
            report["committed_sample_submissions"] += staged_samples
            staged_chunks,staged_samples=0,0
            ensured=None

    try:
        if not validate_only:
            conn = psycopg.connect(dsn)
            conn.execute("SET statement_timeout='120s'")
            if config.rpc_chunks > 1:
                info=CompositeInfo.fetch(conn,"public.morlet_input_chunk")
                if info is None:
                    raise ValueError("server is missing the batched ingestion type")
                register_composite(info,conn)
                chunk_factory=info.python_type
            report["extractor_version"]=conn.execute("SELECT morlet_extractor_version()").fetchone()[0]
            conn.commit()
        for path, signature in zip(paths, signatures):
            ensured = None
            for recording, spec, start, values in chunks(path, config, mapping):
                report["validated_chunk_submissions"] += 1
                report["validated_sample_submissions"] += len(values)
                if conn is None:
                    continue
                if ensured != (recording, spec):
                    flush()
                    rate, channel, unit, meta = spec
                    conn.execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s,%s,%s,%s)",
                                 (collection, recording, rate, channel, unit, Jsonb(meta),
                                  config.window, config.stride, config.normalization))
                    ensured = (recording, spec)
                pending_recording=recording
                pending.append((start,values))
                if len(pending)>=config.rpc_chunks or staged_chunks+len(pending)>=config.commit_chunks:
                    flush()
            after = path.stat()
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != signature:
                raise ValueError("source file changed during import")
            flush()
        if conn is not None:
            conn.commit()
            report["committed_chunk_submissions"] += staged_chunks
            report["committed_sample_submissions"] += staged_samples
        report["status"] = "validated" if validate_only else "complete"
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except psycopg.Error:
                pass  # A broken connection must not mask the original failure.
        report["status"] = "failed"
        report["error"] = (exc.diag.message_primary or type(exc).__name__) if isinstance(exc, psycopg.Error) else str(exc)
        report["elapsed_s"] = time.perf_counter() - started
        report["wall_elapsed_s"] = time.perf_counter() - wall_started
        raise ImportFailure(report) from exc
    finally:
        if conn is not None:
            conn.close()
    report["elapsed_s"] = time.perf_counter() - started
    report["wall_elapsed_s"] = time.perf_counter() - wall_started
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--collection", required=True, help="existing stream collection, e.g. public.signals")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--layout", choices=["chunks", "samples"], default="chunks")
    parser.add_argument("--columns", type=Path, help="JSON canonical-to-source column mapping")
    parser.add_argument("--chunk-size", type=int, default=4096)
    parser.add_argument("--batch-rows", type=int, default=64)
    parser.add_argument("--commit-chunks", type=int, default=16)
    parser.add_argument("--rpc-chunks", type=int, default=1)
    parser.add_argument("--window", type=int, default=512)
    parser.add_argument("--stride", type=int, default=256)
    parser.add_argument("--normalization", choices=["none", "demean", "zscore"], default="zscore")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        mapping = json.loads(args.columns.read_text()) if args.columns else {}
        if not isinstance(mapping, dict):
            raise ValueError("column mapping must be a JSON object")
        config = Config(args.layout, args.chunk_size, args.batch_rows, args.commit_chunks,
                        args.window, args.stride, args.normalization, args.rpc_chunks)
        report = import_files(args.files, args.collection, config, mapping, args.dsn, args.validate_only)
    except ImportFailure as exc:
        print(json.dumps(exc.report, indent=2), file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"status":"failed", "error":str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
