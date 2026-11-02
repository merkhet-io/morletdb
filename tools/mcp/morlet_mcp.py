#!/usr/bin/env python3
"""Read-only MCP server for MorletDB: lets an AI agent find, compare and read recorded signals.

  DATABASE_URL=postgresql://reader@localhost/signals python tools/mcp/morlet_mcp.py     # stdio

Every tool runs in a read-only transaction with a statement timeout, as the database role in DATABASE_URL,
so privileges and row-level security decide what the agent can see. Nothing is written.
"""
import functools
import json
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

MAX_READ = 32768                 # samples per morlet_read_interval call
MAX_SPAN = 2_000_000             # samples per read_interval / describe_interval request
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """MorletDB stores recordings: uniformly sampled signals (seismic, vibration, audio, EEG, ...) with a
sample rate, channel, unit and JSON metadata, losslessly. Positions are sample offsets (start inclusive, end
exclusive), not times; if a recording's metadata has a start time, tools also report UTC times.

Typical investigation:
1. list_collections, then list_recordings to see what exists (metadata_filter narrows by metadata).
2. Pick an interval of interest (locate_time converts a UTC time to a sample offset).
3. describe_interval to characterise it; search_similar to find where similar behaviour occurred, tolerant
   to time shifts of up to about 7.5% of the window. Distances: 0 is identical, below about 0.3 is strong,
   above about 0.6 is weak. Intervals for search must have the stored window length (usually 4096 samples).
4. read_interval or describe_interval on each hit to check it, and cite hits as collection, recording,
   start_sample..end_sample: they refer to the original stored samples, which read back bit for bit.
Search results are candidates for inspection, not diagnoses."""

mcp = MCPServer("morletdb", instructions=INSTRUCTIONS)


