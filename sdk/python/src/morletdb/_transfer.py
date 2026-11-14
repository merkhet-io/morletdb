"""Moving recordings between a MorletDB file (the embedded edition, #351) and a server, losslessly.

The receiving side encodes the samples with the same chunk boundaries as the sending side. What each transfer
verifies is what lossless means, the samples (#356): after every batch of chunks, a hash of the decoded float64
values (8 bytes each, little-endian, every bit) on the receiving side must equal the sending side's, and a
recording is only continued if its stored samples equal the other side's first samples. The chunk bytes are not
required to match: a later encoder may write the same samples as other bytes, and a transfer between it and an
older one must still succeed. When both sides report the same encoder version the chunk bytes are compared as
well, and the result is reported (TransferCheck). Recordings whose raw samples a memory level has removed
(compacted chunks) are refused: they are no longer lossless.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ._client import RAW_READ_MAX
from ._errors import ConflictError, DatabaseError, InvalidInputError, NotFoundError, translate
from ._local import LocalStore
from ._types import Recording, TransferCheck

if TYPE_CHECKING:
    from ._client import Client

BATCH_CHUNKS = 256        # chunks per read, append and comparison (about 1 M samples at 4,096)


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


# --- encoder versions ---------------------------------------------------------------------------------------
def _file_encoder(path: str) -> str:
    """The file's writer as morlet_meta records it, for example "morlet-sqlite 0.3.8"."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        row = c.execute("SELECT value FROM morlet_meta WHERE key='created_by'").fetchone()
    return str(row[0]) if row else "unknown"


def _server_encoder(client: "Client") -> str:
    """The server's encoder, for example "pg_morlet 0.3.8", with " (Grizzbit 2.1)" when the session's appends write
    Grizzbit 2.1 (`morlet.chunk_format`, study B6); servers without that setting write Grizzbit 1. " + float32" is
    added when the session's appends write version-6 chunks for float32 samples (`morlet.float32_chunks`, study B8):
    asked of the encoder itself, with four float32 samples that a version-6 chunk codes smaller, so a server that
    cannot write version 6, or has the setting off, is not marked."""
    version = client._one("SELECT morlet_version()")     # loads the library, which defines the setting
    coding = client._one("SELECT current_setting('morlet.chunk_format', true)")
    v6 = client._one("SELECT get_byte(morlet_encode_chunk(ARRAY[1.5,2.5,3.5,4.5]::float8[],1),0)") == 6
    label = "Grizzbit 2.1" if coding == "grizzbit21" else "Grizzbit 1" if v6 else ""
    return f"pg_morlet {version}" + (f" ({label}{' + float32' if v6 else ''})" if label else "")


def _same_encoder(a: str, b: str) -> bool:
    """Whether two writer labels ("<name> <version>", optionally followed by " (<chunk coding>)", Grizzbit 1 when
    absent) report the same encoder version and chunk coding."""
    def parts(label: str) -> tuple[str, str]:
        head, _, coding = label.partition(" (")
        return head.rsplit(" ", 1)[-1], coding.rstrip(")") or "Grizzbit 1"
    return "unknown" not in (a, b) and parts(a) == parts(b)


# --- chunk bytes: a stronger check between equal encoders, and a fast path for an equal prefix ---------------
def _file_hash(path: str, recording_id: str, a: int, b: int) -> str:
    """Hash of the chunks starting in [a, b): each chunk's start (8 bytes, big-endian) and encoded bytes."""
    h = hashlib.md5()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        for s, enc in c.execute("SELECT start_sample, encoded FROM chunks WHERE recording_id=? AND start_sample>=? "
                                "AND start_sample<? ORDER BY start_sample", (recording_id, a, b)):
            h.update(int(s).to_bytes(8, "big", signed=True))
            h.update(enc)
    return h.hexdigest()


