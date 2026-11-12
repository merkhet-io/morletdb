#!/usr/bin/env python3
"""Read-only MCP server for MorletDB: lets an AI agent find, compare and read recorded signals.

  DATABASE_URL=postgresql://reader@localhost/signals python tools/mcp/morlet_mcp.py     # stdio

Every tool runs in a read-only transaction with a statement timeout, as the database role in DATABASE_URL,
so privileges and row-level security decide what the agent can see. Nothing is written.
"""
import functools
import importlib.util
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


def _sibling(name: str):
    """Import a module that sits next to this file, by path.

    `python -I` (isolated mode, used by smoke_stdio.py and recommended for MCP clients) leaves the script's
    directory off sys.path, so a plain `import decision` fails there. Loading by path works in every mode and
    cannot pick up a same-named module from the current directory or the environment."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


decision = _sibling("decision")

MAX_READ = 32768                 # samples per morlet_read_interval call
MAX_SPAN = 2_000_000             # samples per read_interval / describe_interval request
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """MorletDB stores recordings: uniformly sampled signals (seismic, vibration, audio, EEG, ...) with a
sample rate, channel, unit and JSON metadata, losslessly. Positions are sample offsets (start inclusive, end
exclusive), not times; if a recording's metadata has a start time, tools also report UTC times.

Typical investigation:
1. list_collections, then coverage (an overview per station, sensor or asset) or list_recordings to see what
   exists (metadata_filter narrows by metadata); trend shows how a signal changed over days or weeks;
   latest gives the current value of every sensor.
2. Pick an interval of interest (locate_time converts a UTC time to a sample offset).
3. describe_interval to characterise it (compare_intervals against the background for weak signals;
   find_onsets to locate where something starts); search_from_time (when you know when it happened) or
   search_similar to find where similar behaviour occurred, tolerant
   to time shifts of up to about 7.5% of the window. Intervals for search must have the stored window length
   (usually 4096 samples).
   Distance = 1 - the best correlation of the two amplitude envelopes over the allowed shifts. Each window is
   normalised first, so it compares the shape of the amplitude modulation (when energy rises and falls), not
   the level and not the frequency content: different signals can share an envelope shape, and 0 means the
   same envelope shape, not the same samples. Lower is closer; it is not a probability or a confidence, and
   what counts as close differs between signal types, so rank hits against each other and inspect them rather
   than applying a fixed cut-off. A constant window has no envelope: its distance to everything, itself
   included, is 1, and search_similar says so (query_note).
4. read_interval or describe_interval on each hit to check it, and cite hits as collection, recording,
   start_sample..end_sample: they refer to the original stored samples, which read back bit for bit.
Search results are candidates for inspection, not diagnoses.

