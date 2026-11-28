"""Native PG17/Timescale integration tests. Always start a disposable database server.

python -m unittest discover -s tools/timescale/tests -v
TC1_IMAGE can name a locally built combined image. No skipped integration tests.
"""
import concurrent.futures
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import struct
import sys
import threading
import time
import unittest

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools/timescale'))
from bridge import Bridge, DAY_NS, MINUTE_NS

spec=importlib.util.spec_from_file_location('tc1_runtime',ROOT/'evaluation/timescale_coexistence/run.py')
runtime=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
T0=1767225600000000000
VALUES=[-0.0,1.0,-1.0,2.0]*256
RAW=struct.pack('<1024d',*VALUES)


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name='morlet-bridge-test-'+secrets.token_hex(4)
        cls.password=secrets.token_hex(24)
        runtime.docker('run','-d','--name',cls.name,'--memory','1g','--cpus','2',
                       '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD',runtime.IMAGE,
                       env=dict(os.environ,POSTGRES_PASSWORD=cls.password))
        cls.port=int(runtime.docker('port',cls.name,'5432/tcp').decode().strip().split(':')[-1])
        try:
            for _ in range(90):
                try:
                    with cls.connection('postgres') as c:
                        c.execute('SELECT 1')
                    break
                except psycopg.OperationalError:
                    time.sleep(1)
            else:
                raise RuntimeError('PostgreSQL startup timeout')
        except BaseException:
            runtime.docker('rm','-f','-v',cls.name)
            raise

    @classmethod
    def tearDownClass(cls):
        runtime.docker('rm','-f','-v',cls.name)

    @classmethod
    def connection(cls,db,user='postgres'):
        return psycopg.connect(host='127.0.0.1',port=cls.port,user=user,password=cls.password,
                               dbname=db,autocommit=True,connect_timeout=3,
                               options='-c statement_timeout=30000 -c lock_timeout=10000')

    def setUp(self):
        self.db='bridge_'+secrets.token_hex(5)
        with self.connection('postgres') as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.db)))
        self.c=self.connection(self.db)
        self.c.execute('CREATE EXTENSION pg_morlet; CREATE EXTENSION timescaledb')
        self.c.execute("SELECT morlet_create_stream_collection('public','signals')")
        self.b=Bridge(self.c)
        self.b.install()
        self.start('a')
        self.append('a',0)
        with self.b.transaction():
            self.sid=self.b.register('public.signals','a','pump-A',1024)

    def tearDown(self):
        self.c.close()
        with self.connection('postgres') as c:
            c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(self.db)))

    def start(self,rid,rate=1024,ns=T0):
        self.c.execute("SELECT morlet_start_recording('signals',%s,%s,'vibration','g',%s,extractor=>'none')",
                       (rid,rate,Jsonb({'start_ns':ns,'asset':'pump-A'})))

    def append(self,rid,offset,values=VALUES):
        self.c.execute("SELECT morlet_append_chunk('signals',%s,%s,%s::float8[])",(rid,offset,values))

    def scalar(self,q,args=()):
        return self.c.execute(q,args).fetchone()[0]

    def test_atomic_retry_exact_provenance(self):
        with self.b.transaction():
            eid=self.b.publish(self.sid,0,1024)
            self.assertEqual(self.b.publish(self.sid,0,1024),eid)
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),1)
        self.assertEqual(self.b.resolve(eid)['samples'],RAW)
        row=self.b.one('SELECT * FROM morlet_metrics.metrics')
        self.assertEqual((row['time_ns'],row['sample_count'],row['mean']),(T0,1024,0.5))
        self.assertAlmostEqual(row['rms'],math.sqrt(1.5))
        with self.assertRaises(psycopg.errors.DivisionByZero):
            with self.b.transaction():
                self.append('a',1024)
                self.b.publish(self.sid,1024,2048)
                self.c.execute('SELECT 1/0')
        self.assertEqual(self.scalar("SELECT (checkpoint->>'end')::bigint FROM signals"),1024)
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),1)
        # A trusted writer changing a stored metric must not be masked by a retry.
        self.c.execute('UPDATE morlet_metrics.metrics SET rms=99')
        with self.assertRaisesRegex(ValueError,'conflicting stored metric'):
            with self.b.transaction():
                self.b.publish(self.sid,0,1024)

    def test_overlapping_and_partial_publications_refused(self):
        self.append('a',1024)
        for start,end in [(0,512),(512,1536),(0,2048)]:
            with self.assertRaisesRegex(ValueError,'complete, aligned'):
                with self.b.transaction():
                    self.b.publish(self.sid,start,end)
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.evidence'),0)

    def test_tick_fairness_and_failure_audit(self):
        from cli import tick
        self.start('b')
        self.append('b',0)
        with self.b.transaction():
            second=self.b.register('public.signals','b','pump-B',1024)
        first_run=tick(self.b,1,1,60)
        second_run=tick(self.b,1,1,60)
        self.assertTrue(first_run['more_sources'])
        self.assertEqual(first_run['sources'][0]['source_id'],self.sid)
        self.assertEqual(second_run['sources'][0]['source_id'],second)
        self.c.execute("UPDATE signals SET metadata=metadata||'{\"calibration\":2}'::jsonb WHERE recording_id='a'")
        with self.assertRaises(ValueError):
            tick(self.b,1,1,60)
        self.assertEqual(self.scalar('SELECT state FROM morlet_metrics.worker_runs ORDER BY id DESC LIMIT 1'),'failed')

    def test_changed_source_and_unavailable(self):
        with self.b.transaction():
            eid=self.b.publish(self.sid,0,1024)
        self.c.execute("UPDATE signals SET metadata=metadata||'{\"calibration\":2}'::jsonb")
        with self.assertRaisesRegex(ValueError,'specification or time axis changed'):
            with self.b.transaction():
                self.b.publish(self.sid,0,1024)
        self.assertNotEqual(self.b.resolve(eid)['state'],'raw_available')
        self.c.execute('DELETE FROM signals')
        self.assertIsNone(self.b.viewer_url(eid))

    def test_exact_ns_and_fractional_rejection(self):
        self.start('nano',rate=100000000,ns=T0+3)
        self.append('nano',0)
        self.start('fractional',rate=3)
        self.append('fractional',0)
        with self.b.transaction():
            sid=self.b.register('signals','nano','pump-A',1024)
            self.b.publish(sid,0,1024)
        self.assertEqual(self.scalar('SELECT time_ns FROM morlet_metrics.metrics'),T0+3)
        with self.assertRaisesRegex(ValueError,'exact bigint nanoseconds'):
            with self.b.transaction():
                sid=self.b.register('signals','fractional','pump-A',1)
                self.b.publish(sid,0,1)

    def test_worker_resume_and_terminated_transaction(self):
        self.append('a',1024)
        self.append('a',2048,VALUES[:100])
        self.assertEqual(self.b.drain(self.sid,1)['processed_end'],1024)
        victim=self.connection(self.db)
        b=Bridge(victim)
        try:
            with self.assertRaises(psycopg.OperationalError):
                with b.transaction():
                    b.publish(self.sid,1024,2048)
                    victim.execute('UPDATE morlet_metrics.sources SET processed_end=2048')
                    self.c.execute('SELECT pg_terminate_backend(%s)',(victim.info.backend_pid,))
                    victim.execute('SELECT 1')
        finally:
            victim.close()
        self.assertEqual(self.b.status(self.sid)['processed_end'],1024)
        result=self.b.drain(self.sid,10)
        self.assertTrue(result['complete'])
        self.assertEqual((result['processed_end'],result['partial_tail_samples']),(2048,100))
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),2)

    def test_concurrent_identical_publish(self):
        barrier=threading.Barrier(2)
        def publish():
            with self.connection(self.db) as c:
                b=Bridge(c)
                barrier.wait(timeout=5)
                for _ in range(3):
                    try:
                        with b.transaction():
                            return b.publish(self.sid,0,1024)
                    except psycopg.errors.SerializationFailure:
                        continue
                raise AssertionError('serialization retries exhausted')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(publish); b=pool.submit(publish)
            self.assertEqual(a.result(timeout=15),b.result(timeout=15))
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),1)

    def test_retention_preserves_aggregate_and_raw(self):
        self.b.drain(self.sid)
        plan=self.b.retention_plan(T0+DAY_NS)
        bad={**plan,'cutoff_ns':T0+2*DAY_NS}
        with self.assertRaisesRegex(ValueError,'plan changed'):
            self.b.apply_retention(bad)
        self.b.apply_retention(plan)
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),0)
        self.assertEqual(self.scalar('SELECT samples FROM morlet_metrics.minute'),1024)
        with self.assertRaisesRegex(ValueError,'retention floor'):
            self.b.refresh(T0,T0+MINUTE_NS)
        with self.assertRaisesRegex(ValueError,'retention floor'):
            with self.b.transaction():
                self.b.publish(self.sid,0,1024)
        eid=self.scalar('SELECT evidence_id FROM morlet_metrics.evidence')
        self.assertEqual(self.b.resolve(eid)['samples'],RAW)
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.retention_log'),1)

    def test_pending_retention_refused_and_refresh_late(self):
        with self.assertRaisesRegex(ValueError,'pending complete'):
            self.b.apply_retention(self.b.retention_plan(T0+DAY_NS))
        self.b.drain(self.sid)
        self.b.refresh(T0,T0+MINUTE_NS)
        self.append('a',1024)
        self.b.drain(self.sid)
        self.assertEqual(self.scalar('SELECT samples FROM morlet_metrics.minute'),1024)
        self.b.refresh(T0,T0+MINUTE_NS)
        self.assertEqual(self.scalar('SELECT samples FROM morlet_metrics.minute'),2048)

    def test_rls_refused_and_reader_permissions(self):
        self.b.drain(self.sid)
        reader='reader_'+secrets.token_hex(4)
        self.c.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(reader)))
        try:
            self.c.execute(sql.SQL('GRANT USAGE ON SCHEMA morlet_metrics TO {}').format(sql.Identifier(reader)))
            self.c.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA morlet_metrics TO {}').format(sql.Identifier(reader)))
            with self.c.transaction():
                self.c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(reader)))
                self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),1)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with self.c.transaction():
                        self.c.execute('DELETE FROM morlet_metrics.metrics')
            self.c.execute('ALTER TABLE signals ENABLE ROW LEVEL SECURITY')
            with self.assertRaisesRegex(ValueError,'RLS sources are unsupported'):
                with self.b.transaction():
                    self.b.publish(self.sid,0,1024)
        finally:
            self.c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(reader)))
            self.c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(reader)))

    def test_anchor_identity_and_revision(self):
        self.c.execute("SELECT morlet_enable_time_anchors('signals')")
        self.start('anchored')
        self.append('anchored',0)
        anchors=[{'recording_id':'anchored','sample_offset':n,'utc_ns':T0+n*1000000+3,
                  'uncertainty_ns':1,'source':'synthetic'} for n in (0,1024)]
        self.c.execute("SELECT morlet_add_time_anchors('signals','batch',%s)",(Jsonb(anchors),))
        with self.b.transaction():
            sid=self.b.register('signals','anchored','pump-A',1024)
            eid=self.b.publish(sid,0,1024)
        self.assertEqual(self.b.resolve(eid)['time_ns'],T0+3)
        aid=self.scalar("SELECT anchor_id FROM signals_time_anchors WHERE recording_id='anchored' AND sample_offset=0")
        self.c.execute("SELECT morlet_revise_time_anchor('signals',%s,%s)",(aid,Jsonb({'utc_ns':T0+4})))
        self.assertEqual(self.b.resolve(eid)['state'],'source_changed')
        with self.assertRaisesRegex(ValueError,'time axis changed'):
            with self.b.transaction():
                self.b.publish(sid,0,1024)

    def test_tenant_database_connect_boundary(self):
        other=self.db+'_other'
        reader='tenant_'+secrets.token_hex(4)
        with self.connection('postgres') as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(other)))
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(reader),sql.Literal(self.password)))
            for db in (self.db,other):
                admin.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(db)))
            admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(self.db),sql.Identifier(reader)))
        try:
            with self.connection(self.db,reader) as c:
                self.assertEqual(c.execute('SELECT current_user').fetchone()[0],reader)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute('SELECT * FROM morlet_metrics.metrics')
            with self.assertRaises(psycopg.OperationalError):
                self.connection(other,reader)
        finally:
            with self.connection('postgres') as admin:
                admin.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM {}').format(sql.Identifier(self.db),sql.Identifier(reader)))
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(other)))
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(reader)))

    def test_publisher_waits_for_retention_before_snapshot(self):
        self.b.drain(self.sid)
        ready=threading.Event()
        pids=[]
        def publish():
            with self.connection(self.db) as c:
                pids.append(c.info.backend_pid)
                ready.set()
                with Bridge(c).transaction() as b:
                    b.publish(self.sid,0,1024)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with self.b.maintenance():
                future=pool.submit(publish)
                self.assertTrue(ready.wait(5))
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if self.scalar("SELECT wait_event='advisory' FROM pg_stat_activity WHERE pid=%s",(pids[0],)):
                        break
                    time.sleep(0.01)
                else:
                    self.fail('publisher never waited for retention')
                self.b.apply_retention(self.b.retention_plan(T0+DAY_NS))
            with self.assertRaisesRegex(ValueError,'retention floor'):
                future.result(timeout=10)
        self.assertEqual(self.scalar('SELECT count(*) FROM morlet_metrics.metrics'),0)

    def test_compression_restore_nonempty_aggregate(self):
        self.b.drain(self.sid)
        self.b.refresh(T0,T0+MINUTE_NS)
        self.c.execute("ALTER TABLE morlet_metrics.metrics SET (timescaledb.compress,timescaledb.compress_segmentby='source_id',timescaledb.compress_orderby='time_ns')")
        self.c.execute("SELECT compress_chunk(x) FROM show_chunks('morlet_metrics.metrics') x")
        with self.b.transaction():
            eid=self.b.publish(self.sid,0,1024)
        dump=runtime.docker('exec',self.name,'pg_dump','-U','postgres','-Fc',self.db)
        restored=self.db+'_restore'
        with self.connection('postgres') as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored)))
        try:
            with self.connection(restored) as c:
                c.execute('CREATE EXTENSION timescaledb')
                c.execute('SELECT timescaledb_pre_restore()')
            runtime.docker('exec','-i',self.name,'pg_restore','-U','postgres','--exit-on-error','-d',restored,data=dump)
            with self.connection(restored) as c:
                c.execute('SELECT timescaledb_post_restore()')
                b=Bridge(c)
                self.assertEqual(b.resolve(eid)['samples'],RAW)
                self.assertEqual(b.one('SELECT samples FROM morlet_metrics.minute')['samples'],1024)
                self.assertTrue(b.status(self.sid)['complete'])
        finally:
            with self.connection('postgres') as c:
                c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(restored)))


if __name__=='__main__':
    unittest.main()
