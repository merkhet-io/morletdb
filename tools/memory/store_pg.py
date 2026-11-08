"""PostgreSQL store for the policy engine: every change goes through the extension's SQL functions
(morlet_set_level and the run functions), so privileges, row security and the ledger apply as for any caller."""
from __future__ import annotations

import json
from typing import Sequence

import psycopg

from policy import POLICY_VERSION, Episode, Move


class PgStore:
    def __init__(self, conn: psycopg.Connection, collection: str):
        self.conn, self.collection = conn, collection

    def episodes(self) -> list[Episode]:
        rows = self.conn.execute(
            f"SELECT episode_id, recording_id, start_sample, end_sample, level, context, importance, exempt, "
            f"extract(epoch FROM now()-created_at) FROM {self.collection}_episodes ORDER BY episode_id").fetchall()
        return [Episode(r[0], r[1], r[2], r[3], r[4], r[5] or {}, r[6], r[7], float(r[8])) for r in rows]

    def bytes_at(self) -> int:
        """Bytes of chunks, kept summaries and envelopes of the collection (what the ladder can free)."""
        c = self.collection
        q = (f"SELECT coalesce((SELECT sum(octet_length(encoded)) FROM {c}_chunks),0)"
             f" + coalesce((SELECT sum(octet_length(summary)) FROM {c}_kept_summaries),0)"
             f" + coalesce((SELECT sum(pg_column_size(e.*)) FROM {c}_envelopes e),0)")
        try:
            return int(self.conn.execute(q).fetchone()[0])
        except psycopg.errors.UndefinedTable:
            self.conn.rollback()
            q = q.replace(f" + coalesce((SELECT sum(pg_column_size(e.*)) FROM {c}_envelopes e),0)", "")
            return int(self.conn.execute(q).fetchone()[0])

    def freed(self, moves: Sequence[Move]) -> list[int]:
        """Dry run inside a transaction that is rolled back: nothing, not even the dry run's record, persists."""
        if not moves:
            return []
        with self.conn.transaction(force_rollback=True):
            run = self.conn.execute("SELECT morlet_start_memory_run(%s::regclass,'estimate',%s,'{}',true)",
                                    (self.collection, POLICY_VERSION)).fetchone()[0]
            rows = self.conn.execute("SELECT episode_id, bytes_freed FROM morlet_set_level(%s::regclass,%s,%s,true)",
                                     (self.collection, run, json.dumps([m.as_json() for m in moves]))).fetchall()
        by_id = {}
        for eid, b in rows:
            by_id.setdefault(eid, []).append(int(b))
        return [by_id[m.episode_id].pop(0) for m in moves]

    def apply(self, run_id: int, moves: Sequence[Move]) -> list[int]:
        with self.conn.transaction():
            rows = self.conn.execute("SELECT episode_id, bytes_freed FROM morlet_set_level(%s::regclass,%s,%s,false)",
                                     (self.collection, run_id, json.dumps([m.as_json() for m in moves]))).fetchall()
        return [int(b) for _, b in rows]

    def start_run(self, policy: str, params: dict, dry_run: bool) -> int:
        with self.conn.transaction():
            return self.conn.execute("SELECT morlet_start_memory_run(%s::regclass,%s,%s,%s,%s)",
                                     (self.collection, policy, POLICY_VERSION, json.dumps(params), dry_run)).fetchone()[0]

    def finish_run(self, run_id: int, status: str, summary: dict) -> None:
        with self.conn.transaction():
            self.conn.execute("SELECT morlet_finish_memory_run(%s::regclass,%s,%s,%s)",
                              (self.collection, run_id, status, json.dumps(summary)))