Not found is not the same as not searched. Search sees only stored search windows. Older data may be readable
but out of search (memory levels), compacted to summaries, or gone, and new data may not be indexed yet. Every
search result has a scope: the recordings and windows searched and the periods that were not searched, with
why (coverage shows the same per group). Whenever a search finds nothing close, or you conclude that something
has not happened before, state the scope with it, e.g. "no similar event in the searched 2024-03..2026-11;
2021-2023 is summarized only and was not searched"."""

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
    """Recording start from metadata, as the server reads it: start_ns (integer UTC nanoseconds, written by the
    Python SDK), else the first of start, starttime, start_time (ISO 8601)."""
    ns = meta.get("start_ns") if isinstance(meta, dict) else None
    if isinstance(ns, int) and not isinstance(ns, bool):
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ns // 1000)
    for key in ("start", "starttime", "start_time"):
        if isinstance(meta, dict) and meta.get(key):
            try:
                return datetime.fromisoformat(str(meta[key]).replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                return None
    return None


def at(t0, rate, sample):
    return None if t0 is None else (t0 + timedelta(seconds=sample / rate)).isoformat().replace("+00:00", "Z")


def time_quality(meta, rate):
    """How a recording's UTC times are derived, and how far off they may be (#393). Today always start + rate: there
    are no time anchors yet. When an import measured how the source's own timestamps departed from that axis
    (metadata time_axis, tools/ingest), that departure is the uncertainty of every derived time."""
    q = {"time_basis": "start + rate"}
    ax = meta.get("time_axis") if isinstance(meta, dict) else None
    off = ax.get("max_offset_s") if isinstance(ax, dict) else None
    if isinstance(off, (int, float)) and not isinstance(off, bool) and math.isfinite(off):
        q["time_uncertainty_s"] = off
        q["time_uncertainty_samples"] = round(off * rate, 2)
        if isinstance(ax.get("fitted_rate_hz"), (int, float)):
            q["fitted_rate_hz"] = ax["fitted_rate_hz"]
    return q


def occurrences_at(conn, collection, points):
    """The occurrence (e.g. {"part": "P12"}) of the narrowest current context segment containing each
    (recording_id, sample); points outside every segment, or with an empty occurrence, are left out."""
    try:
        seg_table = conn.execute("SELECT segments::text FROM morlet_context_relations(%s::regclass)",
                                 (collection,)).fetchone()[0]
    except psycopg.Error as e:
        raise ValueError("exclude=occurrence needs a collection with context (morlet_enable_context)") from e
    rids, samples = [p[0] for p in points], [int(p[1]) for p in points]
    out = {}
    for rid, sample, occ in conn.execute(
            f"SELECT p.rid, p.sample, (SELECT g.occurrence FROM {seg_table} g WHERE g.recording_id = p.rid "
            f"AND g.start_sample <= p.sample AND p.sample < g.end_sample AND NOT g.stale "
            f"ORDER BY g.end_sample - g.start_sample, g.segment_id LIMIT 1) "
            f"FROM unnest(%s::text[], %s::bigint[]) AS p(rid, sample)", (rids, samples)):
        if occ:
            out[(rid, sample)] = json.dumps(occ, sort_keys=True)
    return out


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


def stats(x, rate, edges=None, min_hz=0.0):
    """Level, impulsiveness and spectrum of x. Band power is each band's share of the variance, in unit²,
    so intervals of different lengths compare. min_hz excludes lower frequencies (for example ocean
    microseism or machine drift) from the dominant frequency and centroid; bands are unaffected."""
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
        f, p = freqs[1:], spec[1:]                    # without DC
        total = p.sum()
        sel = f >= min_hz
        if sel.any() and p[sel].sum() > 0:
            out["dominant_frequency_hz"] = float(f[sel][np.argmax(p[sel])])
            out["spectral_centroid_hz"] = float((f[sel] * p[sel]).sum() / p[sel].sum())
        if min_hz > 0:
            out["min_frequency_hz"] = min_hz
        edges = edges or bands(rate)
        frac = {}
        for i, (lo, hi) in enumerate(edges):          # the top band includes its upper edge (Nyquist)
            m = (f >= lo) & ((f < hi) | ((i == len(edges) - 1) & (f <= hi)))
            frac[f"{lo:g}-{hi:g} Hz"] = float(p[m].sum() / total)
        out["band_energy_fraction"] = frac
        out["band_power"] = {k: v * sd * sd for k, v in frac.items()}
    return out


def parse_bands(text, rate):
    """'1-5,5-10,10-20' (Hz) to [(1, 5), (5, 10), (10, 20)]; empty gives the default octave-like bands."""
    if not text or not text.strip():
        return None
    out = []
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        lo, hi = float(lo), float(hi)
        if not 0 <= lo < hi <= rate / 2:
            raise ValueError(f"band {part.strip()!r} must satisfy 0 <= low < high <= {rate / 2:g} Hz (Nyquist)")
        out.append((lo, hi))
    return out


def bands(rate):
    """Five octave-like bands up to Nyquist."""
    nyq = rate / 2
    edges = [0] + [nyq / 2 ** k for k in range(4, -1, -1)]
    return list(zip(edges[:-1], edges[1:]))


def bandpass(x, rate, lo, hi):
    """Zero-phase FFT band-pass (lo or hi of 0 leaves that side open). The signal is mirrored first, so its
    ends meet without a jump that would leak strong slow motion into the pass band."""
    x = x - x.mean()
    if not lo and not hi:
        return x
    y = np.concatenate([x, x[::-1]])
    spec = np.fft.rfft(y)
    f = np.fft.rfftfreq(y.size, 1.0 / rate)
    keep = (f >= (lo or 0)) & ((f <= hi) if hi else True)
    return np.fft.irfft(spec * keep, n=y.size)[:x.size]


def sta_lta(x, rate, sta_s, lta_s):
    """Ratio of short-term to long-term mean energy; ratio[i] compares the sta window ending at sample i with
    the lta window just before it. Undefined (0) until both windows are filled."""
    ns, nl = max(1, round(sta_s * rate)), max(2, round(lta_s * rate))
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    ratio = np.zeros(x.size)
    i = np.arange(ns + nl, x.size + 1)
    sta = (c[i] - c[i - ns]) / ns
    lta = (c[i - ns] - c[i - ns - nl]) / nl
    ratio[i - 1] = np.where(lta > 0, sta / np.maximum(lta, 1e-300), 0.0)
    return ratio


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


def recordings_in(conn, c, flt):
    rows = conn.execute(f"SELECT recording_id, sample_rate_hz, metadata, (checkpoint->>'end')::bigint FROM {c} "
                        f"WHERE metadata @> %s::jsonb", (json.dumps(flt),)).fetchall()
    out = []
    for rid, rate, meta, n in rows:
        t0 = start_time(meta)
        if t0 is not None and n:
            out.append({"recording_id": rid, "rate": rate, "metadata": meta, "samples": n, "t0": t0,
                        "t1": t0 + timedelta(seconds=n / rate)})
    return sorted(out, key=lambda r: r["t0"])


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


@mcp.tool(annotations=READ_ONLY)
@safe
def coverage(collection: str, group_by: str = "", metadata_filter: str = "{}", max_gaps: int = 10) -> dict:
    """Overview of what is recorded, per group of recordings (group_by: a metadata key such as "station" or
    "bearing"; empty for one group): number of recordings, samples, first and last UTC time, the recording
    schedule (typical recording length and time between recording starts), time covered, and the largest
    gaps. Recordings that overlap in time (channels of one acquisition) count as one recorded block, so
    covered time is never counted twice. For continuous recording every pause between blocks is a gap; for snapshots (recordings
    shorter than the usual pause, for example 1 s every 10 min) only pauses longer than 1.5 times the usual
    one are. Needs recordings with a start time. On servers with SQL 0.3.6, search_states_s splits each
    group's recorded time by what search can find: searchable, not_indexed (no search window built yet),
    not_searchable (readable, out of search by a memory level), represented, summarized, gone, missing."""
    flt = json.loads(metadata_filter or "{}")
    with connect() as conn:
        c = checked(conn, collection)
        recs = recordings_in(conn, c, flt)
        states = None
        if conn.execute("SELECT to_regproc('public.morlet_coverage') IS NOT NULL").fetchone()[0]:
            states = {}
            for rid, state, n in conn.execute(
                    "SELECT recording_id, state, sum(end_sample - start_sample) FROM morlet_coverage(%s::regclass, "
                    "%s::text[]) GROUP BY 1, 2", (c, [r["recording_id"] for r in recs])).fetchall():
                states[(rid, state)] = int(n)
    groups = {}
    for r in recs:
        groups.setdefault(str(r["metadata"].get(group_by, "")) if group_by else "all", []).append(r)
    out = []
    for name, rs in sorted(groups.items()):
        # Recordings that overlap in time (several channels of one acquisition) are one block of recorded time;
        # adding them up would count the same seconds once per channel.
        blocks = []
        for r in rs:                                         # rs is sorted by start time
            if blocks and r["t0"] < blocks[-1]["t1"]:
                blocks[-1]["t1"] = max(blocks[-1]["t1"], r["t1"])
                blocks[-1]["recordings"] += 1
            else:
                blocks.append({"t0": r["t0"], "t1": r["t1"], "recordings": 1})
        dur = sorted((b["t1"] - b["t0"]).total_seconds() for b in blocks)
        starts = [(b["t0"] - a["t0"]).total_seconds() for a, b in zip(blocks, blocks[1:])]
        pauses = [((b["t0"] - a["t1"]).total_seconds(), a, b) for a, b in zip(blocks, blocks[1:])]
        usual = float(np.median([p for p, _, _ in pauses])) if pauses else 0.0
        snapshots = bool(pauses) and float(np.median(dur)) < usual     # short recordings on a schedule
        limit = max(1.5 * usual, usual + 1.0) if snapshots else 1.0     # continuous: every pause is a gap
        gaps = sorted((p for p in pauses if p[0] > limit), key=lambda p: -p[0])
        span = (max(b["t1"] for b in blocks) - blocks[0]["t0"]).total_seconds()
        covered = sum(dur)
        out.append({"group": name, "recordings": len(rs), "samples": int(sum(r["samples"] for r in rs)),
                    "recorded_blocks": len(blocks),
                    "recordings_per_block": float(np.median([b["recordings"] for b in blocks])),
                    "first_utc": iso(rs[0]["t0"]), "last_utc": iso(max(b["t1"] for b in blocks)),
                    "schedule": "snapshots" if snapshots else "continuous",
                    "typical_recording_s": float(np.median(dur)),
                    "typical_start_interval_s": float(np.median(starts)) if starts else None,
                    "covered_s": covered, "span_s": span, "covered_fraction": covered / span if span else 1.0,
                    "gaps": len(gaps), "gap_s": float(sum(g[0] for g in gaps)),
                    "largest_gaps": [{"from_utc": iso(a["t1"]), "to_utc": iso(b["t0"]), "seconds": round(p, 1)}
                                     for p, a, b in gaps[:max(0, min(max_gaps, 50))]]})
        if states is not None:
            out[-1]["search_states_s"] = {k: round(v, 3) for k, v in sorted(
                (k, sum(states.get((r["recording_id"], k), 0) / r["rate"] for r in rs))
                for k in {k for (_, k) in states}) if v}
    return {"collection": collection, "group_by": group_by or None, "groups": out}


@mcp.tool(annotations=READ_ONLY)
@safe
def latest(collection: str, metadata_filter: str = "{}", samples: int = 1, limit: int = 100) -> dict:
    """The newest data of every recording whose metadata contains metadata_filter: "what is the current value
    of each sensor?". samples=1 (default) gives each recording's last value; up to 1000 gives its last values
    with their statistics (rms, min, max, mean), for example the last second (samples = the sample rate);
    samples=0 gives the statistics of the newest stored chunk instead, without values. Each row has the end
    (UTC where the recording has a start time) and how many seconds before now that was; a recording that
    stopped long ago is not live. When the newest samples are not readable (compacted, or a gap), the row says
    why instead of giving values. More than limit (max 1000) matching recordings is an error: narrow the
    filter. Servers with pg_morlet SQL 0.3.6."""
    n = max(0, min(int(samples), 1000))
    limit = max(1, min(int(limit), 1000))
    flt = json.dumps(json.loads(metadata_filter or "{}"))
    with connect() as conn:
        c = checked(conn, collection)
        if not conn.execute("SELECT to_regproc('public.morlet_latest') IS NOT NULL").fetchone()[0]:
            raise ValueError("latest needs a server with pg_morlet SQL 0.3.6")
        try:
            rows, now = conn.execute(
                "SELECT recording_id, sample_rate_hz, channel, unit, start_sample, end_sample, end_time, samples, "
                "sample_count, min, max, mean, rms, note FROM morlet_latest(%s::regclass, %s, %s::jsonb, %s)",
                (c, n, flt, limit)).fetchall(), datetime.now(timezone.utc)
        except psycopg.Error as e:
            # the SQL function names its own argument (max_recordings); the caller of this tool passed limit
            msg = getattr(getattr(e, "diag", None), "message_primary", None) or str(e)
            if "max_recordings" in msg:
                matched = msg.split(" recordings match")[0].strip()
                raise ValueError(f"{matched} recordings match, more than limit ({limit}). Narrow metadata_filter "
                                 f"or pass a larger limit (at most 1000).") from None
            raise
    out = []
    for rid, rate, ch, unit, a, b, end, values, count, lo, hi, mean, rms, note in rows:
        row = {"recording_id": rid, "channel": ch, "unit": unit, "sample_rate_hz": rate, "start_sample": a,
               "end_sample": b, "end_utc": iso(end) if end else None,
               "seconds_ago": round((now - end).total_seconds(), 3) if end else None}
        if note:
            row["note"] = note
        if values:
            row["last_value"] = values[-1]
            if n > 1:
                row["values"] = values
        if count is not None and (n != 1 or values is None):
            row["stats"] = {"samples": count, "rms": rms, "min": lo, "max": hi, "mean": mean}
        out.append(row)
    return {"collection": collection, "samples": n, "recordings": out}


def change_point(values, baseline_n, k=3, z=5.0):
    """First index after the baseline from which k consecutive values lie more than z robust standard
    deviations (1.4826 MAD) above the baseline median; None if never."""
    v = np.asarray(values, dtype=float)
    base = v[:baseline_n]
    med = float(np.median(base))
    mad = float(np.median(np.abs(base - med))) * 1.4826 or 1e-12
    high = (v - med) / mad > z
    for i in range(baseline_n, len(v) - k + 1):
        if high[i:i + k].all():
            return i, med, mad
    return None, med, mad


def sig(v, digits=4):
    return None if v is None else float(f"{v:.{digits}g}")


@mcp.tool(annotations=READ_ONLY)
@safe
def trend(collection: str, metadata_filter: str, start_utc: str = "", end_utc: str = "", max_points: int = 200,
          samples_per_point: int = 4096, step_seconds: float = 0.0, bands: str = "",
          min_frequency_hz: float = 0.0, baseline_fraction: float = 0.1, segment_context: str = "") -> dict:
    """How a signal's level and spectrum change over time, for the recordings matching metadata_filter (for
    example one sensor or bearing). Takes up to max_points (at most 300) measurement points in time order: one per
    recording for short snapshots, or one every step_seconds within longer recordings, each over
    samples_per_point samples. segment_context (a JSON object, e.g. '{"phase": "cut"}') measures only inside the
    context segments whose context contains it, from each segment's start, so that the idle lead-in before a cut
    is not what the trend describes; a segment shorter than samples_per_point is measured whole (needs a
    collection with context and built segments). Each point gives rms, std, peak, crest factor, kurtosis and power per band
    (bands as in describe_interval; min_frequency_hz high-passes before measuring), returned as one CSV
    table; the number of points is lowered automatically to keep the answer short. change_points gives, per measure, the first point from which three
    in a row lie more than 5 robust standard deviations above the first baseline_fraction of points: a
    candidate for when something changed, to inspect, not a diagnosis."""
    flt = json.loads(metadata_filter or "{}")
    t_lo = datetime.fromisoformat(start_utc.replace("Z", "+00:00")) if start_utc else None
    t_hi = datetime.fromisoformat(end_utc.replace("Z", "+00:00")) if end_utc else None
    max_points = max(10, min(max_points, 300))     # keeps the answer within MCP clients' output limits
    with connect() as conn:
        c = checked(conn, collection)
        recs = recordings_in(conn, c, flt)
        if not recs:
            raise ValueError("no recordings with a start time match metadata_filter")
        rates = {r["rate"] for r in recs}
        if len(rates) > 1:
            raise ValueError(f"matching recordings have several sample rates ({sorted(rates)}); narrow the filter")
        rate = rates.pop()
        n = max(16, samples_per_point)
        seg_flt = json.loads(segment_context or "{}")
        if not isinstance(seg_flt, dict):
            raise ValueError("segment_context must be a JSON object")
        spans = {r["recording_id"]: [(0, r["samples"])] for r in recs}
        if seg_flt:                                       # measure inside matching context segments only (#385)
            try:
                seg_table = conn.execute("SELECT segments::text FROM morlet_context_relations(%s::regclass)",
                                         (c,)).fetchone()[0]
            except psycopg.Error as e:
                raise ValueError("segment_context needs a collection with context (morlet_enable_context)") from e
            spans = {rid: [] for rid in spans}
            for rid, a, b in conn.execute(
                    f"SELECT recording_id, start_sample, end_sample FROM {seg_table} WHERE NOT stale "
                    f"AND context @> %s::jsonb AND recording_id = ANY(%s) ORDER BY recording_id, start_sample",
                    (json.dumps(seg_flt), list(spans))):
                spans[rid].append((a, b))
            if not any(spans.values()):
                raise ValueError(f"no current context segment of the matching recordings has context {seg_flt}")
        points = []
        for r in recs:
            step = int(step_seconds * rate) if step_seconds > 0 else r["samples"]
            for a, b in spans[r["recording_id"]]:
                m = min(n, b - a)
                if m < 16:
                    continue
                for s0 in range(a, max(a + 1, b - m + 1), max(m, step)):
                    t = r["t0"] + timedelta(seconds=s0 / rate)
                    if (t_lo and t < t_lo) or (t_hi and t >= t_hi):
                        continue
                    points.append((t, r["recording_id"], s0, m))
        edges = parse_bands(bands, rate)
        n_bands = len(edges) if edges else 5
        budget = max(10, 30000 // (60 + 9 * (5 + n_bands)))     # keep the table within about 30,000 characters
        max_points = min(max_points, budget)
        if len(points) > max_points:
            idx = np.linspace(0, len(points) - 1, max_points).round().astype(int)
            points = [points[i] for i in sorted(set(idx))]
        cols = {"utc": [], "recording_id": [], "start_sample": [], "rms": [], "std": [], "peak_abs": [],
                "crest_factor": [], "kurtosis": []}
        band_cols = {}
        for t, rid, s0, m in points:
            x = read(conn, c, rid, s0, s0 + m)
            if min_frequency_hz > 0:
                x = bandpass(x, rate, min_frequency_hz, 0.0)
            st = stats(x, rate, edges)
            cols["utc"].append(iso(t)); cols["recording_id"].append(rid); cols["start_sample"].append(s0)
            for k in ("rms", "std", "peak_abs", "crest_factor", "kurtosis"):
                cols[k].append(sig(st.get(k)))
            for k, v in st.get("band_power", {}).items():
                band_cols.setdefault(k, []).append(sig(v))
    base_n = max(3, int(len(points) * baseline_fraction))
    changes = {}
    for name, vals in [(k, cols[k]) for k in ("rms", "kurtosis", "crest_factor")] + list(band_cols.items()):
        if len(vals) < base_n + 3 or any(v is None for v in vals):
            continue
        i, med, mad = change_point(vals, base_n)
        changes[name] = {"baseline_median": sig(med), "first_change_utc": cols["utc"][i] if i is not None else None,
                         "point": i, "value_then": vals[i] if i is not None else None}
    names = ["utc", "recording_id", "start_sample", "rms", "std", "peak_abs", "crest_factor", "kurtosis"]
    header = names + [f"power {k}" for k in band_cols]
    rows = [",".join("" if v is None else str(v) for v in
                     [cols[k][i] for k in names] + [band_cols[k][i] for k in band_cols]) for i in range(len(points))]
    out_seg = {"segment_context": seg_flt} if seg_flt else {}
    return {"collection": collection, "metadata_filter": flt, **out_seg, "points": len(points),
            "samples_per_point": n, "sample_rate_hz": rate, "baseline_points": base_n, "change_points": changes,
            "table_csv": "\n".join([",".join(header)] + rows)}


@mcp.tool(annotations=READ_ONLY)
@safe
def list_recordings(collection: str, metadata_filter: str = "{}", limit: int = 50, offset: int = 0,
                    include_metadata: bool = False) -> dict:
    """List recordings in a collection: id, length and (when the metadata has a start time) UTC start and
    end; rate, channel, unit and metadata are given once when all listed recordings share them, otherwise
    per recording (metadata only with include_metadata). metadata_filter is a JSON object matched by
    containment, e.g. '{"station": "PB01"}'. For an overview of many recordings, coverage is shorter."""
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
    recs, shared = [], {}
    for key, idx in (("sample_rate_hz", 1), ("channel", 2), ("unit", 3)):
        values = {r[idx] for r in rows}
        if len(values) == 1:
            shared[key] = values.pop()
    for rid, rate, ch, unit, meta, n in rows:
        t0 = start_time(meta)
        rec = {"recording_id": rid, "samples": n, "duration_s": n / rate, "start_utc": at(t0, rate, 0),
               "end_utc": at(t0, rate, n)}
        for key, value in (("sample_rate_hz", rate), ("channel", ch), ("unit", unit)):
            if key not in shared:
                rec[key] = value
        if include_metadata:
            rec["metadata"] = meta
        recs.append(rec)
    return {"collection": collection, "total": total, "offset": offset, **shared, "recordings": recs}


@mcp.tool(annotations=READ_ONLY)
@safe
def locate_time(collection: str, utc_time: str, metadata_filter: str = "{}") -> dict:
    """Find which recordings contain a UTC time (ISO 8601, e.g. 2025-03-01T14:02:10Z) and the sample offset
    of that time in each. Needs recordings whose metadata has a start time. When no recording contains the
    time, a note says whether it falls into a gap, before the first or after the last recording, with the
    nearest recording on each side. Each match says how its time axis is derived (time_basis: start + rate)
    and, when the import measured how the source's timestamps departed from it, the uncertainty in seconds and
    samples: a clock that drifts or stamps blocks puts the located sample off by up to that much."""
    t = datetime.fromisoformat(utc_time.replace("Z", "+00:00")).astimezone(timezone.utc)
    flt = json.loads(metadata_filter or "{}")
    with connect() as conn:
        c = checked(conn, collection)
        rows = conn.execute(f"SELECT recording_id, sample_rate_hz, metadata, (checkpoint->>'end')::bigint FROM {c} "
                            f"WHERE metadata @> %s::jsonb", (json.dumps(flt),)).fetchall()
    hits, before, after, timed = [], None, None, 0
    for rid, rate, meta, n in rows:
        t0 = start_time(meta)
        if t0 is None:
            continue
        timed += 1
        s = math.floor((t - t0).total_seconds() * rate)
        if 0 <= s < n:
            hit = {"recording_id": rid, "sample": s, **time_quality(meta, rate), "metadata": meta}
            if hit.get("time_uncertainty_samples", 0) > 1:
                hit["time_note"] = (f"the source's timestamps departed from start + rate by up to "
                                    f"{hit['time_uncertainty_s']:.3g} s, so this sample may be off by up to "
                                    f"{hit['time_uncertainty_samples']:.4g} samples (no time anchors yet, #393)")
            hits.append(hit)
            continue
        t1 = t0 + timedelta(seconds=n / rate)
        if t1 <= t and (before is None or t1 > before[1]):
            before = (rid, t1)
        if t0 > t and (after is None or t0 < after[1]):
            after = (rid, t0)
    out = {"utc_time": utc_time, "matches": sorted(hits, key=lambda h: h["recording_id"])}
    if not hits:
        if not timed:
            out["note"] = "no matching recording has a start time, so no time can be located"
        else:
            out["note"] = ("the time falls into a gap between recordings" if before and after else
                           "the time is before the first recording" if after else "the time is after the last recording")
            if before:
                out["previous"] = {"recording_id": before[0], "end_utc": iso(before[1]),
                                   "seconds_before": round((t - before[1]).total_seconds(), 3)}
            if after:
                out["next"] = {"recording_id": after[0], "start_utc": iso(after[1]),
                               "seconds_after": round((after[1] - t).total_seconds(), 3)}
    return out


def query_note(x):
    """A note for a query window whose distances carry no information, or None. A constant window has an
    all-zero envelope, so its distance to every window, itself included, is 1 (no correlation)."""
    if x.size and np.ptp(x) == 0:
        return ("the query interval is constant, so it has no envelope: every distance is 1 and the ranking "
                "is arbitrary. Choose an interval where the signal varies.")
    return None


def merge_hits(runs, query_recording, query_lo, query_hi):
    """Merge the hit lists of shifted queries: best distance first; a hit is dropped if it overlaps the query
    span (any shift) in the query's recording, or overlaps a better hit already kept in the same recording."""
    kept = []
    for h in sorted((h for run in runs for h in run), key=lambda h: (h["distance"], h["recording_id"], h["start_sample"])):
        if h["recording_id"] == query_recording and h["start_sample"] < query_hi and h["end_sample"] > query_lo:
            continue
        if any(k["recording_id"] == h["recording_id"] and h["start_sample"] < k["end_sample"]
               and h["end_sample"] > k["start_sample"] for k in kept):
            continue
        kept.append(h)
    return kept


NOT_SEARCHED = {   # morlet_coverage states other than searchable (SQL 0.3.6, #215)
    "not_indexed": "stored and readable, but no search window was built there yet",
    "not_searchable": "readable, but taken out of search by a memory level (L1 or above)",
    "represented": "compacted to L2: only its cluster's representative can be found",
    "summarized": "compacted to L3: chunk summaries only",
    "gone": "compacted to L4: no longer in the database",
    "missing": "never stored (a gap)",
}


def summarize_scope(ranges, bounds, rate, t0s, max_periods=10, window=None):
    """Totals per state and the largest not-searched periods from morlet_coverage rows (recording_id, start, end,
    state), each clipped to its recording's searched bounds {recording_id: (lo, hi)}. An unindexed range shorter
    than one window (typically a recording's last samples) cannot hold a window: it is counted as
    shorter_than_window and does not make the scope incomplete."""
    samples, periods = {}, []
    for rid, a, b, state in ranges:
        lo, hi = bounds.get(rid, (None, None))
        if lo is None:
            continue
        a, b = max(a, lo), min(b, hi)
        if a >= b:
            continue
        if state == "not_indexed" and window and b - a < window:
            state = "shorter_than_window"
        samples[state] = samples.get(state, 0) + (b - a)
        if state not in ("searchable", "shorter_than_window"):
            periods.append({"recording_id": rid, "start_sample": a, "end_sample": b, "state": state,
                            "start_utc": at(t0s.get(rid), rate, a), "end_utc": at(t0s.get(rid), rate, b)})
    periods.sort(key=lambda p: (-(p["end_sample"] - p["start_sample"]), p["recording_id"], p["start_sample"]))
    not_searched = sum(n for k, n in samples.items() if k not in ("searchable", "shorter_than_window"))
    return {"seconds": {k: round(n / rate, 3) for k, n in sorted(samples.items())},
            "not_searched_seconds": round(not_searched / rate, 3), "complete": not_searched == 0,
            "not_searched_periods": periods[:max(0, max_periods)], "not_searched_period_count": len(periods)}


SCOPE_TIMEOUT_MS = int(os.environ.get("MORLET_MCP_SCOPE_TIMEOUT_MS", "5000"))   # morlet_coverage: ~1.3 us per window


def search_scope(conn, c, src, flt, window, time_bounds=(None, None), before=None):
    """search_scope_exact within SCOPE_TIMEOUT_MS; past it, a scope that says it is unknown (never complete)."""
    try:
        with conn.transaction():                 # a savepoint: a cancelled scope leaves the search intact
            conn.execute(f"SET LOCAL statement_timeout = {max(1, SCOPE_TIMEOUT_MS)}")
            return search_scope_exact(conn, c, src, flt, window, time_bounds, before)
    except psycopg.errors.QueryCanceled:
        return {"window_samples": window, "complete": None,
                "note": f"the search scope could not be computed within {SCOPE_TIMEOUT_MS / 1000:g} s, so it is "
                        "unknown which periods were searched: treat a missing match as unverified, and use coverage "
                        "for the collection's search states"}


def search_scope_exact(conn, c, src, flt, window, time_bounds=(None, None), before=None):
    """What a search over collection c actually searched: the recordings it could match (same rate, channel and
    unit as the query, metadata containing flt, inside the time bounds), the stored windows of its length there,
    and, on servers with morlet_coverage, the periods that exist but were not searched and why."""
    rows = conn.execute(f"SELECT recording_id, metadata, (checkpoint->>'end')::bigint FROM {c} WHERE metadata @> %s::jsonb "
                        f"AND sample_rate_hz = %s AND channel = %s AND unit = %s",
                        (flt, src["rate"], src["channel"], src["unit"])).fetchall()
    timed = before is not None or any(t is not None for t in time_bounds)
    bounds, t0s, untimed = {}, {}, 0
    for rid, meta, n in rows:
        t0 = start_time(meta)
        t0s[rid] = t0
        lo, hi = 0, n or 0
        if timed:
            if t0 is None:
                untimed += 1
                continue
            sec = lambda t: (t - t0).total_seconds() * src["rate"]
            if time_bounds[0] is not None:
                lo = max(lo, math.ceil(sec(time_bounds[0])))
            if time_bounds[1] is not None:
                hi = min(hi, math.floor(sec(time_bounds[1])))
            if before is not None:
                hi = min(hi, math.floor(sec(before)))
        if hi > lo:
            bounds[rid] = (lo, hi)
    ids = sorted(bounds)
    out = {"window_samples": window, "recordings": len(ids), "searched_windows": 0}
    if untimed:
        out["recordings_left_out_without_start_time"] = untimed
    env = conn.execute("SELECT to_regclass(%s)", (c + "_envelopes",)).fetchone()[0]
    if env is not None and ids:
        out["searched_windows"] = conn.execute(
            f"SELECT count(*) FROM {c}_envelopes v JOIN unnest(%s::text[], %s::bigint[], %s::bigint[]) b(rid, lo, hi) "
            f"ON v.recording_id = b.rid WHERE v.window_size = %s AND v.start_sample >= b.lo "
            f"AND v.start_sample + v.window_size <= b.hi",
            (ids, [bounds[i][0] for i in ids], [bounds[i][1] for i in ids], window)).fetchone()[0]
    if not conn.execute("SELECT to_regproc('public.morlet_coverage') IS NOT NULL").fetchone()[0]:
        out["note"] = ("this server (before pg_morlet SQL 0.3.6) cannot report which periods were not searched; "
                       "a search without a match only means no match among the searched windows")
        return out
    ranges = conn.execute("SELECT recording_id, start_sample, end_sample, state FROM morlet_coverage(%s::regclass, "
                          "%s::text[], %s)", (c, ids, window)).fetchall() if ids else []
    out.update(summarize_scope(ranges, bounds, src["rate"], t0s, window=window))
    mem = conn.execute("SELECT to_regclass(%s)", (c + "_episodes",)).fetchone()[0]
    if mem is not None and ids:
        lib = conn.execute(
            f"SELECT count(*) FILTER (WHERE e.level = 0), count(*) FILTER (WHERE e.level > 0) FROM {c}_episodes e "
            f"JOIN unnest(%s::text[], %s::bigint[], %s::bigint[]) b(rid, lo, hi) ON e.recording_id = b.rid "
            f"WHERE e.exempt AND e.start_sample < b.hi AND e.end_sample > b.lo",
            (ids, [bounds[i][0] for i in ids], [bounds[i][1] for i in ids])).fetchone()
        out["event_library"] = {"entries": lib[0] + lib[1], "at_full_level": lib[0], "out_of_search": lib[1]}
    out["note"] = ("every period of the matching recordings was searchable" if out["complete"] else
                   f"{out['not_searched_seconds']:g} s of the matching recordings were not searched "
                   f"({', '.join(k for k in out['seconds'] if k in NOT_SEARCHED)}): a missing match says nothing "
                   f"about those periods")
    return out


@mcp.tool(annotations=READ_ONLY)
@safe
def search_similar(collection: str, recording_id: str, start_sample: int, end_sample: int,
                   metadata_filter: str = "{}", top_k: int = 10, shifts: int = 0, before_only: bool = False,
                   time_from: str = "", time_to: str = "", evidence: bool = True, exclude: str = "") -> dict:
    """Find the intervals most similar to [start_sample, end_sample) of a recording, tolerant to small time
    shifts (bounded-lag cross-correlation of signal envelopes). The interval must have the stored window
    length (usually 4096 samples; see list_collections). Searches recordings with the same sample rate,
    channel and unit whose metadata contains metadata_filter; never returns the query interval itself.
    Results depend on where the window starts, in ways that are hard to predict. When you know when
    something happened, use search_from_time, which frames the window itself. Here, shifts=2 also
    searches with the window moved by 1/8 and 2/8 of its length either way and merges the results,
    keeping each hit's best distance; on seismic test data this found more related events than any single
    placement. shifts=0 (default) is the setting the published evaluations used. Each hit has a distance
    (lower is closer; 0 = same envelope shape; not a probability, see the server instructions), UTC times where known,
    and the query shift that found it; metadata is given once per recording.
    before_only=True keeps only intervals that end before the query starts ("has this happened before?");
    time_from / time_to (UTC, ISO 8601) keep intervals inside that range. Both need recordings with start
    times and leave out recordings without one. With evidence (default), each hit also carries the
    matched interval's rms, min, max and an 8-part min/max sketch, so you can judge it without reading it
    (servers with SQL 0.3.3). scope says what was searched: the matching recordings, the stored windows
    searched, and (servers with SQL 0.3.6) the periods that exist but were not searched, with why; report it
    with every negative or thin result.
    exclude leaves out hits from the query's own context: "occurrence" drops hits from the same part, cycle or
    run (the occurrence of the context segment that contains the query, e.g. other channels or other windows of
    the same part) but keeps earlier parts in the same recording, the usual question in series production;
    "recording" drops every hit in the query's
    recording (not only the overlapping windows); "metadata:<key>" drops hits from every recording whose
    metadata <key> equals the query's (e.g. "metadata:part" for the other channels of the same part in
    series production). Up to 100 candidates are fetched so that up to top_k hits remain; the query reports how
    many were left out."""
    top_k = max(1, min(top_k, 100))
    if exclude and exclude not in ("recording", "occurrence") and not exclude.startswith("metadata:"):
        raise ValueError('exclude must be "", "recording", "occurrence" or "metadata:<key>"')
    fetch_k = min(100, top_k * 5 + 20) if exclude else top_k      # the SQL search returns at most 100
    shifts = max(0, min(shifts, 4))
    flt = json.dumps(json.loads(metadata_filter or "{}"))
    window = end_sample - start_sample
    step = max(1, window // 8)
    with connect() as conn:
        c = checked(conn, collection)
        src = recording(conn, c, recording_id)
        note = query_note(read(conn, c, recording_id, start_sample, end_sample)) if 0 < window <= MAX_READ else None
        similar = conn.execute("SELECT to_regproc('public.morlet_search_similar') IS NOT NULL").fetchone()[0]
        v033 = conn.execute("SELECT to_regproc('public.morlet_search_similar_evidence') IS NOT NULL").fetchone()[0]
        if (before_only or time_from or time_to) and not v033:
            raise ValueError("before_only and time ranges need a server with pg_morlet SQL 0.3.3")
        bounds = [datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None for t in (time_from, time_to)]
        if any(b is not None and b.tzinfo is None for b in bounds):
            raise ValueError("time_from and time_to need a time zone, e.g. 2026-03-01T00:00:00Z")
        runs, metas, used = [], {}, []
        for k in range(-shifts, shifts + 1):
            a, b = start_sample + k * step, end_sample + k * step
            if a < 0 or b > src["samples"]:
                continue
            used.append(k * step)
            if v033:
                sql, args = ("SELECT recording_id, start_sample, end_sample, distance, metadata, rms, min, max, overview "
                             "FROM morlet_search_similar_evidence(%s::regclass, %s, %s, %s, %s::jsonb, %s, "
                             "time_from => %s, time_to => %s, before_only => %s, overview_buckets => 8)",
                             (c, recording_id, a, b, flt, fetch_k, bounds[0], bounds[1], before_only))
            elif similar:
                sql, args = ("SELECT *, NULL, NULL, NULL, NULL FROM morlet_search_similar(%s::regclass, %s, %s, %s, "
                             "%s::jsonb, %s)", (c, recording_id, a, b, flt, fetch_k))
            else:                                # SQL 0.3.1: exact search with an explicit candidate cap
                sql, args = ("SELECT *, NULL, NULL, NULL, NULL FROM morlet_search_envelope_interval(%s::regclass, "
                             "%s, %s, %s, %s::jsonb, %s, 0.075, true, true, 1000000)", (c, recording_id, a, b, flt, fetch_k))
            run = []
            for rid, s, e, d, meta, rms, lo, hi, overview in conn.execute(sql, args).fetchall():
                metas[rid] = meta
                hit = {"recording_id": rid, "start_sample": s, "end_sample": e, "distance": round(d, 4),
                       "start_utc": at(start_time(meta), src["rate"], s), "query_shift_samples": k * step}
                if evidence and rms is not None:
                    hit["evidence"] = {"rms": rms, "min": lo, "max": hi,
                                       "sketch_min_max": [[p[2], p[3]] for p in overview or []]}
                run.append(hit)
            runs.append(run)
        q0 = start_time(src["metadata"])
        before = q0 + timedelta(seconds=start_sample / src["rate"]) if before_only and q0 else None
        scope = search_scope(conn, c, src, flt, window, bounds, before)
    lo, hi = start_sample + min(used) if used else start_sample, end_sample + max(used) if used else end_sample
    hits = merge_hits(runs, recording_id, lo, hi)
    excluded = 0
    if exclude == "occurrence":                       # #384: the same part or cycle, from the context segments
        with connect() as conn:
            occ = occurrences_at(conn, c, [(recording_id, start_sample)] + [(h["recording_id"], h["start_sample"])
                                                                            for h in hits])
        own = occ.get((recording_id, start_sample))
        if not own:
            raise ValueError("the query interval lies in no context segment with an occurrence (part, cycle, run); "
                             "build context segments with occurrence keys first")
        keep = [h for h in hits if occ.get((h["recording_id"], h["start_sample"])) != own]
        excluded, hits = len(hits) - len(keep), keep
    elif exclude:
        key = exclude.split(":", 1)[1] if exclude.startswith("metadata:") else None
        own = src["metadata"].get(key) if key else None
        if key and own is None:
            raise ValueError(f"the query recording has no metadata key {key!r} to exclude by")
        keep = [h for h in hits if not (h["recording_id"] == recording_id if key is None
                                        else (metas.get(h["recording_id"]) or {}).get(key) == own)]
        excluded, hits = len(hits) - len(keep), keep
    hits = hits[:top_k]
    t0 = start_time(src["metadata"])
    return {"query": {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
                      "end_sample": end_sample, "start_utc": at(t0, src["rate"], start_sample),
                      "shifts_samples": used, "before_only": before_only,
                      "time_range": [time_from or None, time_to or None],
                      **({"exclude": exclude, "excluded_hits": excluded} if exclude else {}),
                      **({"query_note": note} if note else {})},
            "path": "morlet_search_similar" if similar else "exact (morlet_search_envelope_interval)",
            "scope": scope, "hits": hits,
            "recordings": {h["recording_id"]: metas[h["recording_id"]] for h in hits}}


# Framing for search_from_time, chosen on the agent demo's development data (4 seismic stations, 4 days; 203
# queries from 52 catalogued earthquakes): share of the top 10 overlapping a catalogued event was 0.318 for
# "2 s before the onset, shifts=2", against 0.278 for 15 s before, 0.280 for 6 s after, 0.217 for 2 s before
# without shifts and 0.194 for the 41 s with most energy. Results vary with placement in steps of about the
# stored window stride, so merging shifted searches beats any single placement.
FRAME_LEAD_S = 2.0
FRAME_SHIFTS = 2


def frame_window(onset, rate, window):
    """Query window start for an onset at sample `onset`."""
    return max(0, onset - int(round(FRAME_LEAD_S * rate)))


@mcp.tool(annotations=READ_ONLY)
@safe
def search_from_time(collection: str, utc_time: str, metadata_filter: str = "{}", search_filter: str = "{}",
                     top_k: int = 10, min_frequency_hz: float = 0.0, look_seconds: float = 30.0,
                     before_only: bool = False) -> dict:
    """Search for intervals similar to what happens at a UTC time, without choosing the window yourself.
    metadata_filter must select the one recording that contains the time (e.g. '{"station": "PB01"}').
    The tool looks for onsets within look_seconds of the time (STA/LTA after an optional high-pass at
    min_frequency_hz; for seismic data use about 2 Hz), takes the strongest, frames the stored window
    length to start 2 s before it and searches with the window also moved by 1/8 and 2/8 of its length
    either way (on seismic test data this found the most related events of the framings tried), over
    recordings matching search_filter. If there is no onset, the window starts a quarter window before the
    time. For short recordings such as snapshots, the query's own snapshot is left out and each other snapshot
    appears once, with its best window. Returns how the window was chosen and the hits, as search_similar
    does (with evidence). before_only=True searches only what happened before the time. Prefer this to
    search_similar when you know when something happened."""
    t = datetime.fromisoformat(utc_time.replace("Z", "+00:00")).astimezone(timezone.utc)
    flt = json.loads(metadata_filter or "{}")
    with connect() as conn:
        c = checked(conn, collection)
        rows = conn.execute(f"SELECT recording_id, sample_rate_hz, metadata, (checkpoint->>'end')::bigint FROM {c} "
                            f"WHERE metadata @> %s::jsonb", (json.dumps(flt),)).fetchall()
        hits = []
        for rid, rate, meta, n in rows:
            t0 = start_time(meta)
            if t0 is not None and 0 <= (k := math.floor((t - t0).total_seconds() * rate)) < n:
                hits.append((rid, rate, meta, n, k))
        if len(hits) != 1:
            names = ", ".join(h[0] for h in hits) or "none"
            raise ValueError(f"metadata_filter must select exactly one recording containing {utc_time}; found: {names}")
        rid, rate, meta, n, k = hits[0]
        windows = [w for w, _ in conn.execute(f"SELECT window_size, 1 FROM {c}_envelopes GROUP BY 1").fetchall()]
    if not windows:
        raise ValueError(f"{collection} has no search windows")
    window = max(windows)
    lta, sta = 30.0, 1.0
    a = max(0, k - int((look_seconds + lta + sta) * rate))
    b = min(n, k + int(look_seconds * rate))
    try:
        onsets = find_onsets(collection, rid, a, b, min_frequency_hz, 0.0, sta, lta, 4.0, 50)["onsets"]
        why = "no onset found"
    except ToolError:                            # recording too short for onset detection (e.g. snapshots)
        onsets, why = [], f"recording too short to detect onsets ({n / rate:g} s)"
    near = [o for o in onsets if abs(o["trigger_sample"] - k) <= look_seconds * rate]
    if near:
        best = max(near, key=lambda o: o["peak_ratio"])
        start, how = frame_window(best["trigger_sample"], rate, window), {
            "onset_sample": best["trigger_sample"], "onset_utc": best["trigger_utc"], "onset_ratio": best["peak_ratio"],
            "rule": f"window starts {FRAME_LEAD_S:g} s before the strongest onset within {look_seconds:g} s, "
                    f"searched with shifts={FRAME_SHIFTS}"}
    else:
        start, how = max(0, k - window // 4), {"rule": f"{why}: window starts a quarter window before "
                                                       f"the time, searched with shifts={FRAME_SHIFTS}"}
    start = max(0, min(start, n - window))
    short = n / rate < 120                       # a snapshot: its other windows are not "elsewhere"
    result = search_similar(collection, rid, start, start + window, search_filter, top_k * 4 + 20 if short else top_k,
                            FRAME_SHIFTS, before_only=before_only)
    if short:                                    # snapshots: the best window of each other snapshot
        best, seen = [], set()
        for h in result["hits"]:
            if h["recording_id"] != rid and h["recording_id"] not in seen:
                seen.add(h["recording_id"])
                best.append(h)
        result["hits"] = best[:top_k]
        result["recordings"] = {h["recording_id"]: result["recordings"][h["recording_id"]] for h in result["hits"]}
        how["excluded"] = "the query's own recording (shorter than 2 min); one hit per snapshot"
    result["framing"] = {"recording_id": rid, "utc_time": utc_time, **how}
    return result


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
def describe_interval(collection: str, recording_id: str, start_sample: int, end_sample: int,
                      bands: str = "", min_frequency_hz: float = 0.0) -> dict:
    """Characterise [start_sample, end_sample) of a recording: level (mean, std, rms, min, max, peak),
    impulsiveness (crest factor, kurtosis) and spectrum (dominant frequency, spectral centroid, energy share
    and power per band), in the recording's unit. bands: your own bands in Hz, e.g. "1-5,5-10,10-20" (default:
    five octave-like bands up to Nyquist). min_frequency_hz ignores lower frequencies for the dominant frequency
    and centroid; use it when slow background motion (ocean microseism, drift) would otherwise dominate.
    For a weak signal, compare_intervals against a quiet reference says more than this alone."""
    with connect() as conn:
        c = checked(conn, collection)
        rec = recording(conn, c, recording_id)
        x = read(conn, c, recording_id, start_sample, end_sample)
    return {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
            "end_sample": end_sample, "unit": rec["unit"], "sample_rate_hz": rec["rate"],
            "start_utc": at(start_time(rec["metadata"]), rec["rate"], start_sample),
            **stats(x, rec["rate"], parse_bands(bands, rec["rate"]), max(0.0, min_frequency_hz))}


def background(x_before, x_after, n, rate, edges):
    """Median band power and variance over non-overlapping windows of n samples cut from the stretches before
    and after an interval: a background level that a single event in the stretch does not move much."""
    rows = []
    for seg in (x_before, x_after):
        for i in range(0, seg.size - n + 1, n):
            st = stats(seg[i:i + n], rate, edges)
            if "band_power" in st:
                rows.append(st)
    if len(rows) < 3:
        raise ValueError("too little data around the interval for a background estimate; give a reference")
    keys = rows[0]["band_power"].keys()
    return {"std": float(np.sqrt(np.median([r["std"] ** 2 for r in rows]))),
            "band_power": {k: float(np.median([r["band_power"][k] for r in rows])) for k in keys},
            "windows": len(rows)}


@mcp.tool(annotations=READ_ONLY)
@safe
def compare_intervals(collection: str, recording_id: str, start_sample: int, end_sample: int,
                      reference_start_sample: int = -1, reference_end_sample: int = -1,
                      reference_recording_id: str = "", bands: str = "", background_minutes: float = 10.0) -> dict:
    """Compare an interval with a reference: by default the background around it (the median over windows
    of the same length in the background_minutes before and after, so a passing event barely moves it), or
    an explicit reference interval (a quiet stretch, normal operation, an earlier event), in the same
    recording unless reference_recording_id is given (same sample rate needed; lengths may differ).
    Returns the signal-to-reference ratio overall and per band (in dB; power is variance, so the mean level
    is ignored), the power each band has above the reference, and the share of that excess per band: the
    frequency content of what is new, not of the background. An explicit reference that itself contains an
    onset is flagged in warnings. bands as in describe_interval."""
    explicit = reference_start_sample >= 0 and reference_end_sample > reference_start_sample
    ref_id = reference_recording_id or recording_id
    warnings = []
    with connect() as conn:
        c = checked(conn, collection)
        rec = recording(conn, c, recording_id)
        rate = rec["rate"]
        edges = parse_bands(bands, rate)
        x = read(conn, c, recording_id, start_sample, end_sample)
        sx = stats(x, rate, edges)
        if explicit:
            ref = rec if ref_id == recording_id else recording(conn, c, ref_id)
            if ref["rate"] != rate:
                raise ValueError(f"sample rates differ ({rate:g} and {ref['rate']:g} Hz)")
            y = read(conn, c, ref_id, reference_start_sample, reference_end_sample)
            sy = stats(y, rate, edges)
            if y.size >= 15 * rate and float(sta_lta(y - y.mean(), rate, 0.5, 10.0).max()) >= 4.0:
                warnings.append("the reference contains an onset (short-term energy over 4x the preceding 10 s); "
                                "it may not be background")
            ref_desc = {"recording_id": ref_id, "start_sample": reference_start_sample,
                        "end_sample": reference_end_sample,
                        "start_utc": at(start_time(ref["metadata"]), rate, reference_start_sample)}
        else:
            n, span = end_sample - start_sample, int(max(0.0, background_minutes) * 60 * rate)
            lo, hi = max(0, start_sample - span), min(rec["samples"], end_sample + span)
            before = read(conn, c, recording_id, lo, start_sample) if start_sample - lo >= n else np.zeros(0)
            after = read(conn, c, recording_id, end_sample, hi) if hi - end_sample >= n else np.zeros(0)
            sy = background(before, after, n, rate, edges)
            ref_desc = {"recording_id": recording_id, "background": f"median of {sy['windows']} windows",
                        "from_sample": lo, "to_sample": hi, "from_utc": at(start_time(rec["metadata"]), rate, lo),
                        "to_utc": at(start_time(rec["metadata"]), rate, hi)}
    if "band_power" not in sx or "band_power" not in sy:
        raise ValueError("both intervals need at least 16 samples and some variation")

    def db(a, b):
        return None if a <= 0 or b <= 0 else round(10 * math.log10(a / b), 2)

    excess = {k: max(0.0, sx["band_power"][k] - sy["band_power"][k]) for k in sx["band_power"]}
    total = sum(excess.values())
    per_band = {k: {"power": sx["band_power"][k], "reference_power": sy["band_power"][k],
                    "ratio_db": db(sx["band_power"][k], sy["band_power"][k]), "excess_power": excess[k],
                    "excess_fraction": excess[k] / total if total > 0 else 0.0} for k in sx["band_power"]}
    keep = ("std", "rms", "peak_abs", "crest_factor", "kurtosis", "spectral_centroid_hz")
    out = {"collection": collection, "unit": rec["unit"], "sample_rate_hz": rate,
           "interval": {"recording_id": recording_id, "start_sample": start_sample, "end_sample": end_sample,
                        "start_utc": at(start_time(rec["metadata"]), rate, start_sample),
                        **{k: sx.get(k) for k in keep}},
           "reference": {**ref_desc, **{k: sy.get(k) for k in keep if k in sy}},
           "power_ratio_db": db(sx["std"] ** 2, sy["std"] ** 2), "bands": per_band}
    if warnings:
        out["warnings"] = warnings
    return out


def envelope(x, rate, lo, hi):
    """Amplitude envelope of x after a band-pass to [lo, hi] Hz (analytic signal by FFT), mean removed."""
    y = bandpass(x, rate, lo, hi)
    spec = np.fft.fft(y)
    h = np.zeros(y.size)
    h[0] = 1.0
    if y.size % 2 == 0:
        h[1:y.size // 2] = 2.0
        h[y.size // 2] = 1.0
    else:
        h[1:(y.size + 1) // 2] = 2.0
    env = np.abs(np.fft.ifft(spec * h))
    return env - env.mean()


@mcp.tool(annotations=READ_ONLY)
@safe
def envelope_spectrum(collection: str, recording_id: str, start_sample: int, end_sample: int, band: str = "",
                      max_frequency_hz: float = 600.0, check_frequencies: str = "", harmonics: int = 3,
                      tolerance_percent: float = 3.0, top_peaks: int = 8) -> dict:
    """Envelope spectrum of an interval: band-pass around a resonance (band "lo-hi" in Hz; default the
    strongest of the default bands above a tenth of Nyquist), take the amplitude envelope and its spectrum up
    to max_frequency_hz. Repeating impacts, such as rolling elements passing a bearing defect, show up as a
    line at their repetition frequency and its harmonics. Levels are dB above the median of the envelope
    spectrum. noise_peak_db is the level random peaks reach here (90th percentile of all local maxima): a line
    well above it (say 10 dB) with harmonics is clear. Returns the strongest peaks, and for each frequency in
    check_frequencies (comma-separated, e.g. defect frequencies you computed) the strongest peak within
    tolerance_percent of it and of its harmonics; real defect lines often sit a few percent off the computed
    value (slip, contact angle). A line shows periodic impacts at that rate, not by itself a diagnosis."""
    with connect() as conn:
        c = checked(conn, collection)
        rec = recording(conn, c, recording_id)
        x = read(conn, c, recording_id, start_sample, end_sample)
    rate = rec["rate"]
    if x.size < 256:
        raise ValueError("interval too short for an envelope spectrum (need at least 256 samples)")
    if band.strip():
        (lo, hi), = parse_bands(band, rate)
    else:
        st = stats(x, rate)
        cands = [(k, v) for k, v in st.get("band_power", {}).items() if float(k.split("-")[0]) >= rate / 20]
        if not cands:
            raise ValueError("no band above a tenth of Nyquist; give band")
        k = max(cands, key=lambda kv: kv[1])[0]
        lo, hi = (float(v) for v in k.replace(" Hz", "").split("-"))
        hi = min(hi, rate / 2 * 0.98)
    env = envelope(x, rate, lo, hi)
    spec = np.abs(np.fft.rfft(env * np.hanning(env.size))) / env.size
    freqs = np.fft.rfftfreq(env.size, 1.0 / rate)
    res = float(freqs[1])
    keep = (freqs >= max(2 * res, 1.0)) & (freqs <= max_frequency_hz)
    f, a = freqs[keep], spec[keep]
    if f.size < 10:
        raise ValueError("too few frequencies below max_frequency_hz; use a longer interval")
    med = float(np.median(a)) or 1e-30
    db = 20 * np.log10(np.maximum(a, 1e-30) / med)
    loc = np.flatnonzero((a[1:-1] > a[:-2]) & (a[1:-1] >= a[2:])) + 1
    noise = float(np.percentile(db[loc], 90)) if loc.size else 0.0
    order = loc[np.argsort(-a[loc])][:max(1, min(top_peaks, 30))]
    peaks = [{"frequency_hz": round(float(f[i]), 2), "db": round(float(db[i]), 1)} for i in order]
    tol = max(0.0, tolerance_percent) / 100
    checks = []
    for item in [t for t in check_frequencies.split(",") if t.strip()]:
        f0 = float(item)
        if f0 <= 0:
            raise ValueError("check_frequencies must be positive")
        hs = []
        for h in range(1, max(1, min(harmonics, 6)) + 1):
            target = h * f0
            if target > max_frequency_hz:
                break
            near = np.abs(f - target) <= max(2 * res, tol * target)
            i = int(np.argmax(np.where(near, a, -1.0)))
            hs.append({"harmonic": h, "expected_hz": round(target, 2), "found_hz": round(float(f[i]), 2),
                       "db": round(float(db[i]), 1), "db_above_noise_peaks": round(float(db[i]) - noise, 1)})
        checks.append({"frequency_hz": f0, "harmonics": hs})
    return {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
            "end_sample": end_sample, "start_utc": at(start_time(rec["metadata"]), rate, start_sample),
            "demodulation_band_hz": [lo, hi], "resolution_hz": round(res, 3), "noise_peak_db": round(noise, 1),
            "peaks": peaks, "checks": checks}


@mcp.tool(annotations=READ_ONLY)
@safe
def find_onsets(collection: str, recording_id: str, start_sample: int, end_sample: int,
                min_frequency_hz: float = 0.0, max_frequency_hz: float = 0.0, sta_seconds: float = 1.0,
                lta_seconds: float = 30.0, threshold: float = 4.0, max_onsets: int = 20) -> dict:
    """Find candidate onsets in [start_sample, end_sample): moments where the short-term energy rises above
    threshold times the energy of the preceding lta_seconds (an STA/LTA trigger, after an optional band-pass
    between min_frequency_hz and max_frequency_hz; 0 leaves that side open). The first lta_seconds +
    sta_seconds of the interval only fill the windows, so start a little before the time of interest. Each
    onset has its trigger sample (up to sta_seconds after the true onset), its end (ratio back below 1.5), the
    peak ratio and UTC times. These are candidates to inspect: detection quality has not been evaluated."""
    if not (0 < sta_seconds < lta_seconds and threshold > 1):
        raise ValueError("need 0 < sta_seconds < lta_seconds and threshold > 1")
    with connect() as conn:
        c = checked(conn, collection)
        rec = recording(conn, c, recording_id)
        x = read(conn, c, recording_id, start_sample, end_sample)
    rate = rec["rate"]
    if max_frequency_hz and not 0 <= min_frequency_hz < max_frequency_hz <= rate / 2:
        raise ValueError(f"need 0 <= min_frequency_hz < max_frequency_hz <= {rate / 2:g} Hz")
    if x.size < (sta_seconds + lta_seconds) * rate * 1.5:
        raise ValueError(f"interval too short: give at least {1.5 * (sta_seconds + lta_seconds):g} s")
    r = sta_lta(bandpass(x, rate, min_frequency_hz, max_frequency_hz), rate, sta_seconds, lta_seconds)
    t0 = start_time(rec["metadata"])
    onsets, i, n = [], 0, r.size
    while i < n and len(onsets) < max(1, min(max_onsets, 200)):
        up = np.flatnonzero(r[i:] >= threshold)
        if up.size == 0:
            break
        on = i + int(up[0])
        down = np.flatnonzero(r[on:] < 1.5)
        off = on + int(down[0]) if down.size else n
        s_on, s_off = start_sample + on, start_sample + off
        onsets.append({"trigger_sample": s_on, "trigger_utc": at(t0, rate, s_on), "end_sample": s_off,
                       "end_utc": at(t0, rate, s_off), "duration_s": (off - on) / rate,
                       "peak_ratio": round(float(r[on:off].max()), 2)})
        i = off + 1
    return {"collection": collection, "recording_id": recording_id, "start_sample": start_sample,
            "end_sample": end_sample, "start_utc": at(t0, rate, start_sample),
            "filter_hz": [min_frequency_hz or None, max_frequency_hz or None], "sta_seconds": sta_seconds,
            "lta_seconds": lta_seconds, "threshold": threshold, "onsets": onsets,
            "truncated": len(onsets) >= max(1, min(max_onsets, 200))}


@mcp.tool(annotations=READ_ONLY)
@safe
def decision_state(collection: str, recording_id: str, sample: int, match_keys: str = "", exclude_labels: str = "",
                   min_history: int = 3, neighbors: int = 3, include_occurrence: bool = False,
                   reliable_history: int = 10, baseline_recordings: str = "", examples: int = 0,
                   example_labels: str = "", example_match_keys: str = "") -> dict:
    """What a fast decision model needs to know about the context segment (a cut, a cycle, a part) that contains
    a sample: its context (program, tool, material, ...); per value (rms, std, p75, crest, kurtosis, ...) its
    deviation from the same-context history; a summary score with its quantile in that history; and the nearest
    earlier cases with their labels. Needs a collection with context (morlet_enable_context) and built segments.

    match_keys: comma-separated context keys the history must share (empty: all of the segment's keys).
    exclude_labels: comma-separated labels whose segments are left out of the history (e.g. "crash,air_cut").
    Relative deviation is (value - history median) / |history median|; quantile is the value's position within
    the history. Occurrence identifiers (run or part numbers) are left out unless include_occurrence is true.
    With fewer than reliable_history earlier segments, history.small is true and history.note says why that matters:
    one unusual segment, or a short transient in this one (high kurtosis or crest), can dominate the deviations.
    baseline_recordings: comma-separated recording ids (optionally recording_id@sample) of this asset's own
    reference segments, e.g. a tool's first cuts after a change; own_baseline then shows each value's drift from
    them, which is how wear often shows. examples: how many nearest labelled cases of the same context to
    include, with their labels, notes and values (example_labels: comma-separated labels that count; default any
    label except logged decisions). The whole history is searched; if no labelled case exists, examples is empty
    and examples_note says so. example_match_keys: comma-separated context keys examples must share (default
    match_keys); fewer keys widen the search, e.g. "process" to find labelled cuts of other tools.
    This describes how unusual the segment is for its context; it is not a diagnosis. Cite the segment and the
    neighbours by recording and sample range."""
    keys = [k.strip() for k in match_keys.split(",") if k.strip()]
    labels = [k.strip() for k in exclude_labels.split(",") if k.strip()]
    with connect() as conn:
        c = checked(conn, collection)
        seg = decision.segment_at(conn, c, recording_id, sample)
        base = []
        for item in (x.strip() for x in baseline_recordings.split(",") if x.strip()):
            brid, _, bs = item.partition("@")
            base.append(decision.segment_at(conn, c, brid, int(bs) if bs else 0))
        wanted = [x.strip() for x in example_labels.split(",") if x.strip()] or None
        ex_keys = [k.strip() for k in example_match_keys.split(",") if k.strip()] or None
        return decision.decision_state(conn, c, seg, match_keys=keys, exclude_labels=labels,
                                       example_match_keys=ex_keys,
                                       min_history=min_history, neighbors=max(0, min(neighbors, 20)),
                                       include_occurrence=include_occurrence,
                                       reliable_history=max(1, reliable_history), baseline=base,
                                       examples=max(0, min(examples, 20)), example_labels=wanted)


@mcp.tool(annotations=READ_ONLY)
@safe
def decisions(collection: str, recording_prefix: str = "", answer: str = "", limit: int = 20,
              with_state: bool = False) -> dict:
    """Decisions a fast decision model ("System 1") logged on recordings (labels "s1:<answer>"), newest first:
    the answer, model, question, probability, the threshold that turned it into the answer, when and by whom,
    and the recordings and sample ranges it is about, and the nearest labelled precedents the model's state held
    at that moment (precedents: recording, sample range, distance, labels; precedents_from says whether they were
    labelled examples or neighbours). with_state also returns the exact state the model saw (its sha256 is always
    returned). To explain a decision, look at those recordings: decision_state shows how the
    segment deviates from its history now (history may have grown since), describe_interval and read_interval
    show the samples. The model gives a probability, not a reason; the explanation has to come from the data."""
    with connect() as conn:
        c = checked(conn, collection)
        found = decision.decisions(conn, c, recording_prefix=recording_prefix, answer=answer,
                                   limit=max(1, min(limit, 200)), with_state=with_state)
    return {"collection": collection, "decisions": found, "truncated": len(found) >= max(1, min(limit, 200))}


if __name__ == "__main__":
    mcp.run("stdio")