def _server_hash(client: "Client", chunks: sql.Composable, recording_id: str, a: int, b: int) -> str:
    q = sql.SQL("SELECT md5(coalesce(string_agg(int8send(start_sample) || encoded, ''::bytea ORDER BY start_sample), "
                "''::bytea)) FROM {} WHERE recording_id=%s AND start_sample>=%s AND start_sample<%s").format(chunks)
    return client._one(q, (recording_id, a, b))


# --- samples: what is verified ------------------------------------------------------------------------------
def _pieces(a: int, b: int) -> list[tuple[int, int]]:
    return [(s, min(s + RAW_READ_MAX, b)) for s in range(a, b, RAW_READ_MAX)]


def _digest(x: np.ndarray) -> str:
    """MD5 of float64 samples as 8 little-endian bytes each: every bit counts, including the sign of zero."""
    return hashlib.md5(np.ascontiguousarray(x, dtype="<f8").tobytes()).hexdigest()


def _server_digests(client: "Client", collection: str, recording_id: str, a: int, b: int) -> list[str]:
    """Digests of the server's decoded samples of [a, b), per piece of up to RAW_READ_MAX samples. Hashed by
    the server where it returns raw float64 (SQL 0.3.3, encoded chunks), otherwise read and hashed here."""
    if client._raw_reads(collection):
        q = "SELECT md5(morlet_read_interval_raw(%s::regclass,%s,%s,%s))"
        return [client._one(q, (collection, recording_id, s, e)) for s, e in _pieces(a, b)]
    return [_digest(client.read(collection, recording_id, s, e).samples) for s, e in _pieces(a, b)]


def _array_digests(x: np.ndarray, a: int, b: int) -> list[str]:
    return [_digest(x[s - a:e - a]) for s, e in _pieces(a, b)]


def _file_digests(local: LocalStore, recording_id: str, a: int, b: int) -> list[str]:
    return [_digest(local.read(recording_id, s, e).samples) for s, e in _pieces(a, b)]


def _same_prefix(client: "Client", collection: str, chunks: sql.Composable, local: LocalStore, path: str,
                 recording_id: str, have: int) -> bool:
    """Whether both sides hold the same samples in [0, have). Identical chunks (same starts, same bytes) decode
    to identical samples, so equal chunk bytes settle it without decoding; otherwise the samples decide."""
    if have == 0:
        return True
    if _server_hash(client, chunks, recording_id, 0, have) == _file_hash(path, recording_id, 0, have):
        return True
    return _server_digests(client, collection, recording_id, 0, have) == _file_digests(local, recording_id, 0, have)


def _batches(layout: list[tuple[int, int]], start: int) -> list[list[tuple[int, int]]]:
    todo = [c for c in layout if c[0] >= start]
    return [todo[i:i + BATCH_CHUNKS] for i in range(0, len(todo), BATCH_CHUNKS)]


def _append_batch(client: "Client", collection: str, recording_id: str, batch: list[tuple[int, int]],
                  x: np.ndarray, a: int) -> None:
    """Append one batch of chunks to the server with the file's chunk boundaries, in one round trip."""
    try:
        with client.connection.pipeline():
            for s, n in batch:
                client.connection.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%b)",
                                          (collection, recording_id, s, x[s - a:s - a + n].tobytes()))
    except psycopg.Error as exc:
        raise translate(exc) from exc


def _check(recording_id: str, copied: int, end: int, compared: bool, identical: bool, sender: str,
           receiver: str) -> TransferCheck:
    state = "not compared" if not (compared and copied) else "identical" if identical else "different"
    if state == "different":
        warnings.warn(f"{recording_id}: the samples are identical, but the chunk bytes differ although both sides "
                      f"report the same encoder version ({sender}; {receiver})", RuntimeWarning, stacklevel=3)
    return TransferCheck(recording_id, copied, end, state, sender, receiver)


