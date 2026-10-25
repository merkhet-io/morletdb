"""Shared ingestion for replay and live chunks into MorletDB recordings.

Each contiguous run of one stream becomes one MorletDB recording (a segment).
A chunk continues a segment only when its first sample falls on that segment's
sample grid (within half a sample) at the committed end. Gaps, misaligned
timing and conflicting overlaps close the segment: the next data starts a new
recording. Nothing is concatenated across missing time or zero-filled.

Overlapping chunks are compared with stored samples. Identical overlap is a
verified duplicate and only the new remainder is appended; differing overlap is
a conflict and both versions are preserved in separate recordings. Raw samples,
MorletDB extraction, checkpoints and the stream progress row commit together.

Recording identity:
- A recording opened for new time (first data, or after a gap) is
  ``<dataset>/<stream>/<first-sample UTC>``. Placement compares every overlap with
  stored samples first, so no stored recording can already start there.
- A recording opened because its samples conflict with stored samples is a
  version: ``<plain id>#<16 hex of sha256(opening chunk as float64 LE)>``. Several
  versions may share a first-sample time; their content keeps them distinct.
- Retransmissions never open recordings: identical data is found by content in an
  existing version (including after restart) and verified as a duplicate. The
  same conflicting data therefore cannot create endless copies. Recognition
  compares stored samples, never recomputed hashes, so it does not depend on
  chunk boundaries; the suffix is only a stable label for one database.
- A recording identity commits in the same transaction as its opening samples,
  so no empty version can survive a crash and later be filled by other data.
  A suffix collision (64-bit prefix) raises instead of reusing a version.
- After an exception, discard the Ingestor and rebuild it from the database.
- When several versions could continue contiguously, the candidate order is
  first-sample time, then recording ID, both descending; the choice is logged
  as ``ambiguous_continuation`` because timestamps alone cannot decide it.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "client"))
from morlet_client import RecordingClient  # noqa: E402

COLLECTION = "public.seismic_waveforms"
INGEST_VERSION = "seismic-ingest-v1"
# Server-side representation domains (see docs/morlet-extractor.md).
RATE_DOMAINS = {"morlet-energy-v1": lambda r: 128 <= r <= 200000,
                "morlet-energy-lowrate-v1": lambda r: 1 <= r < 128}

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS seismic;
CREATE TABLE IF NOT EXISTS seismic.segments (
  recording_id text PRIMARY KEY, dataset text NOT NULL, stream text NOT NULL,
  split text NOT NULL, start_ns bigint NOT NULL, sample_rate_hz float8 NOT NULL,
  opened_reason text NOT NULL, created_wall timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS segments_stream ON seismic.segments(dataset, stream, start_ns);
CREATE TABLE IF NOT EXISTS seismic.progress (
  dataset text NOT NULL, stream text NOT NULL, mode text NOT NULL,
  last_recording_id text, last_committed_end_ns bigint, committed_samples bigint NOT NULL DEFAULT 0,
  committed_chunks bigint NOT NULL DEFAULT 0, duplicate_samples bigint NOT NULL DEFAULT 0,
  conflicts bigint NOT NULL DEFAULT 0, gaps bigint NOT NULL DEFAULT 0, errors bigint NOT NULL DEFAULT 0,
  last_chunk_emitted_wall float8, updated_wall timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(dataset, stream));
CREATE TABLE IF NOT EXISTS seismic.events (
  id bigserial PRIMARY KEY, dataset text NOT NULL, stream text NOT NULL, kind text NOT NULL,
  sample_ns bigint, recording_id text, detail jsonb NOT NULL DEFAULT '{}',
  wall timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS events_stream ON seismic.events(dataset, stream, sample_ns);
CREATE TABLE IF NOT EXISTS seismic.triggers (
  recording_id text NOT NULL REFERENCES seismic.segments(recording_id) ON DELETE CASCADE,
  on_sample bigint NOT NULL, off_sample bigint NOT NULL, dataset text NOT NULL, stream text NOT NULL,
  on_ns bigint NOT NULL, off_ns bigint NOT NULL, peak_ratio float8 NOT NULL,
  off_truncated boolean NOT NULL, detector_version text NOT NULL,
  PRIMARY KEY(recording_id, on_sample, detector_version));
CREATE TABLE IF NOT EXISTS seismic.detector_progress (
  recording_id text NOT NULL REFERENCES seismic.segments(recording_id) ON DELETE CASCADE,
  detector_version text NOT NULL, next_block bigint NOT NULL, closed boolean NOT NULL DEFAULT false,
  finalized_end bigint, finalized_from_block bigint,
  PRIMARY KEY(recording_id, detector_version));
-- App bookkeeping from earlier versions of this example (not the pg_morlet extension).
ALTER TABLE seismic.detector_progress ADD COLUMN IF NOT EXISTS finalized_end bigint;
ALTER TABLE seismic.detector_progress ADD COLUMN IF NOT EXISTS finalized_from_block bigint;
"""


