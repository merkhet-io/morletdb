"""Prometheus metrics for a MorletDB database and its steward (#197; docs/steward-design.md, "Status for people").

`morlet-steward metrics` serves them over HTTP in the Prometheus text format (version 0.0.4), from the standard
library only. A scrape only reads, and needs no privilege the steward does not already have. Run it as
morlet_steward, or as a role that can read the morlet_steward schema and the collections; pg_monitor adds the WAL
size. Every query has a statement timeout, and per-collection statistics are cached for `collection_every_s`, so
a scrape stays cheap on a large archive.

The metrics:
- morlet_up: whether the last scrape reached the database. When it is 0, it is the only metric.
- morlet_steward_state: 0 normal, 1 attention, 2 action required (the status view, including "the steward
  stopped reporting").
- morlet_steward_status_items{level}, morlet_steward_heartbeat_age_seconds.
- morlet_steward_breaker_open{job}, morlet_steward_breaker_failures{job}.
- morlet_steward_actions_24h{job,outcome}, morlet_steward_plans_waiting.
- morlet_ingest_hold: 1 while appends of new samples are held.
- morlet_disk_free_bytes, morlet_disk_total_bytes, morlet_database_bytes: the steward's latest disk sample.
- morlet_verify_last_pass_age_seconds{collection}, morlet_verify_last_pass_problems{collection}.
- morlet_collection_*{collection,window_size}: from morlet_collection_status (recordings, raw samples, unbuilt
  samples, probe backlog, bytes).
- morlet_table_dead_tuples{table}, morlet_table_autovacuum_age_seconds{table}: the collections' tables, from
  pg_stat_user_tables.
"""
from __future__ import annotations

import http.server
import threading
import time

import psycopg

LEVELS = {"normal": 0, "attention": 1, "action_required": 2}
STATUS_COLUMNS = ("recordings", "raw_samples", "envelopes", "recordings_without_envelopes", "unbuilt_samples",
                  "probe_rows", "probe_backlog", "recordings_bytes", "chunk_bytes", "segment_bytes", "envelope_bytes",
                  "probe_table_bytes", "probe_index_bytes")
HELP = {
    "morlet_up": "1 when the last scrape reached the database",
    "morlet_steward_state": "overall steward state: 0 normal, 1 attention, 2 action required",
    "morlet_steward_status_items": "status items by level",
    "morlet_steward_heartbeat_age_seconds": "seconds since the steward last wrote its status",
    "morlet_steward_breaker_open": "1 when a job's circuit breaker is open",
    "morlet_steward_breaker_failures": "consecutive failures of a job",
    "morlet_steward_actions_24h": "steward actions started in the last 24 hours, by job and outcome",
    "morlet_steward_plans_waiting": "plans neither decided nor expired",
    "morlet_ingest_hold": "1 while appends of new samples are held",
    "morlet_disk_free_bytes": "free bytes on the database's filesystem (steward's latest sample)",
    "morlet_disk_total_bytes": "size of the database's filesystem (steward's latest sample)",
    "morlet_database_bytes": "database size plus WAL (steward's latest sample)",
    "morlet_verify_last_pass_age_seconds": "seconds since the last completed integrity pass of a collection",
    "morlet_verify_last_pass_problems": "problems found by the last completed integrity pass of a collection",
    "morlet_table_dead_tuples": "dead tuples of a collection table",
    "morlet_table_autovacuum_age_seconds": "seconds since a collection table was last vacuumed (auto or manual)",
}
for _c in STATUS_COLUMNS:
    HELP[f"morlet_collection_{_c}"] = f"{_c.replace('_', ' ')} (morlet_collection_status)"


