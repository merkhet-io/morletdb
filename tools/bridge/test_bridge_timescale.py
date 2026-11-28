"""Metric bridge with a TimescaleDB hypertable as the PostgreSQL sink's target (#631, #639). Needs
MORLET_BRIDGE_TIMESCALE_DSN: a server with pg_morlet SQL 0.3.18 or later and TimescaleDB preloaded, where the role
may create databases and roles; each test gets its own database. For dump and restore, MORLET_PG_BIN names a directory
with pg_dump and pg_restore of the same major version. The image in tools/bridge/timescale is built for this:

  docker build -t morlet-bridge-timescale-test tools/bridge/timescale
  docker run -d --name morlet-bridge-ts -e POSTGRES_PASSWORD=... -p 127.0.0.1:28933:5432 morlet-bridge-timescale-test
  MORLET_BRIDGE_TIMESCALE_DSN='host=127.0.0.1 port=28933 user=postgres dbname=postgres password=...' \\
    python -m unittest tools/bridge/test_bridge_timescale.py
"""
import math
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from morlet_bridge import Bridge  # noqa: E402

DSN = os.environ.get("MORLET_BRIDGE_TIMESCALE_DSN")
PG_BIN = os.environ.get("MORLET_PG_BIN")
DAY = 86_400_000_000_000
T0 = 1767225600000000000
VALUES = [-0.0, 1.0, -1.0, 2.0] * 256