def safe(fn):
    """Errors an agent can act on (bad arguments, unknown names, database refusals) become tool errors with
    the database's message, instead of failing the call."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, psycopg.Error) as exc:
            msg = exc.diag.message_primary if isinstance(exc, psycopg.Error) and exc.diag.message_primary else str(exc)
            hint = exc.diag.message_hint if isinstance(exc, psycopg.Error) else None
            raise ToolError(msg + (f" ({hint})" if hint else "")) from exc
    return wrapper


def connect():
    dsn = os.environ.get("DATABASE_URL", "")
    timeout = int(os.environ.get("MORLET_MCP_TIMEOUT_MS", "30000"))
    conn = psycopg.connect(dsn, options=f"-c default_transaction_read_only=on -c statement_timeout={timeout}")
    conn.read_only = True
    return conn


def collections(conn):
    """Stream collections the role can read: a recordings table with a checkpoint and a _chunks companion."""
    return [r[0] for r in conn.execute("""
        SELECT format('%I.%I', c.table_schema, c.table_name)
          FROM information_schema.columns c
         WHERE c.column_name = 'checkpoint'
           AND EXISTS (SELECT 1 FROM information_schema.columns k WHERE k.table_schema = c.table_schema
                        AND k.table_name = c.table_name AND k.column_name = 'sample_rate_hz')
           AND to_regclass(format('%I.%I', c.table_schema, c.table_name || '_chunks')) IS NOT NULL
           AND has_table_privilege(format('%I.%I', c.table_schema, c.table_name), 'SELECT')
         ORDER BY 1""").fetchall()]


def checked(conn, collection):
    known = collections(conn)
    if collection not in known:
        raise ValueError(f"unknown collection {collection!r}; known: {', '.join(known) or 'none'}")
    return collection


def start_time(meta):
    for key in ("start", "starttime", "start_time"):
        if isinstance(meta, dict) and meta.get(key):
            try:
                return datetime.fromisoformat(str(meta[key]).replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                return None
    return None


def at(t0, rate, sample):
    return None if t0 is None else (t0 + timedelta(seconds=sample / rate)).isoformat().replace("+00:00", "Z")


def recording(conn, collection, recording_id):
    row = conn.execute(f"SELECT sample_rate_hz, channel, unit, metadata, (checkpoint->>'end')::bigint "
                       f"FROM {collection} WHERE recording_id = %s", (recording_id,)).fetchone()
    if row is None:
        raise ValueError(f"recording {recording_id!r} not found in {collection} (or not visible)")
    return {"rate": row[0], "channel": row[1], "unit": row[2], "metadata": row[3], "samples": row[4]}


def read(conn, collection, recording_id, start, end):
    if not 0 <= start < end or end - start > MAX_SPAN:
        raise ValueError(f"interval must satisfy 0 <= start < end and span at most {MAX_SPAN} samples")
    parts = [conn.execute("SELECT samples FROM morlet_read_interval(%s::regclass, %s, %s, %s)",
                          (collection, recording_id, a, min(a + MAX_READ, end))).fetchone()[0]
             for a in range(start, end, MAX_READ)]
    return np.concatenate([np.asarray(p, dtype=np.float64) for p in parts])


def stats(x, rate):
    centred = x - x.mean()
    sd = float(centred.std())
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.abs(x).max())
    out = {"samples": int(x.size), "duration_s": x.size / rate, "mean": float(x.mean()), "std": sd, "rms": rms,
           "min": float(x.min()), "max": float(x.max()), "peak_abs": peak,
           "crest_factor": peak / rms if rms > 0 else None,
           "kurtosis": float(np.mean(centred ** 4) / sd ** 4) if sd > 0 else None}
    if x.size >= 16 and sd > 0:
        spec = np.abs(np.fft.rfft(centred * np.hanning(x.size))) ** 2
        freqs = np.fft.rfftfreq(x.size, 1.0 / rate)
        k = int(np.argmax(spec[1:]) + 1)
        total = spec[1:].sum()
        out["dominant_frequency_hz"] = float(freqs[k])
        out["spectral_centroid_hz"] = float((freqs[1:] * spec[1:]).sum() / total)
        f, p = freqs[1:], spec[1:]                    # without DC; the last band includes Nyquist
        edges = bands(rate)
        out["band_energy_fraction"] = {
            f"{lo:g}-{hi:g} Hz": float(p[(f >= lo) & ((f < hi) | (i == len(edges) - 1) & (f <= hi))].sum() / total)
            for i, (lo, hi) in enumerate(edges)}
    return out


def bands(rate):
    """Five octave-like bands up to Nyquist."""
    nyq = rate / 2
    edges = [0] + [nyq / 2 ** k for k in range(4, -1, -1)]
    return list(zip(edges[:-1], edges[1:]))


def search_paths(conn, c):
    """Stored envelope windows per window size and whether probe search is enabled for it."""
    env = conn.execute("SELECT to_regclass(%s)", (c + "_envelopes",)).fetchone()[0]
    if env is None:
        return []
    out = []
    for w, n in conn.execute(f"SELECT window_size, count(*) FROM {c}_envelopes GROUP BY 1 ORDER BY 1").fetchall():
        probes = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{c}_probes_{w}",)).fetchone()[0]
        out.append({"window_samples": w, "windows": n, "probe_search": probes})
    return out


@mcp.tool(annotations=READ_ONLY)
@safe
def list_collections() -> dict:
    """List the signal collections this role can read, with recording counts, total samples and which
    search paths are available (window sizes with stored envelopes; probe search where enabled)."""
    with connect() as conn:
        out = []
        for c in collections(conn):
            n, total, rates = conn.execute(
                f"SELECT count(*), coalesce(sum((checkpoint->>'end')::bigint), 0), array_agg(DISTINCT sample_rate_hz) "
                f"FROM {c}").fetchone()
            entry = {"collection": c, "recordings": n, "samples": int(total), "sample_rates_hz": sorted(rates or [])}
            entry["search"] = search_paths(conn, c)
            out.append(entry)
        return {"collections": out}


@mcp.tool(annotations=READ_ONLY)
@safe
def list_recordings(collection: str, metadata_filter: str = "{}", limit: int = 50, offset: int = 0) -> dict:
    """List recordings in a collection: id, sample rate, channel, unit, metadata, length and (when the
    metadata has a start time) UTC start and end. metadata_filter is a JSON object matched by containment,
    e.g. '{"station": "PB01"}'."""
    limit = max(1, min(limit, 500))
    flt = json.loads(metadata_filter or "{}")
    if not isinstance(flt, dict):
        raise ValueError("metadata_filter must be a JSON object")
    with connect() as conn:
        c = checked(conn, collection)
        total = conn.execute(f"SELECT count(*) FROM {c} WHERE metadata @> %s::jsonb", (json.dumps(flt),)).fetchone()[0]
        rows = conn.execute(f"SELECT recording_id, sample_rate_hz, channel, unit, metadata, (checkpoint->>'end')::bigint "
                            f"FROM {c} WHERE metadata @> %s::jsonb ORDER BY recording_id LIMIT %s OFFSET %s",
                            (json.dumps(flt), limit, max(0, offset))).fetchall()
    recs = []
    for rid, rate, ch, unit, meta, n in rows:
        t0 = start_time(meta)
        recs.append({"recording_id": rid, "sample_rate_hz": rate, "channel": ch, "unit": unit, "metadata": meta,
                     "samples": n, "duration_s": n / rate, "start_utc": at(t0, rate, 0), "end_utc": at(t0, rate, n)})
    return {"collection": collection, "total": total, "offset": offset, "recordings": recs}


@mcp.tool(annotations=READ_ONLY)
@safe
def locate_time(collection: str, utc_time: str, metadata_filter: str = "{}") -> dict:
    """Find which recordings contain a UTC time (ISO 8601, e.g. 2025-03-01T14:02:10Z) and the sample offset
    of that time in each. Needs recordings whose metadata has a start time."""
    t = datetime.fromisoformat(utc_time.replace("Z", "+00:00")).astimezone(timezone.utc)
    flt = json.loads(metadata_filter or "{}")
    with connect() as conn:
        c = checked(conn, collection)
        rows = conn.execute(f"SELECT recording_id, sample_rate_hz, metadata, (checkpoint->>'end')::bigint FROM {c} "
                            f"WHERE metadata @> %s::jsonb", (json.dumps(flt),)).fetchall()
    hits = []
    for rid, rate, meta, n in rows:
        t0 = start_time(meta)
        if t0 is None:
            continue
        s = math.floor((t - t0).total_seconds() * rate)
        if 0 <= s < n:
            hits.append({"recording_id": rid, "sample": s, "metadata": meta})
    return {"utc_time": utc_time, "matches": sorted(hits, key=lambda h: h["recording_id"])}


@mcp.tool(annotations=READ_ONLY)
@safe
def search_similar(collection: str, recording_id: str, start_sample: int, end_sample: int,
                   metadata_filter: str = "{}", top_k: int = 10) -> dict:
    """Find the intervals most similar to [start_sample, end_sample) of a recording, tolerant to small time
    shifts (bounded-lag cross-correlation of signal envelopes). The interval must have the stored window
    length (usually 4096 samples; see list_collections). Searches recordings with the same sample rate,
    channel and unit whose metadata contains metadata_filter; never returns the query interval itself.
    Each hit has a distance (0 identical, < 0.3 strong) and, where known, UTC times."""
    top_k = max(1, min(top_k, 100))
    flt = json.dumps(json.loads(metadata_filter or "{}"))
    with connect() as conn:
        c = checked(conn, collection)
        src = recording(conn, c, recording_id)
        similar = conn.execute("SELECT to_regproc('public.morlet_search_similar') IS NOT NULL").fetchone()[0]
        if similar:
            sql, args = ("SELECT * FROM morlet_search_similar(%s::regclass, %s, %s, %s, %s::jsonb, %s)",
                         (c, recording_id, start_sample, end_sample, flt, top_k))
        else:                                    # SQL 0.3.1: exact search with an explicit candidate cap
            sql, args = ("SELECT * FROM morlet_search_envelope_interval(%s::regclass, %s, %s, %s, %s::jsonb, %s, "
                         "0.075, true, true, 1000000)", (c, recording_id, start_sample, end_sample, flt, top_k))
        rows = conn.execute(sql, args).fetchall()
        hits = []
        for rid, s, e, d, meta in rows:
            rate = src["rate"]
            t0 = start_time(meta)
            hits.append({"recording_id": rid, "start_sample": s, "end_sample": e, "distance": round(d, 4),
                         "start_utc": at(t0, rate, s), "metadata": meta})
    t0 = start_time(src["metadata"])
    return {"query": {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
                      "end_sample": end_sample, "start_utc": at(t0, src["rate"], start_sample)},
            "path": "morlet_search_similar" if similar else "exact (morlet_search_envelope_interval)", "hits": hits}


@mcp.tool(annotations=READ_ONLY)
@safe
def read_interval(collection: str, recording_id: str, start_sample: int, end_sample: int,
                  max_points: int = 1000) -> dict:
    """Read the original samples of [start_sample, end_sample). Up to max_points values are returned as is;
    longer intervals are summarised as min/max per bucket (an envelope that keeps peaks), with statistics."""
    max_points = max(10, min(max_points, 20000))
    with connect() as conn:
        c = checked(conn, collection)
        rec = recording(conn, c, recording_id)
        x = read(conn, c, recording_id, start_sample, end_sample)
    out = {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
           "end_sample": end_sample, "sample_rate_hz": rec["rate"], "unit": rec["unit"],
           "start_utc": at(start_time(rec["metadata"]), rec["rate"], start_sample)}
    if x.size <= max_points:
        out["samples"] = x.tolist()
    else:
        buckets = np.array_split(x, max_points // 2)
        out["bucket_samples"] = int(math.ceil(x.size / (max_points // 2)))
        out["min_max"] = [[float(b.min()), float(b.max())] for b in buckets]
    out["stats"] = stats(x, rec["rate"])
    return out


@mcp.tool(annotations=READ_ONLY)
@safe
def describe_interval(collection: str, recording_id: str, start_sample: int, end_sample: int) -> dict:
    """Characterise [start_sample, end_sample) of a recording: level (mean, std, rms, min, max, peak),
    impulsiveness (crest factor, kurtosis) and spectrum (dominant frequency, spectral centroid, energy per
    band), in the recording's unit. Compare a hit with its query or with a quiet reference interval."""
    with connect() as conn:
        c = checked(conn, collection)
        rec = recording(conn, c, recording_id)
        x = read(conn, c, recording_id, start_sample, end_sample)
    return {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
            "end_sample": end_sample, "unit": rec["unit"], "sample_rate_hz": rec["rate"],
            "start_utc": at(start_time(rec["metadata"]), rec["rate"], start_sample), **stats(x, rec["rate"])}


if __name__ == "__main__":
    mcp.run("stdio")
