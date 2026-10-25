"""Thin SQL client for the canonical recording workflow; no local signal engine."""
from psycopg.rows import dict_row
from psycopg.types.composite import CompositeInfo, register_composite
from psycopg.types.json import Jsonb

DEFAULT_EXTRACTOR = "morlet-energy-v1"


class RecordingClient:
    """Uses the caller's connection, transaction and permissions unchanged."""

    def __init__(self, connection, collection):
        self.connection = connection
        self.collection = collection
        self._chunk_type = None

    def ensure_recording(self, recording_id, sample_rate_hz, channel, unit, metadata=None, *,
                         window_size=512, stride=256, normalization="zscore",
                         extractor=DEFAULT_EXTRACTOR):
        """Idempotent; a changed acquisition or extraction specification raises."""
        # Omit the default extractor so servers without the argument keep working.
        extra = () if extractor == DEFAULT_EXTRACTOR else (extractor,)
        self.connection.execute(
            "SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s,%s,%s,%s"
            + ",%s" * len(extra) + ")",
            (self.collection, recording_id, sample_rate_hz, channel, unit,
             Jsonb({} if metadata is None else metadata), window_size, stride,
             normalization, *extra))

    def append_chunks(self, recording_id, chunks):
        """Durable batch append of (start_sample, samples) pairs; returns received samples.

        Identical replays of committed chunks are accepted without duplication;
        changed replays, gaps and overlaps raise. The caller commits.
        """
        if self._chunk_type is None:
            info = CompositeInfo.fetch(self.connection, "public.morlet_input_chunk")
            if info is None:
                raise ValueError("server is missing the batched ingestion type")
            register_composite(info, self.connection)
            self._chunk_type = info.python_type
        rows = [self._chunk_type(int(start), [float(v) for v in samples])
                for start, samples in chunks]
        return self.connection.execute(
            "SELECT morlet_append_chunks(%s::regclass,%s,%s)",
            (self.collection, recording_id, rows)).fetchone()[0]

    def stream_status(self, recording_id):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM morlet_stream_status(%s::regclass,%s)",
                           (self.collection, recording_id))
            return cursor.fetchone()

    def read_interval(self, recording_id, start, end):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM morlet_read_interval(%s::regclass,%s,%s,%s)",
                (self.collection, recording_id, start, end),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("interval arguments must not be NULL")
            return row

    def search_interval(self, recording_id, start, end, *, metadata=None,
                        top_k=10, exclude_source=True, suppress_overlaps=True,
                        max_candidates=4096):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM morlet_search_interval(%s::regclass,%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.collection, recording_id, start, end,
                 Jsonb({} if metadata is None else metadata), top_k,
                 exclude_source, suppress_overlaps, max_candidates),
            )
            return cursor.fetchall()