@unittest.skipUnless(DSN, "needs MORLET_BRIDGE_TIMESCALE_DSN")
class Hypertable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.types.json import Jsonb
        cls.psycopg, cls.sql, cls.Jsonb = psycopg, sql, Jsonb

    def connect(self, db=None, **kw):
        dsn = self.psycopg.conninfo.make_conninfo(DSN, **({"dbname": db} if db else {}), **kw)
        return self.psycopg.connect(dsn, autocommit=True, options="-c statement_timeout=60000 -c lock_timeout=10000")

    def make_db(self):
        db = "bridge_ts_" + secrets.token_hex(4)
        with self.connect() as c:
            c.execute(self.sql.SQL("CREATE DATABASE {}").format(self.sql.Identifier(db)))
        self.dbs.append(db)
        return db

    def setUp(self):
        self.dbs, self.roles, self.conns = [], [], []

    def tearDown(self):
        for c in self.conns:
            c.close()
        with self.connect() as c:
            for db in self.dbs:
                c.execute(self.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(self.sql.Identifier(db)))
            for r in self.roles:
                c.execute(self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(r)))

    def setup_db(self, timescale_first=False, rows_before=0):
        """A database with both extensions, the bridge, two recordings two days apart and the metrics table as a
        hypertable on time_ns (1-day chunks). rows_before: windows delivered before the conversion."""
        db = self.make_db()
        c = self.connect(db)
        self.conns.append(c)
        exts = ["timescaledb", "pg_morlet"] if timescale_first else ["pg_morlet", "timescaledb"]
        for e in exts:
            c.execute(self.sql.SQL("CREATE EXTENSION {}").format(self.sql.Identifier(e)))
        c.execute("SELECT morlet_create_stream_collection('public', 'signals')")
        b = Bridge(c)
        b.install()
        self.sources = {}
        for rid, start in (("day0", T0), ("day2", T0 + 2 * DAY + 5)):
            c.execute("SELECT morlet_start_recording('signals', %s, 1024, 'vibration', 'g', %s, extractor => 'none')",
                      (rid, self.Jsonb({"start_ns": start})))
            for k in range(4):
                c.execute("SELECT morlet_append_chunk('signals', %s, %s, %s::float8[])", (rid, 1024 * k, VALUES))
            with b.transaction():
                self.sources[rid] = b.register("public.signals", rid, "pump-" + rid, "vib-x", 1024)
        b.add_sink("ts", "postgres")
        if rows_before:
            b.drain(self.sources["day0"], rows_before)
            self.assertEqual(b.deliver("ts"), rows_before)
        c.execute("""SELECT create_hypertable('morlet_bridge.metrics', by_range('time_ns', %s::bigint),
                     migrate_data => true)""", (DAY,))
        return db, c, b

    def scalar(self, c, q, args=()):
        return c.execute(q, args).fetchone()[0]

    # ---- the sink
    def test_exactly_once_into_a_hypertable_in_both_install_orders(self):
        for first in (False, True):
            with self.subTest(timescale_first=first):
                db, c, b = self.setup_db(timescale_first=first, rows_before=2)
                for sid in self.sources.values():
                    b.drain(sid, 10)
                self.assertEqual(b.deliver("ts"), 6)               # 8 windows, 2 delivered before the conversion
                self.assertEqual(b.deliver("ts"), 0)
                self.assertEqual(self.scalar(c, "SELECT count(*) FROM morlet_bridge.metrics"), 8)
                self.assertEqual(self.scalar(c, """SELECT count(*) FROM timescaledb_information.chunks
                    WHERE hypertable_schema = 'morlet_bridge' AND hypertable_name = 'metrics'"""), 2)
                m = c.execute("""SELECT time_ns, mean, rms, recording_id, start_sample FROM morlet_bridge.metrics
                    ORDER BY time_ns LIMIT 1""").fetchone()
                self.assertEqual((m[0], m[1], m[3], m[4]), (T0, 0.5, "day0", 0))
                self.assertAlmostEqual(m[2], math.sqrt(1.5), places=15)
                self.assertEqual(b.sink_status("ts")["pending"], 0)

    def test_a_changed_copy_is_reported_in_a_compressed_chunk_and_delivery_stays_exactly_once(self):
        db, c, b = self.setup_db()
        b.drain(self.sources["day0"], 2)
        b.deliver("ts")
        c.execute("""ALTER TABLE morlet_bridge.metrics SET (timescaledb.compress,
                     timescaledb.compress_segmentby = 'asset, channel', timescaledb.compress_orderby = 'time_ns')""")
        self.assertEqual(self.scalar(c, """SELECT count(compress_chunk(ch)) FROM show_chunks('morlet_bridge.metrics') ch"""), 1)
        chunk = "SELECT chunk_name, is_compressed FROM timescaledb_information.chunks WHERE hypertable_name = 'metrics'"
        before = c.execute(chunk).fetchall()
        self.assertEqual([r[1] for r in before], [True])
        # the two remaining day-0 windows belong to the compressed chunk; a differing copy of one of them is placed
        # there first: delivery must report it, not keep it
        b.drain(self.sources["day0"], 10)
        lid, t = c.execute("""SELECT l.ledger_id, l.time_ns FROM morlet_bridge.ledger l LEFT JOIN morlet_bridge.deliveries d
            ON d.ledger_id = l.ledger_id WHERE d.ledger_id IS NULL ORDER BY l.ledger_id DESC LIMIT 1""").fetchone()
        self.assertLess(t, T0 + DAY)                               # inside the compressed day-0 chunk
        c.execute("""INSERT INTO morlet_bridge.metrics (ledger_id, time_ns, end_time_ns, asset, channel, calculation,
            collection, recording_id, start_sample, end_sample, sample_count, minimum, maximum, mean, rms)
            VALUES (%s, %s, 1, 'x', 'x', 'x', 'x', 'x', 0, 1, 1, 0, 0, 0, 0)""", (lid, t))
        self.assertEqual(c.execute(chunk).fetchall(), before)     # still the one chunk, still compressed
        with self.assertRaisesRegex(ValueError, "differs from the ledger"):
            b.deliver("ts")
        self.assertEqual(b.sink_status("ts")["pending"], 2)       # nothing of that batch was recorded
        # once the wrong copy is gone, both windows are delivered into the compressed chunk, exactly once
        c.execute("DELETE FROM morlet_bridge.metrics WHERE ledger_id = %s", (lid,))
        self.assertEqual(b.deliver("ts"), 2)
        self.assertEqual(b.deliver("ts"), 0)
        rows = c.execute("SELECT rms FROM morlet_bridge.metrics ORDER BY time_ns").fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(struct.pack("<d", r[0]) == struct.pack("<d", math.sqrt(1.5)) for r in rows))

    def test_concurrent_deliveries_into_a_hypertable(self):
        db, c, b = self.setup_db()
        for sid in self.sources.values():
            b.drain(sid, 10)
        counts, errors = [], []

        def run():
            try:
                with self.connect(db) as cc:
                    counts.append(Bridge(cc).deliver("ts", limit=1))
            except Exception as exc:                    # noqa: BLE001 - reported below
                errors.append(exc)
        for _ in range(3):
            ts = [threading.Thread(target=run) for _ in range(4)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(sum(counts), 8)
        self.assertEqual(self.scalar(c, "SELECT count(*) FROM morlet_bridge.metrics"), 8)

    def test_a_continuous_aggregate_matches_the_metrics_and_readers_need_grants(self):
        db, c, b = self.setup_db()
        for sid in self.sources.values():
            b.drain(sid, 10)
        b.deliver("ts")
        c.execute("""CREATE FUNCTION morlet_bridge.now_ns() RETURNS bigint LANGUAGE sql STABLE
                     AS $$ SELECT (extract(epoch FROM now()) * 1e9)::bigint $$""")
        c.execute("SELECT set_integer_now_func('morlet_bridge.metrics', 'morlet_bridge.now_ns')")
        c.execute("""CREATE MATERIALIZED VIEW morlet_bridge.metrics_minute WITH (timescaledb.continuous) AS
            SELECT time_bucket(60000000000::bigint, time_ns) AS bucket, asset, count(*) AS windows,
                   max(maximum) AS peak, sqrt(sum(rms * rms * sample_count) / sum(sample_count)) AS rms
            FROM morlet_bridge.metrics GROUP BY 1, 2 WITH NO DATA""")
        c.execute("CALL refresh_continuous_aggregate('morlet_bridge.metrics_minute', NULL, NULL)")
        agg = c.execute("SELECT bucket, asset, windows, peak, rms FROM morlet_bridge.metrics_minute ORDER BY 1, 2").fetchall()
        direct = c.execute("""SELECT time_ns / 60000000000 * 60000000000, asset, count(*), max(maximum),
            sqrt(sum(rms * rms * sample_count) / sum(sample_count)) FROM morlet_bridge.metrics GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
        self.assertEqual([r[:4] for r in agg], [tuple(r[:4]) for r in direct])
        for a, d in zip(agg, direct):
            self.assertAlmostEqual(a[4], d[4], places=12)
        reader = "bridge_ts_reader_" + secrets.token_hex(3)
        pw = secrets.token_hex(12)
        c.execute(self.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(self.sql.Identifier(reader), self.sql.Literal(pw)))
        self.roles.append(reader)
        with self.connect(db, user=reader, password=pw) as r:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                r.execute("SELECT count(*) FROM morlet_bridge.metrics")
        c.execute(self.sql.SQL("GRANT USAGE ON SCHEMA morlet_bridge TO {}").format(self.sql.Identifier(reader)))
        c.execute(self.sql.SQL("GRANT SELECT ON morlet_bridge.metrics, morlet_bridge.metrics_minute TO {}").format(
            self.sql.Identifier(reader)))
        with self.connect(db, user=reader, password=pw) as r:
            self.assertEqual(r.execute("SELECT count(*) FROM morlet_bridge.metrics").fetchone()[0], 8)
            self.assertEqual(r.execute("SELECT sum(windows) FROM morlet_bridge.metrics_minute").fetchone()[0], 8)
            for q in ("SELECT count(*) FROM morlet_bridge.ledger", "SELECT count(*) FROM signals_chunks"):
                with self.subTest(q=q), self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    r.execute(q)

    @unittest.skipUnless(PG_BIN, "needs MORLET_PG_BIN for pg_dump and pg_restore")
    def test_dump_and_restore_keep_the_hypertable_and_delivery_exact(self):
        db, c, b = self.setup_db(rows_before=2)
        b.drain(self.sources["day2"], 10)
        b.deliver("ts")
        target = self.make_db()
        with self.connect(target) as t:
            t.execute("CREATE EXTENSION timescaledb")
            t.execute("SELECT timescaledb_pre_restore()")
        conn = self.psycopg.conninfo.conninfo_to_dict(DSN)
        env = dict(os.environ, **({"PGPASSWORD": conn["password"]} if conn.get("password") else {}))
        args = ["-h", conn.get("host", "localhost"), "-p", str(conn.get("port", 5432)), "-U", conn.get("user", "postgres")]
        with tempfile.TemporaryDirectory() as d:
            dump = os.path.join(d, "db.dump")
            subprocess.run([os.path.join(PG_BIN, "pg_dump"), *args, "-w", "-Fc", "-f", dump, db], check=True, env=env,
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
            p = subprocess.run([os.path.join(PG_BIN, "pg_restore"), *args, "-w", "-d", target, dump], env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        with self.connect(target) as t:
            t.execute("SELECT timescaledb_post_restore()")
            self.assertEqual(t.execute("SELECT count(*) FROM morlet_bridge.metrics").fetchone()[0], 6)
            self.assertEqual(t.execute("""SELECT count(*) FROM timescaledb_information.hypertables
                WHERE hypertable_schema = 'morlet_bridge' AND hypertable_name = 'metrics'""").fetchone()[0], 1)
            rb = Bridge(t)
            rb.drain(self.sources["day0"], 10)                 # the remaining day-0 windows, after the restore
            self.assertEqual(rb.deliver("ts"), 2)
            self.assertEqual(rb.deliver("ts"), 0)
            self.assertEqual(t.execute("SELECT count(*) FROM morlet_bridge.metrics").fetchone()[0], 8)
            raw = t.execute("SELECT public.morlet_read_interval_raw('signals', 'day0', 0, 1024)").fetchone()[0]
            self.assertEqual(bytes(raw), struct.pack("<1024d", *VALUES))


if __name__ == "__main__":
    unittest.main()
