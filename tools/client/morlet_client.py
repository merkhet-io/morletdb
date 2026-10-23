"""Thin SQL client for the canonical recording workflow; no local signal engine."""
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class RecordingClient:
    """Uses the caller's connection, transaction and permissions unchanged."""

    def __init__(self, connection, collection):
        self.connection = connection
        self.collection = collection

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
                        top_k=10, exclude_source=True, suppress_overlaps=True):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM morlet_search_interval(%s::regclass,%s,%s,%s,%s,%s,%s,%s)",
                (self.collection, recording_id, start, end,
                 Jsonb({} if metadata is None else metadata), top_k,
                 exclude_source, suppress_overlaps),
            )
            return cursor.fetchall()
