"""Synchronous client for pg_morlet's recording API (SQL 0.3.2 and later)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import numpy as np
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from . import _pgnumpy
from ._errors import InvalidInputError, NotFoundError, UnsupportedError, translate
from ._types import Interval, IntervalRef, IntervalStats, Match, OverviewBucket, Recording, SearchResult

CHUNK = 4096                 # samples per stored chunk (the importer's default)
READ_PIECE = 32768           # morlet_read_interval's per-call limit (servers without the raw read)
RAW_READ_MAX = 16_777_216    # morlet_read_interval_raw's per-call limit
SEARCH_WINDOW = 4096         # default envelope window; a search interval must have this length
MAX_AMPLITUDE = 1e9          # pg_morlet stores samples with |x| <= 1e9
_EXACT_DTYPES = ("f", "i", "u")


def _start_ns(start_time: datetime | int | None) -> int | None:
    if start_time is None:
        return None
    if isinstance(start_time, bool):
        raise InvalidInputError("start_time must be a timezone-aware datetime or integer nanoseconds")
    if isinstance(start_time, (int, np.integer)):
        return int(start_time)
    if isinstance(start_time, datetime):
        if start_time.tzinfo is None or start_time.utcoffset() is None:
            raise InvalidInputError("start_time must be timezone-aware (naive datetimes are ambiguous)")
        delta = start_time.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    raise InvalidInputError("start_time must be a timezone-aware datetime or integer nanoseconds")


def _iso(ns: int) -> str:
    seconds, frac = divmod(ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{frac:09d}Z" if frac else f"{base}Z"


def _samples(values: Any) -> np.ndarray:
    """A 1-D float64 copy of the input, refusing anything that would not be stored exactly."""
    a = np.asarray(values)
    if a.ndim != 1:
        raise InvalidInputError(f"samples must be one-dimensional, got shape {a.shape}")
    if a.size == 0:
        raise InvalidInputError("samples must not be empty")
    if a.dtype.kind not in _EXACT_DTYPES:
        raise InvalidInputError(f"unsupported sample type {a.dtype}; use float or integer arrays")
    if a.dtype.kind == "f" and a.dtype.itemsize > 8:
        raise InvalidInputError(f"{a.dtype} cannot be stored exactly as float64")
    x = np.ascontiguousarray(a, dtype="<f8")       # widening: exact for float16/32 and integers within ±1e9
    if not np.isfinite(x).all():
        raise InvalidInputError("samples must be finite (NaN and infinities cannot be stored)")
    if np.abs(x).max() > MAX_AMPLITUDE:
        raise InvalidInputError("samples must be within ±1e9 (pg_morlet's range); store a scaled unit instead")
    return x


class Client:
    """One connection to a MorletDB (PostgreSQL with pg_morlet) database.

    Transactions: methods run in the connection's current transaction and never commit. With
    `Client.connect(...)` used as a context manager, the client owns the connection and commits on a
    clean exit (rolls back on an exception) and closes it. With `Client(connection)` the caller keeps
    ownership: commit, roll back and close it yourself.
    """

    def __init__(self, connection: psycopg.Connection, *, owns_connection: bool = False):
        self.connection = connection
        self._owns = owns_connection
        _pgnumpy.register(connection)
        self._functions: dict[str, bool] = {}

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs: Any) -> "Client":
        """Open a connection (libpq conninfo or URL; empty uses the PG* environment variables)."""
        return cls(psycopg.connect(conninfo, **kwargs), owns_connection=True)

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._owns:
            return
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()

    # --- helpers -------------------------------------------------------------------------------------
    def _has(self, signature: str) -> bool:
        if signature not in self._functions:
            self._functions[signature] = self._one("SELECT to_regprocedure(%s) IS NOT NULL", (signature,))
        return self._functions[signature]

    def _one(self, sql: str, args: tuple = (), **kw: Any) -> Any:
        row = self._execute(sql, args, **kw).fetchone()
        return None if row is None else row[0]

    def _execute(self, sql: str, args: tuple = (), **kw: Any) -> psycopg.Cursor:
        try:
            return self.connection.execute(sql, args, **kw)
        except psycopg.errors.InsufficientPrivilege:
            raise
        except psycopg.Error as exc:
            raise translate(exc) from exc

    # --- collections ---------------------------------------------------------------------------------
    def create_collection(self, name: str, *, searchable: bool = True, summaries: bool = True) -> None:
        """Create a stream collection `schema.name` (or `name` in public) if it does not exist, enable
        search envelopes on it when `searchable`, and per-chunk summaries when `summaries` and the server
        supports them (SQL 0.3.3): statistics and overviews of long intervals then read summaries instead
        of decoding, with identical results, for about 5% slower ingestion and 1% more chunk storage.
        Idempotent."""
        schema, table = name.split(".", 1) if "." in name else ("public", name)
        if self._one("SELECT to_regclass(%s)", (f"{schema}.{table}",)) is None:
            self._execute("SELECT morlet_create_stream_collection(%s, %s)", (schema, table))
        if searchable and self._one("SELECT to_regclass(%s)", (f"{schema}.{table}_envelopes",)) is None:
            self._execute("SELECT morlet_enable_envelopes(%s::regclass)", (f"{schema}.{table}",))
        if summaries and self._has("public.morlet_enable_chunk_summaries(regclass)"):
            self._execute("SELECT morlet_enable_chunk_summaries(%s::regclass)", (f"{schema}.{table}",))

    # --- ingestion -----------------------------------------------------------------------------------
    def ingest(self, collection: str, recording_id: str, samples: Any, *, sample_rate_hz: float,
               channel: str, unit: str, start_time: datetime | int | None = None,
               metadata: Mapping[str, Any] | None = None, sample_quantum: float | None = None,
               search_window: int | None = SEARCH_WINDOW, search_stride: int | None = None) -> Recording:
        """Store a complete waveform as a new recording (samples 0..n-1) and, if the collection is
        searchable and `search_window` is set, build its search envelopes: one per window of
        `search_window` samples, starting every `search_stride` samples (default: half a window).

        Samples are stored losslessly as float64: float and integer arrays are widened exactly; NaN,
        infinities, 2-D and complex input are refused. `start_time` (timezone-aware datetime or integer
        UTC nanoseconds) is the time of sample 0; it is stored in the metadata as `start` (ISO 8601, UTC)
        and `start_ns` (exact). `sample_quantum` is an optional value grid (e.g. an ADC step); servers
        that detect grids do not need it. Calling again with identical samples is a no-op; different
        samples at the same offsets raise ConflictError.
        """
        x = _samples(samples)
        if not (np.isfinite(sample_rate_hz) and sample_rate_hz > 0):
            raise InvalidInputError("sample_rate_hz must be a positive finite number")
        if not recording_id or not channel or unit is None:
            raise InvalidInputError("recording_id and channel must be non-empty; unit must be a string")
        meta = dict(metadata or {})
        ns = _start_ns(start_time)
        if ns is not None:
            for key in ("start", "start_ns"):
                if key in meta:
                    raise InvalidInputError(f"metadata[{key!r}] is set from start_time; do not pass both")
            meta.update({"start": _iso(ns), "start_ns": ns})
        quantum = 1.0 if sample_quantum is None else float(sample_quantum)
        if not (np.isfinite(quantum) and quantum > 0):
            raise InvalidInputError("sample_quantum must be a positive finite number")
        self._execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s,512,256,'zscore','none',%s)",
                      (collection, recording_id, float(sample_rate_hz), channel, unit, Jsonb(meta), quantum))
        self.append(collection, recording_id, 0, x)
        if search_window and self._one("SELECT to_regclass(%s)", (_envelopes(collection),)) is not None:
            self.build_search_index(collection, recording_id, window=search_window, stride=search_stride)
        return self.recording(collection, recording_id)

    def append(self, collection: str, recording_id: str, start_sample: int, samples: Any) -> int:
        """Append samples at `start_sample` (the next sample of the recording, or an identical replay of
        stored samples). Returns the number of samples stored. Gaps and overlaps with different values
        raise. Search envelopes are not rebuilt; call build_search_index."""
        x = _samples(samples)
        if start_sample < 0:
            raise InvalidInputError("start_sample must be nonnegative")
        raw = self._has("public.morlet_append_chunk_raw(regclass,text,bigint,bytea)")
        end = None
        try:
            with self.connection.pipeline():
                for off in range(0, x.size, CHUNK):
                    piece = x[off:off + CHUNK]
                    if raw:
                        cur = self.connection.execute(
                            "SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%b)",
                            (collection, recording_id, start_sample + off, piece.tobytes()))
                    else:
                        cur = self.connection.execute(
                            "SELECT morlet_append_chunk(%s::regclass,%s,%s,%s::float8[])",
                            (collection, recording_id, start_sample + off, piece))
            end = cur.fetchone()[0]
        except psycopg.Error as exc:
            raise translate(exc) from exc
        return int(end)

    def build_search_index(self, collection: str, recording_id: str, *, window: int = SEARCH_WINDOW,
                           stride: int | None = None) -> int:
        """Build the search envelopes of a recording (resumable; returns the number of windows built).

        Stored windows start every `stride` samples (default window // 2). Search tolerates time shifts of
        about `max_lag_fraction` × window (±307 samples at 4,096 and the default 0.075), so an event is found
        reliably when it starts within that distance of a window start: a stride of 512 at window 4,096
        covers every offset, at 4× the envelope storage of the default. The stride of a recording's windows
        is fixed by its first build."""
        stride = window // 2 if stride is None else stride
        total = 0
        while True:
            n = self._one("SELECT morlet_build_envelopes(%s::regclass,%s,%s,%s)",
                          (collection, recording_id, window, stride))
            total += n
            if not n:
                return total

    # --- recordings ----------------------------------------------------------------------------------
    def recording(self, collection: str, recording_id: str) -> Recording:
        rows = self.recordings(collection, recording_id=recording_id)
        if not rows:
            raise NotFoundError(f"recording {recording_id!r} does not exist in {collection} or is not visible")
        return rows[0]

    def recordings(self, collection: str, metadata_filter: Mapping[str, Any] | None = None, *,
                   recording_id: str | None = None) -> list[Recording]:
        """Recordings whose metadata contains `metadata_filter` (JSON containment), by recording_id."""
        if self._one("SELECT to_regclass(%s)", (collection,)) is None:
            raise NotFoundError(f"collection {collection!r} does not exist or is not visible")
        query = sql.SQL(
            "SELECT recording_id, sample_rate_hz, channel, unit, coalesce((checkpoint->>'end')::bigint, 0), "
            "metadata FROM {} WHERE metadata @> %s AND (%s::text IS NULL OR recording_id = %s) "
            "ORDER BY recording_id").format(_identifier(collection))
        rows = self._execute(query, (Jsonb(dict(metadata_filter or {})), recording_id, recording_id)).fetchall()
        return [Recording(collection, r[0], r[1], r[2], r[3], int(r[4]), _meta_start_ns(r[5]), r[5]) for r in rows]

    # --- reads ---------------------------------------------------------------------------------------
    def read(self, collection: str, recording_id: str, start_sample: int, end_sample: int) -> Interval:
        """The original samples of [start_sample, end_sample), bit for bit, as a float64 array."""
        return self.read_ref(IntervalRef(collection, recording_id, int(start_sample), int(end_sample)))

    def read_ref(self, ref: IntervalRef) -> Interval:
        if not 0 <= ref.start_sample < ref.end_sample:
            raise InvalidInputError("an interval needs 0 <= start_sample < end_sample (end is exclusive)")
        rec = self.recording(ref.collection, ref.recording_id)
        if ref.end_sample > rec.samples:
            raise NotFoundError(f"interval ends at {ref.end_sample}, but {ref.recording_id} has {rec.samples} samples")
        parts = []
        if self._has("public.morlet_read_interval_raw(regclass,text,bigint,bigint)"):
            for a in range(ref.start_sample, ref.end_sample, RAW_READ_MAX):
                b = min(a + RAW_READ_MAX, ref.end_sample)
                raw = self._one("SELECT morlet_read_interval_raw(%s::regclass,%s,%s,%s)",
                                (ref.collection, ref.recording_id, a, b), binary=True)
                parts.append(np.frombuffer(raw, dtype="<f8"))
        else:
            for a in range(ref.start_sample, ref.end_sample, READ_PIECE):
                b = min(a + READ_PIECE, ref.end_sample)
                parts.append(np.asarray(self._one(
                    "SELECT samples FROM morlet_read_interval(%s::regclass,%s,%s,%s)",
                    (ref.collection, ref.recording_id, a, b), binary=True), dtype=np.float64))
        samples = parts[0] if len(parts) == 1 else np.concatenate(parts)
        samples = samples.astype(np.float64, copy=True)   # writable and native byte order
        return Interval(ref, samples, rec)

    def stats(self, collection: str, recording_id: str, start_sample: int, end_sample: int) -> IntervalStats:
        """Count, min, max, mean and RMS of [start_sample, end_sample), computed by the server (SQL 0.3.3)."""
        if not self._has("public.morlet_interval_stats(regclass,text,bigint,bigint)"):
            raise UnsupportedError("morlet_interval_stats needs pg_morlet SQL 0.3.3 or later")
        row = self._execute("SELECT sample_count, min, max, mean, rms FROM morlet_interval_stats(%s::regclass,%s,%s,%s)",
                            (collection, recording_id, int(start_sample), int(end_sample))).fetchone()
        return IntervalStats(int(row[0]), *map(float, row[1:]))

    def overview(self, collection: str, recording_id: str, start_sample: int, end_sample: int,
                 buckets: int = 100) -> list[OverviewBucket]:
        """Statistics of [start_sample, end_sample) in up to `buckets` consecutive parts (whole chunks each,
        except at the edges), for example to draw an hour of data. Fast on collections with summaries;
        intervals beyond 16,777,216 samples need them (SQL 0.3.3)."""
        if not self._has("public.morlet_interval_overview(regclass,text,bigint,bigint,integer)"):
            raise UnsupportedError("morlet_interval_overview needs pg_morlet SQL 0.3.3 with chunk summaries support")
        rows = self._execute("SELECT bucket_start, bucket_end, sample_count, min, max, mean, rms FROM "
                             "morlet_interval_overview(%s::regclass,%s,%s,%s,%s)",
                             (collection, recording_id, int(start_sample), int(end_sample), int(buckets))).fetchall()
        return [OverviewBucket(int(a), int(b), int(n), *map(float, r)) for a, b, n, *r in rows]

    def samples_for_time(self, recording: Recording, t_start_ns: int, t_end_ns: int) -> tuple[int, int]:
        """The half-open sample range covering UTC times [t_start_ns, t_end_ns) (integer nanoseconds), by
        the server's rule: the first sample at or after each time (ceil). Needs the recording's start time."""
        if recording.start_ns is None:
            raise InvalidInputError(f"{recording.recording_id} has no start time")
        row = self._execute("SELECT * FROM morlet_time_to_samples(%s,%s,%s,%s)",
                            (recording.sample_rate_hz, recording.start_ns, int(t_start_ns), int(t_end_ns))).fetchone()
        return int(row[0]), int(row[1])

    # --- search --------------------------------------------------------------------------------------
    def search(self, collection: str, recording_id: str, start_sample: int, end_sample: int | None = None, *,
               metadata_filter: Mapping[str, Any] | None = None, top_k: int = 10,
               max_lag_fraction: float = 0.075, exclude_source: bool = True, suppress_overlaps: bool = True,
               max_candidates: int = 1_000_000) -> SearchResult:
        """Stored intervals most similar to [start_sample, end_sample) of a recording (`morlet_search_similar`).

        The interval must have the length of a built search window (default 4096 samples). Candidates are
        stored windows whose recording has the same sample rate, channel and unit and whose metadata
        contains `metadata_filter`; time shifts up to `max_lag_fraction` of the window are tolerated.
        Results are references into stored recordings: read them with `read_ref(match.ref)`.
        """
        end = start_sample + SEARCH_WINDOW if end_sample is None else end_sample
        query = IntervalRef(collection, recording_id, int(start_sample), int(end))
        if not 0 <= query.start_sample < query.end_sample:
            raise InvalidInputError("an interval needs 0 <= start_sample < end_sample (end is exclusive)")
        probes = bool(query.length <= 32768
                      and self._has("public.morlet_envelope_probe_relation(regclass,integer)")
                      and self._one("SELECT public.morlet_envelope_probe_relation(%s::regclass,%s) IS NOT NULL",
                                    (collection, query.length)))
        rows = self._execute(
            "SELECT recording_id, start_sample, end_sample, distance, metadata FROM morlet_search_similar("
            "%s::regclass,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (collection, recording_id, query.start_sample, query.end_sample, Jsonb(dict(metadata_filter or {})),
             int(top_k), float(max_lag_fraction), bool(exclude_source), bool(suppress_overlaps),
             int(max_candidates))).fetchall()
        matches = [Match(i + 1, IntervalRef(collection, r[0], int(r[1]), int(r[2])), float(r[3]), r[4])
                   for i, r in enumerate(rows)]
        return SearchResult(query, matches, "probes" if probes else "exact", float(max_lag_fraction))


def _identifier(collection: str) -> sql.Identifier:
    schema, table = collection.split(".", 1) if "." in collection else ("public", collection)
    return sql.Identifier(schema, table)


def _envelopes(collection: str) -> str:
    schema, table = collection.split(".", 1) if "." in collection else ("public", collection)
    return f"{schema}.{table}_envelopes"


def _meta_start_ns(meta: Mapping[str, Any] | None) -> int | None:
    if not isinstance(meta, Mapping):
        return None
    if isinstance(meta.get("start_ns"), int):
        return meta["start_ns"]
    for key in ("start", "starttime", "start_time"):
        if meta.get(key):
            try:
                t = datetime.fromisoformat(str(meta[key]).replace("Z", "+00:00"))
            except ValueError:
                return None
            if t.tzinfo is None:
                return None
            return _start_ns(t)
    return None