def import_file(client: "Client", path: str | Path, collection: str,
                recording_ids: Iterable[str] | None = None, report: list | None = None) -> list[Recording]:
    """Copy recordings from a MorletDB file into a server collection (created recordings, or the rest of
    recordings already started there). Runs in the client's transaction; commit to keep it. With a `report`
    list, one TransferCheck per recording is appended to it."""
    path = str(path)
    table, chunks = _names(collection)
    sender, receiver = _file_encoder(path), _server_encoder(client)
    compare = _same_encoder(sender, receiver)
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
            if not _same_prefix(client, collection, chunks, local, path, rid, have):
                raise ConflictError(f"{rid}: the server's first {have} samples differ from the file's")
            identical = True
            for batch in _batches(layout, have):
                a, b = batch[0][0], batch[-1][0] + batch[-1][1]
                x = local.read(rid, a, b).samples
                _append_batch(client, collection, rid, batch, x, a)
                if _server_digests(client, collection, rid, a, b) != _array_digests(x, a, b):
                    raise DatabaseError(f"{rid}: samples of [{a}, {b}) differ after the import")
                if compare:
                    identical &= _server_hash(client, chunks, rid, a, b) == _file_hash(path, rid, a, b)
            out.append(client.recording(collection, rid))
            if report is not None:
                report.append(_check(rid, end - have, out[-1].samples, compare, identical, sender, receiver))
    return out


def export_file(client: "Client", collection: str, path: str | Path,
                recording_ids: Iterable[str] | None = None, report: list | None = None) -> list[Recording]:
    """Copy recordings of a server collection into a MorletDB file (created if missing; recordings already in
    the file are continued). Each batch of chunks is one durable transaction in the file. With a `report`
    list, one TransferCheck per recording is appended to it."""
    path = str(path)
    table, chunks = _names(collection)
    ids = list(recording_ids) if recording_ids is not None else [r.recording_id for r in client.recordings(collection)]
    sender = _server_encoder(client)
    out = []
    with LocalStore(path) as local:
        local.recordings()                  # creates the file if missing, so its writer is recorded
        receiver = _file_encoder(path)
        compare = _same_encoder(sender, receiver)
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
            if rid in known:                    # an existing recording must have the server's parameters (#443)
                k = known[rid]
                fq = next((float(r[4]) for r in _file_rows(path, [rid])), None)
                diff = [f"{name} {mine!r} (server {theirs!r})" for name, mine, theirs in (
                    ("sample_rate_hz", k.sample_rate_hz, rec.sample_rate_hz), ("channel", k.channel, rec.channel),
                    ("unit", k.unit, rec.unit), ("metadata", k.metadata, rec.metadata),
                    ("sample_quantum", fq, float(quantum))) if mine != theirs]
                if diff:
                    raise ConflictError(f"{rid}: the file's recording has other parameters: {'; '.join(diff)}")
            if rid not in known:
                local.ingest_empty(rid, sample_rate_hz=rec.sample_rate_hz, channel=rec.channel, unit=rec.unit,
                                   metadata=rec.metadata, sample_quantum=float(quantum))
            have = known[rid].samples if rid in known else 0
            if have not in {s for s, _ in layout} | {rec.samples}:
                raise ConflictError(f"{rid}: the file holds {have} samples, not a chunk boundary of the server")
            if not _same_prefix(client, collection, chunks, local, path, rid, have):
                raise ConflictError(f"{rid}: the file's first {have} samples differ from the server's")
            identical = True
            for batch in _batches(layout, have):
                a, b = batch[0][0], batch[-1][0] + batch[-1][1]
                x = client.read(collection, rid, a, b).samples
                local.append_chunks(rid, a, x, [n for _, n in batch])
                if _server_digests(client, collection, rid, a, b) != _file_digests(local, rid, a, b):
                    raise DatabaseError(f"{rid}: samples of [{a}, {b}) differ after the export")
                if compare:
                    identical &= _server_hash(client, chunks, rid, a, b) == _file_hash(path, rid, a, b)
            out.append(local.recording(rid))
            if report is not None:
                report.append(_check(rid, rec.samples - have, out[-1].samples, compare, identical, sender, receiver))
    return out
