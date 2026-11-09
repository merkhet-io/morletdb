"""Moving recordings between a MorletDB file (the embedded edition, #351) and a server, losslessly.

The receiving side encodes the samples with the same chunk boundaries as the sending side; the codec is
deterministic, so both hold the same chunk bytes afterwards. Each transfer checks that by comparing a hash of the
chunk bytes on both sides, range by range. Recordings whose raw samples a memory level has removed (compacted
chunks) are refused: they are no longer lossless.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ._errors import ConflictError, DatabaseError, InvalidInputError, NotFoundError, translate
from ._local import LocalStore
from ._types import Recording

if TYPE_CHECKING:
    from ._client import Client

BATCH_CHUNKS = 256        # chunks per read, append and hash comparison (about 1 M samples at 4,096)


def _names(collection: str) -> tuple[sql.Composable, sql.Composable]:
    schema, table = collection.split(".", 1) if "." in collection else ("public", collection)
    return sql.Identifier(schema, table), sql.Identifier(schema, table + "_chunks")


def _file_rows(path: str, recording_ids: Iterable[str] | None) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        rows = c.execute("SELECT recording_id, sample_rate_hz, channel, unit, sample_quantum, metadata, end_sample "
                         "FROM recordings ORDER BY recording_id").fetchall()
    if recording_ids is not None:
        want = set(recording_ids)
        missing = want - {r[0] for r in rows}
        if missing:
            raise NotFoundError(f"not in {path}: {sorted(missing)}")
        rows = [r for r in rows if r[0] in want]
    return rows


def _file_layout(path: str, recording_id: str) -> list[tuple[int, int]]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        return c.execute("SELECT start_sample, sample_count FROM chunks WHERE recording_id=? ORDER BY start_sample",
                         (recording_id,)).fetchall()


def _file_hash(path: str, recording_id: str, a: int, b: int) -> str:
    h = hashlib.md5()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        for (enc,) in c.execute("SELECT encoded FROM chunks WHERE recording_id=? AND start_sample>=? AND start_sample<? "
                                "ORDER BY start_sample", (recording_id, a, b)):
            h.update(enc)
    return h.hexdigest()


def _server_hash(client: "Client", chunks: sql.Composable, recording_id: str, a: int, b: int) -> str:
    q = sql.SQL("SELECT md5(coalesce(string_agg(encoded, ''::bytea ORDER BY start_sample), ''::bytea)) FROM {} "
                "WHERE recording_id=%s AND start_sample>=%s AND start_sample<%s").format(chunks)
    return client._one(q, (recording_id, a, b))


def _batches(layout: list[tuple[int, int]], start: int) -> list[list[tuple[int, int]]]:
    todo = [c for c in layout if c[0] >= start]
    return [todo[i:i + BATCH_CHUNKS] for i in range(0, len(todo), BATCH_CHUNKS)]


def import_file(client: "Client", path: str | Path, collection: str,
                recording_ids: Iterable[str] | None = None) -> list[Recording]:
    """Copy recordings from a MorletDB file into a server collection (created recordings, or the rest of
    recordings already started there). Runs in the client's transaction; commit to keep it."""
    path = str(path)
    table, chunks = _names(collection)
    out = []
    with LocalStore(path) as local:
        for rid, rate, channel, unit, quantum, meta, end in _file_rows(path, recording_ids):
            client._execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s,512,256,'zscore','none',%s)",
                            (collection, rid, rate, channel, unit, Jsonb(json.loads(meta)), quantum))
            have = client.recording(collection, rid).samples
            layout = _file_layout(path, rid)
            starts = {s for s, _ in layout} | {end}
            if have not in starts:
                raise ConflictError(f"{rid}: the server holds {have} samples, not a chunk boundary of the file")
            if have > 0 and _server_hash(client, chunks, rid, 0, have) != _file_hash(path, rid, 0, have):
                raise ConflictError(f"{rid}: the server's first {have} samples differ from the file's")
            for batch in _batches(layout, have):
                a, b = batch[0][0], batch[-1][0] + batch[-1][1]
                x = local.read(rid, a, b).samples
                try:
                    with client.connection.pipeline():
                        for s, n in batch:
                            client.connection.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%b)",
                                                      (collection, rid, s, x[s - a:s - a + n].tobytes()))
                except psycopg.Error as exc:
                    raise translate(exc) from exc
                if _server_hash(client, chunks, rid, a, b) != _file_hash(path, rid, a, b):
                    raise DatabaseError(f"{rid}: chunks of [{a}, {b}) differ after the import")
            out.append(client.recording(collection, rid))
    return out


def export_file(client: "Client", collection: str, path: str | Path,
                recording_ids: Iterable[str] | None = None) -> list[Recording]:
    """Copy recordings of a server collection into a MorletDB file (created if missing; recordings already in
    the file are continued). Each batch of chunks is one durable transaction in the file."""
    path = str(path)
    table, chunks = _names(collection)
    ids = list(recording_ids) if recording_ids is not None else [r.recording_id for r in client.recordings(collection)]
    out = []
    with LocalStore(path) as local:
        for rid in ids:
            rec = client.recording(collection, rid)
            quantum = client._one(sql.SQL("SELECT sample_quantum FROM {} WHERE recording_id=%s").format(table), (rid,))
            layout = [tuple(map(int, r)) for r in client._execute(
                sql.SQL("SELECT start_sample, sample_count, encoded IS NULL FROM {} WHERE recording_id=%s "
                        "ORDER BY start_sample").format(chunks), (rid,)).fetchall()]
            if any(r[2] for r in layout):
                raise InvalidInputError(f"{rid}: a memory level removed raw samples; it cannot be exported losslessly")
            layout = [(s, n) for s, n, _ in layout if s < rec.samples]
            known = {r.recording_id: r for r in local.recordings()}
            if rid not in known:
                local.ingest_empty(rid, sample_rate_hz=rec.sample_rate_hz, channel=rec.channel, unit=rec.unit,
                                   metadata=rec.metadata, sample_quantum=float(quantum))
            have = known[rid].samples if rid in known else 0
            if have not in {s for s, _ in layout} | {rec.samples}:
                raise ConflictError(f"{rid}: the file holds {have} samples, not a chunk boundary of the server")
            if have > 0 and _server_hash(client, chunks, rid, 0, have) != _file_hash(path, rid, 0, have):
                raise ConflictError(f"{rid}: the file's first {have} samples differ from the server's")
            for batch in _batches(layout, have):
                a, b = batch[0][0], batch[-1][0] + batch[-1][1]
                x = client.read(collection, rid, a, b).samples
                local.append_chunks(rid, a, x, [n for _, n in batch])
                if _server_hash(client, chunks, rid, a, b) != _file_hash(path, rid, a, b):
                    raise DatabaseError(f"{rid}: chunks of [{a}, {b}) differ after the export")
            out.append(local.recording(rid))
    return out