def escape(value: str) -> str:
    """A label value in the text format: backslash, double quote and line feed escaped."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(samples: list[tuple[str, dict, float]]) -> str:
    """Text format 0.0.4: each metric once with HELP and TYPE gauge, then its samples, in first-seen order."""
    order: list[str] = []
    by_name: dict[str, list[tuple[dict, float]]] = {}
    for name, labels, value in samples:
        if name not in by_name:
            order.append(name)
            by_name[name] = []
        by_name[name].append((labels, value))
    out = []
    for name in order:
        out.append(f"# HELP {name} {HELP.get(name, name)}")
        out.append(f"# TYPE {name} gauge")
        for labels, value in by_name[name]:
            lab = ",".join(f'{k}="{escape(v)}"' for k, v in labels.items())
            out.append(f"{name}{{{lab}}} {format_value(value)}" if lab else f"{name} {format_value(value)}")
    return "\n".join(out) + "\n"


def format_value(v: float) -> str:
    if v != v:
        return "NaN"
    if v in (float("inf"), float("-inf")):
        return "+Inf" if v > 0 else "-Inf"
    return str(int(v)) if float(v).is_integer() and abs(v) < 2 ** 53 else repr(float(v))


class Collector:
    """Reads the metrics from one connection, reconnecting after an error. Per-collection statistics are cached."""

    def __init__(self, dsn: str, collection_every_s: float = 60.0, timeout_ms: int = 5000):
        self.dsn, self.collection_every_s, self.timeout_ms = dsn, collection_every_s, timeout_ms
        self.conn: psycopg.Connection | None = None
        self.cache: tuple[float, list] = (0.0, [])
        self.lock = threading.Lock()

    def connect(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.dsn, autocommit=True, application_name="morlet-steward-metrics",
                                        connect_timeout=5)
            self.conn.execute(f"SET statement_timeout = {int(self.timeout_ms)}")
        return self.conn

    def scrape(self) -> str:
        with self.lock:
            try:
                return render([("morlet_up", {}, 1)] + self.read(self.connect()))
            except psycopg.Error:
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                return render([("morlet_up", {}, 0)])

    def read(self, conn: psycopg.Connection) -> list[tuple[str, dict, float]]:
        s: list[tuple[str, dict, float]] = []
        steward = conn.execute("SELECT to_regclass('morlet_steward.overall') IS NOT NULL").fetchone()[0]
        if steward:
            level = conn.execute("SELECT level FROM morlet_steward.overall").fetchone()[0]
            s.append(("morlet_steward_state", {}, LEVELS.get(level, 2)))
            counts = dict(conn.execute("SELECT level,count(*) FROM morlet_steward.status GROUP BY level").fetchall())
            for lvl in LEVELS:
                s.append(("morlet_steward_status_items", {"level": lvl}, counts.get(lvl, 0)))
            age = conn.execute("SELECT extract(epoch FROM now()-at) FROM morlet_steward.heartbeat").fetchone()
            if age:
                s.append(("morlet_steward_heartbeat_age_seconds", {}, float(age[0])))
            for job, failures, is_open in conn.execute(
                    "SELECT job,failures,open FROM morlet_steward.breakers ORDER BY job"):
                s.append(("morlet_steward_breaker_open", {"job": job}, int(is_open)))
                s.append(("morlet_steward_breaker_failures", {"job": job}, failures))
            for job, outcome, n in conn.execute(
                    "SELECT job,outcome,count(*) FROM morlet_steward.actions WHERE started_at>now()-interval '24 hours' "
                    "GROUP BY job,outcome ORDER BY job,outcome"):
                s.append(("morlet_steward_actions_24h", {"job": job, "outcome": outcome}, n))
            s.append(("morlet_steward_plans_waiting", {}, conn.execute(
                "SELECT count(*) FROM morlet_steward.plans WHERE decision IS NULL AND now()<=valid_until").fetchone()[0]))
            disk = conn.execute("SELECT free_bytes,total_bytes,db_bytes FROM morlet_steward.disk_samples "
                                "ORDER BY at DESC LIMIT 1").fetchone()
            if disk:
                for name, v in zip(("morlet_disk_free_bytes", "morlet_disk_total_bytes", "morlet_database_bytes"), disk):
                    if v is not None:
                        s.append((name, {}, v))
            for coll, problems, age_s in conn.execute(
                    "SELECT collection,last_pass_problems,extract(epoch FROM now()-last_pass_finished_at) "
                    "FROM morlet_steward.verify_cursor WHERE last_pass_finished_at IS NOT NULL ORDER BY collection"):
                s.append(("morlet_verify_last_pass_age_seconds", {"collection": coll}, float(age_s)))
                s.append(("morlet_verify_last_pass_problems", {"collection": coll}, problems or 0))
        if conn.execute("SELECT to_regclass('public.morlet_ingest_hold') IS NOT NULL").fetchone()[0]:
            held = conn.execute("SELECT count(*)>0 FROM public.morlet_ingest_hold").fetchone()[0]
            s.append(("morlet_ingest_hold", {}, int(held)))
        at, cached = self.cache
        if time.monotonic() - at > self.collection_every_s or not cached:
            cached = self.collections(conn)
            self.cache = (time.monotonic(), cached)
        return s + cached

    def collections(self, conn: psycopg.Connection) -> list[tuple[str, dict, float]]:
        import store as S
        s: list[tuple[str, dict, float]] = []
        for c in S.collections(conn):
            if not S.privileged(conn, c):
                continue
            try:
                with conn.transaction():
                    rows = conn.execute(f"SELECT window_size,{','.join(STATUS_COLUMNS)} "
                                        f"FROM morlet_collection_status(%s::regclass)", (c,)).fetchall()
            except psycopg.errors.QueryCanceled:
                continue                                  # over the timeout: left out of this scrape
            for row in rows:
                labels = {"collection": c, "window_size": "" if row[0] is None else str(row[0])}
                for name, v in zip(STATUS_COLUMNS, row[1:]):
                    if v is not None:
                        s.append((f"morlet_collection_{name}", labels, v))
            # the collection's own tables (as the steward grants them), not other collections with the same prefix
            tables = [t for t in (S.sibling(conn, c, suffix) for suffix in S.READ) if t]
            schema, name = S.split(c)
            tables += [r[0] for r in conn.execute(
                "SELECT format('%%I.%%I',schemaname,tablename) FROM pg_tables WHERE schemaname=%s AND tablename LIKE %s",
                (schema, name.replace("_", "\\_") + "\\_probes\\_%"))]
            for table, dead, age_s in conn.execute(
                    "SELECT format('%%I.%%I',schemaname,relname),n_dead_tup,"
                    "extract(epoch FROM now()-greatest(last_autovacuum,last_vacuum)) FROM pg_stat_user_tables "
                    "WHERE relid=ANY(%s::regclass[]) ORDER BY 1", (tables,)):
                s.append(("morlet_table_dead_tuples", {"table": table}, dead))
                if age_s is not None:
                    s.append(("morlet_table_autovacuum_age_seconds", {"table": table}, float(age_s)))
        return s


def serve(collector: Collector, host: str, port: int) -> None:
    """GET /metrics until interrupted; any other path is 404."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            if self.path.split("?", 1)[0] != "/metrics":
                self.send_error(404)
                return
            body = collector.scrape().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with http.server.ThreadingHTTPServer((host, port), Handler) as httpd:
        httpd.serve_forever()