def init_db(conn):
    # Parallel per-stream processes may initialize a fresh database at once;
    # IF NOT EXISTS checks are not race-safe, so serialize setup per database.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('morlet_seismic.init_db'))")
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    exists = conn.execute("SELECT to_regclass(%s)", (COLLECTION,)).fetchone()[0]
    if exists is None:
        schema, name = COLLECTION.split(".")
        conn.execute("SELECT morlet_create_stream_collection(%s,%s)", (schema, name))
    conn.execute(SCHEMA)
    conn.commit()


def iso_ns(value):
    from datetime import datetime, timezone
    seconds, nanos = divmod(int(value), 10**9)
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{nanos:09d}Z"


@dataclass
class Segment:
    recording_id: str
    start_ns: int
    rate: float
    committed: int   # samples durably committed in MorletDB

    def time_of(self, sample):
        return self.start_ns + round(sample * 1e9 / self.rate)

    @property
    def end_ns(self):
        return self.time_of(self.committed)


class Ingestor:
    """Stateless across restarts: segment progress is reloaded from PostgreSQL."""

    def __init__(self, conn, dataset, representation, stream_metadata, *, mode="replay",
                 split_of=None, tolerance=0.5, batch_chunks=16, on_commit=None, on_open=None):
        self.conn = conn
        self.client = RecordingClient(conn, COLLECTION)
        self.dataset = dataset
        self.rep = representation
        self.stream_metadata = stream_metadata   # stream -> static acquisition metadata
        self.mode = mode
        # (stream, start_ns) -> {"split": ..., optional deterministic labels}
        self.split_of = split_of or (lambda stream, start_ns: {"split": mode})
        self.tolerance = tolerance
        self.batch_chunks = batch_chunks
        self.on_commit = on_commit               # callback(set of recording ids)
        self.on_open = on_open                   # callback(stream, new recording id)
        self.segments = {}                       # stream -> [Segment] sorted by start
        self.pending = []                        # (segment, start_sample, samples, chunk)
        self.stats = {"chunks": 0, "appended_samples": 0, "duplicate_samples": 0,
                      "conflicts": 0, "gaps": 0, "errors": 0, "segments_opened": 0,
                      "batches": 0}
        self.latency = {"append_batch_s": [], "chunk_commit_s": []}
        self._events = []
        self._progress = {}
        self._opened = []                        # (stream, recording id) awaiting commit

    # -- state -------------------------------------------------------------
    def load(self, stream):
        if stream in self.segments:
            return self.segments[stream]
        rows = self.conn.execute(
            "SELECT recording_id,start_ns,sample_rate_hz FROM seismic.segments "
            "WHERE dataset=%s AND stream=%s ORDER BY start_ns,recording_id",
            (self.dataset, stream)).fetchall()
        segments = []
        for recording_id, start_ns, rate in rows:
            status = self.client.stream_status(recording_id)
            segments.append(Segment(recording_id, start_ns, rate,
                                    0 if status is None else status["received_samples"]))
        self.segments[stream] = segments
        return segments

    def _event(self, stream, kind, sample_ns=None, recording_id=None, **detail):
        self._events.append((self.dataset, stream, kind, sample_ns, recording_id, Jsonb(detail)))
        key = {"gap": "gaps", "overlap_conflict": "conflicts", "rejected_chunk": "errors"}.get(kind)
        if key:
            self._progress.setdefault(stream, {}).setdefault(key, 0)
            self._progress[stream][key] += 1

    # -- placement -----------------------------------------------------------
    def recording_id(self, chunk, reason):
        """Identity rule documented in the module docstring."""
        plain = f"{self.dataset}/{chunk.stream}/{iso_ns(chunk.start_ns)}"
        if reason != "overlap_conflict":
            return plain
        content = np.asarray(chunk.samples, dtype="<f8").tobytes()
        return f"{plain}#{hashlib.sha256(content).hexdigest()[:16]}"

    def _open(self, chunk, reason):
        self.flush()
        recording_id = self.recording_id(chunk, reason)
        known = {s.recording_id for s in self.segments[chunk.stream]}
        if recording_id in known or self.conn.execute(
                "SELECT 1 FROM seismic.segments WHERE recording_id=%s", (recording_id,)).fetchone():
            # Placement should have matched existing content; never reuse an identity.
            raise RuntimeError(f"recording identity already exists: {recording_id}")
        labels = self.split_of(chunk.stream, chunk.start_ns)
        split = labels["split"]
        meta = {**self.stream_metadata[chunk.stream], **labels, "dataset": self.dataset,
                "stream": chunk.stream, "segment_start_utc": iso_ns(chunk.start_ns),
                "segment_start_ns": str(chunk.start_ns), "ingest_version": INGEST_VERSION}
        self.client.ensure_recording(
            recording_id, chunk.sample_rate, chunk.stream.split(".")[-1], self.rep["units"], meta,
            window_size=self.rep["window_samples"], stride=self.rep["stride_samples"],
            normalization=self.rep["normalization"], extractor=self.rep["extractor"])
        self.conn.execute(
            "INSERT INTO seismic.segments(recording_id,dataset,stream,split,start_ns,sample_rate_hz,opened_reason)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (recording_id) DO NOTHING",
            (recording_id, self.dataset, chunk.stream, split, chunk.start_ns, chunk.sample_rate, reason))
        # No COMMIT here: the identity commits with its opening samples in flush(),
        # so a crash cannot leave an empty version whose suffix describes other data.
        segment = Segment(recording_id, chunk.start_ns, chunk.sample_rate, 0)
        segments = self.segments[chunk.stream]
        segments.append(segment)
        segments.sort(key=lambda s: (s.start_ns, s.recording_id))
        self.stats["segments_opened"] += 1
        self._opened.append((chunk.stream, recording_id))
        return segment

    def _stored(self, segment, start, end):
        values = []
        for lo in range(start, end, 32768):
            row = self.client.read_interval(segment.recording_id, lo, min(end, lo + 32768))
            values.extend(row["samples"])
        return np.asarray(values, dtype=float)

    def handle(self, chunk):
        """Place one chunk. Returns the action taken, for tests and diagnostics."""
        self.stats["chunks"] += 1
        samples = np.asarray(chunk.samples, dtype=float)
        problem = None
        if not (math.isfinite(chunk.sample_rate)
                and RATE_DOMAINS[self.rep["extractor"]](chunk.sample_rate)):
            problem = f"sample rate {chunk.sample_rate} outside {self.rep['extractor']} domain"
        elif len(samples) == 0 or len(samples) > 32768:
            problem = "chunk must contain 1..32768 samples"
        elif not np.isfinite(samples).all() or np.abs(samples).max() > 1e9:
            problem = "samples must be finite and within +/-1e9"
        if problem:
            self.stats["errors"] += 1
            self._event(chunk.stream, "rejected_chunk", chunk.start_ns, None, reason=problem,
                        origin=chunk.origin, samples=len(samples))
            return "rejected"
        segments = self.load(chunk.stream)
        pending_end = {}
        for segment, start, values, _ in self.pending:
            pending_end[segment.recording_id] = start + len(values)
        overlapped = False
        # Newest first: a replayed segment opened after a conflict must win.
        ordered = sorted(segments, key=lambda s: (s.start_ns, s.recording_id), reverse=True)
        for segment in ordered:
            if segment.rate != chunk.sample_rate:
                continue
            end = pending_end.get(segment.recording_id, segment.committed)
            position = (chunk.start_ns - segment.start_ns) * segment.rate / 1e9
            index = round(position)
            if index + len(samples) <= 0 or index > end:
                continue  # Does not touch this segment (gap or earlier data).
            if abs(position - index) > self.tolerance:
                if index < end:
                    overlapped = True
                continue
            if index < 0:
                overlapped = True  # Starts before this segment: never prepend.
                continue
            if index < end:
                self.flush()
                stop = min(end, index + len(samples))
                stored = self._stored(segment, index, stop)
                if not np.array_equal(stored, samples[:stop - index]):
                    overlapped = True
                    continue
                self.stats["duplicate_samples"] += stop - index
                self._progress.setdefault(chunk.stream, {}).setdefault("duplicate_samples", 0)
                self._progress[chunk.stream]["duplicate_samples"] += stop - index
                remainder = samples[stop - index:]
                if len(remainder) == 0:
                    return "duplicate"
                self._queue(segment, stop, remainder, chunk)
                return "partial_duplicate"
            # index == end: contiguous continuation on the segment's grid.
            others = [o.recording_id for o in ordered if o is not segment and o.rate == segment.rate
                      and abs((chunk.start_ns - o.start_ns) * o.rate / 1e9
                              - pending_end.get(o.recording_id, o.committed)) <= self.tolerance]
            if others:
                self._event(chunk.stream, "ambiguous_continuation", chunk.start_ns, segment.recording_id,
                            alternatives=others)
            jitter = abs(position - index)
            if jitter > 1e-6:
                self._event(chunk.stream, "absorbed_jitter", chunk.start_ns, segment.recording_id,
                            samples=round(jitter, 6))
            self._queue(segment, index, samples, chunk)
            return "append"
        previous = [s for s in segments if s.rate == chunk.sample_rate and s.start_ns < chunk.start_ns]
        if overlapped:
            reason = "overlap_conflict"
            self.stats["conflicts"] += 1
            self._event(chunk.stream, reason, chunk.start_ns, None, origin=chunk.origin)
        elif previous:
            reason = "gap"
            last = max(previous, key=lambda s: s.start_ns)
            self.stats["gaps"] += 1
            last_end = last.time_of(pending_end.get(last.recording_id, last.committed))
            self._event(chunk.stream, reason, chunk.start_ns, last.recording_id,
                        gap_s=(chunk.start_ns - last_end) / 1e9, origin=chunk.origin)
        else:
            reason = "no_earlier_segment"
        segment = self._open(chunk, reason)
        self._event(chunk.stream, "segment_opened", chunk.start_ns, segment.recording_id,
                    reason=reason)
        self._queue(segment, 0, samples, chunk)
        return reason

    # -- durable commit ------------------------------------------------------
    def _queue(self, segment, start, samples, chunk):
        if self.pending and self.pending[-1][0] is not segment:
            self.flush()
        self.pending.append((segment, start, samples, chunk))
        if len(self.pending) >= self.batch_chunks:
            self.flush()

    def flush(self):
        """Commit pending samples, events and progress counters in one transaction.

        Counters are snapshotted, written with the samples and cleared only after
        COMMIT, so a progress-only flush (for example an all-duplicate replay) is
        persisted, a repeated flush applies nothing twice, and a failed flush can be
        retried after the caller rolls back. Samples and features are idempotent;
        duplicate_samples counts verification work, so it grows on every replay.
        """
        counters = {stream: {k: v for k, v in c.items() if v}
                    for stream, c in self._progress.items()}
        counters = {stream: c for stream, c in counters.items() if c}
        if not self.pending and not self._events and not counters and not self._opened:
            return
        touched = set()
        started = time.perf_counter()
        if self.pending:
            segment = self.pending[0][0]
            batch = [(start, values) for _, start, values, _ in self.pending]
            received = self.client.append_chunks(segment.recording_id, batch)
            chunk = self.pending[-1][3]
            appended = sum(len(v) for _, v in batch)
            c = dict(counters.pop(chunk.stream, {}))
            c["committed_samples"] = c.get("committed_samples", 0) + appended
            c["committed_chunks"] = c.get("committed_chunks", 0) + len(batch)
            self._upsert_progress(chunk.stream, c, segment.recording_id, segment.time_of(received),
                                  chunk.emitted_wall)
            touched.add(segment.recording_id)
        for stream, c in counters.items():
            self._upsert_progress(stream, c)
        if self._events:
            with self.conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO seismic.events(dataset,stream,kind,sample_ns,recording_id,detail)"
                    " VALUES (%s,%s,%s,%s,%s,%s)", self._events)
        self.conn.commit()   # Samples, segments, checkpoint, progress and events together.
        committed_wall = time.time()
        elapsed = time.perf_counter() - started
        if self.pending:
            segment.committed = received
            self.latency["append_batch_s"].append(elapsed)
            self.latency["chunk_commit_s"].extend(committed_wall - c.emitted_wall
                                                  for _, _, _, c in self.pending)
            self.stats["appended_samples"] += appended
            self.stats["batches"] += 1
        opened, self._opened = self._opened, []
        self.pending, self._events, self._progress = [], [], {}
        # Callbacks see only committed state: new recordings first, then new samples.
        for stream, recording_id in opened:
            if self.on_open:
                self.on_open(stream, recording_id)
        if touched and self.on_commit:
            self.on_commit(touched)

    def _upsert_progress(self, stream, p, recording_id=None, end_ns=None, emitted=None):
        self.conn.execute(
            """INSERT INTO seismic.progress AS p(dataset,stream,mode,last_recording_id,last_committed_end_ns,
                 committed_samples,committed_chunks,duplicate_samples,conflicts,gaps,errors,last_chunk_emitted_wall)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (dataset,stream) DO UPDATE SET mode=EXCLUDED.mode,
                 last_recording_id=coalesce(EXCLUDED.last_recording_id,p.last_recording_id),
                 last_committed_end_ns=CASE WHEN EXCLUDED.last_committed_end_ns IS NULL THEN p.last_committed_end_ns
                   ELSE greatest(coalesce(p.last_committed_end_ns,0),EXCLUDED.last_committed_end_ns) END,
                 committed_samples=p.committed_samples+EXCLUDED.committed_samples,
                 committed_chunks=p.committed_chunks+EXCLUDED.committed_chunks,
                 duplicate_samples=p.duplicate_samples+EXCLUDED.duplicate_samples,
                 conflicts=p.conflicts+EXCLUDED.conflicts, gaps=p.gaps+EXCLUDED.gaps,
                 errors=p.errors+EXCLUDED.errors,
                 last_chunk_emitted_wall=coalesce(EXCLUDED.last_chunk_emitted_wall,p.last_chunk_emitted_wall),
                 updated_wall=now()""",
            (self.dataset, stream, self.mode, recording_id, end_ns, p.get("committed_samples", 0),
             p.get("committed_chunks", 0), p.get("duplicate_samples", 0), p.get("conflicts", 0),
             p.get("gaps", 0), p.get("errors", 0), emitted))


def run(source, ingestor, *, detector=None, log=None, report_every=500):
    """Drive any source through the shared ingestor; flushes on exhaustion."""
    actions = {}
    for chunk in source.chunks():
        action = ingestor.handle(chunk)
        actions[action] = actions.get(action, 0) + 1
        if log and ingestor.stats["chunks"] % report_every == 0:
            log(json.dumps({"chunks": ingestor.stats["chunks"], "sample_utc": iso_ns(chunk.start_ns),
                            **{k: v for k, v in ingestor.stats.items() if k != "chunks"}}))
    ingestor.flush()
    # Finalize only when the input ended naturally. An operator stop, a truncated
    # demonstration run or live input leaves recordings open for resumed data.
    if detector is not None and getattr(source, "exhausted", False):
        # Only this run's streams: other processes may still be appending to theirs.
        detector.close_all(streams=sorted(ingestor.segments))
    return actions
