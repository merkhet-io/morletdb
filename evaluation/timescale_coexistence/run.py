#!/usr/bin/env python3
"""TC1 synthetic development experiment. Requires psycopg 3.2.10 and Docker.
Creates only a uniquely named container, with no host data mount; removes it on exit.
Run: .venv-b3/bin/python evaluation/timescale_coexistence/run.py
"""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import struct
import subprocess
import time
import traceback
from datetime import datetime, timezone

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
NAME = 'morlet-tc1-' + secrets.token_hex(4)
IMAGE = os.environ.get('TC1_IMAGE', 'morlet-tc1:experiment')
PASSWORD = secrets.token_hex(24)
PORT = None
REPORT = {'kind': 'development compatibility experiment', 'started_utc': STAMP,
          'protocol_sha256': hashlib.sha256((ROOT / 'PROTOCOL.md').read_bytes()).hexdigest(),
          'addendum_sha256': hashlib.sha256((ROOT / 'ADDENDUM.md').read_bytes()).hexdigest(),
          'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'cases': []}
OUT = ROOT / 'results' / (STAMP + '.json')
SAMPLES = [-0.0, 1.0, -1.0, 2.0] * 256
RAW = struct.pack('<1024d', *SAMPLES)


def docker(*args, data=None, env=None):
    return subprocess.run(['docker', *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env).stdout


def save():
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(REPORT, indent=2, default=str) + '\n')


def case(name, fn):
    try:
        result = fn()
        REPORT['cases'].append({'name': name, 'status': 'pass', 'observed': result})
        print(name + ': PASS', flush=True)
    except Exception as exc:
        REPORT['cases'].append({'name': name, 'status': 'fail', 'error': str(exc),
                                'sqlstate': getattr(exc, 'sqlstate', None),
                                'traceback': traceback.format_exc()})
        print(name + ': FAIL: ' + str(exc).splitlines()[0], flush=True)
    save()


def connect(db='tc1'):
    return psycopg.connect(host='127.0.0.1', port=PORT, user='postgres',
                           password=PASSWORD, dbname=db, autocommit=True,
                           connect_timeout=3, options='-c statement_timeout=30000')


def ready():
    for _ in range(90):
        try:
            with connect('postgres') as c:
                c.execute('SELECT 1')
            return
        except psycopg.OperationalError:
            time.sleep(1)
    raise RuntimeError('database startup timeout')


def start_recording(c, rid, tenant='a'):
    c.execute("SELECT morlet_start_recording('lab.signals',%s,1024,'vibration','g',%s,extractor=>'none')",
              (rid, Jsonb({'tenant': tenant, 'asset': tenant + '-pump',
                           'start_ns': 1767225600000000000})))


def append(c, rid, values=SAMPLES):
    c.execute("SELECT morlet_append_chunk('lab.signals',%s,0,%s::float8[])", (rid, values))


def raw(c, rid):
    return bytes(c.execute("SELECT morlet_read_interval_raw('lab.signals',%s,0,1024)", (rid,)).fetchone()[0])


def pair(c, rid, tenant='a'):
    start_recording(c, rid, tenant)
    append(c, rid)
    metric(c, rid, tenant)


def metric(c, rid, tenant='a'):
    c.execute("""INSERT INTO lab.metrics
      SELECT '2026-01-01'::timestamptz,%s,%s,0,1024,'stats-v1',sample_count,mean,rms
      FROM morlet_interval_stats('lab.signals',%s,0,1024)
      ON CONFLICT DO NOTHING""", (tenant, rid, rid))


def probe(c, statement):
    try:
        with c.transaction():
            c.execute(statement)
        return {'accepted': True}
    except psycopg.Error as exc:
        return {'accepted': False, 'sqlstate': exc.sqlstate, 'error': str(exc)}


def create_db(name, order):
    with connect('postgres') as c:
        c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    with connect(name) as c:
        for ext in order:
            c.execute(sql.SQL('CREATE EXTENSION {}').format(sql.Identifier(ext)))
        c.execute('CREATE SCHEMA lab')
        c.execute("SELECT morlet_create_stream_collection('lab','signals')")
        c.execute("""CREATE TABLE lab.metrics (
          time timestamptz NOT NULL, tenant text NOT NULL, recording_id text NOT NULL,
          start_sample bigint NOT NULL, end_sample bigint NOT NULL, calculation_version text NOT NULL,
          n bigint NOT NULL, mean float8 NOT NULL, rms float8 NOT NULL,
          PRIMARY KEY(time,recording_id,start_sample,calculation_version))""")
        c.execute("SELECT create_hypertable('lab.metrics','time',chunk_time_interval=>interval '1 day')")
        with c.transaction():
            pair(c, 'base')
        assert raw(c, 'base') == RAW
        result = c.execute("SELECT extname,extversion FROM pg_extension ORDER BY extname").fetchall()
        return {'extensions': result, 'binary_equal': True}


def atomicity():
    with connect() as c:
        stats = c.execute("SELECT * FROM morlet_interval_stats('lab.signals','base',0,1024)").fetchone()
        assert stats[:4] == (1024, -1.0, 2.0, 0.5), stats
        assert math.isclose(stats[4], math.sqrt(1.5), rel_tol=1e-14)
        try:
            with c.transaction():
                pair(c, 'aborted')
                c.execute('SELECT 1/0')
        except psycopg.errors.DivisionByZero:
            pass
        assert c.execute("SELECT count(*) FROM lab.signals WHERE recording_id='aborted'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM lab.metrics WHERE recording_id='aborted'").fetchone()[0] == 0
        with c.transaction():
            append(c, 'base')
            metric(c, 'base')
        assert c.execute("SELECT count(*) FROM lab.metrics WHERE recording_id='base'").fetchone()[0] == 1
        changed = SAMPLES.copy()
        changed[1] = 9.0
        try:
            with c.transaction():
                append(c, 'base', changed)
            raise AssertionError('conflicting retry accepted')
        except psycopg.Error as exc:
            conflict = {'sqlstate': exc.sqlstate, 'error': str(exc)}
        assert raw(c, 'base') == RAW
        assert c.execute("SELECT n,mean,rms FROM lab.metrics WHERE recording_id='base'").fetchone() == (1024,0.5,stats[4])
        return {'stats': stats, 'paired_rollback': True, 'identical_retry_rows': 1,
                'conflict': conflict, 'samples_and_metric_unchanged': True}


def interrupted():
    writer = connect()
    writer.execute('BEGIN')
    pair(writer, 'interrupted')
    pid = writer.info.backend_pid
    with connect() as c:
        assert c.execute('SELECT pg_terminate_backend(%s)', (pid,)).fetchone()[0]
        assert c.execute("SELECT count(*) FROM lab.signals WHERE recording_id='interrupted'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM lab.metrics WHERE recording_id='interrupted'").fetchone()[0] == 0
        with c.transaction():
            pair(c, 'interrupted')
        assert raw(c, 'interrupted') == RAW
    writer.close()
    return {'uncommitted_pair_absent': True, 'retry_committed_binary_equal': True}


AGG = """CREATE MATERIALIZED VIEW lab.minute WITH (timescaledb.continuous,
 timescaledb.materialized_only=true) AS
 SELECT time_bucket('1 minute',time) AS bucket,tenant,count(*) AS n,avg(value) AS mean
 FROM lab.telemetry GROUP BY 1,2 WITH NO DATA"""
REFRESH = "CALL refresh_continuous_aggregate('lab.minute','2026-01-01'::timestamptz,'2026-01-02'::timestamptz)"


def aggregates():
    with connect() as c:
        c.execute('CREATE TABLE lab.telemetry(time timestamptz NOT NULL,tenant text NOT NULL,value float8 NOT NULL)')
        c.execute("SELECT create_hypertable('lab.telemetry','time',chunk_time_interval=>interval '1 day')")
        c.execute("INSERT INTO lab.telemetry VALUES ('2026-01-01 00:00:40+00','a',4),('2026-01-01 00:00:10+00','a',2)")
        c.execute(AGG)
        c.execute(REFRESH)
        before = c.execute('SELECT n,mean FROM lab.minute').fetchone()
        c.execute("INSERT INTO lab.telemetry VALUES ('2026-01-01 00:00:25+00','a',12)")
        stale = c.execute('SELECT n,mean FROM lab.minute').fetchone()
        c.execute(REFRESH)
        after = c.execute('SELECT n,mean FROM lab.minute').fetchone()
        assert before == stale == (2,3.0)
        assert after == (3,6.0)
        return {'before': before, 'after_late_insert_before_refresh': stale, 'after_refresh': after}


def compression():
    with connect() as c:
        c.execute("ALTER TABLE lab.metrics SET (timescaledb.compress,timescaledb.compress_segmentby='tenant,recording_id',timescaledb.compress_orderby='time')")
        chunks = c.execute("SELECT compress_chunk(x) FROM show_chunks('lab.metrics') x").fetchall()
        rows = c.execute("""SELECT m.recording_id,m.n,m.mean,m.rms,
          morlet_read_interval_raw('lab.signals',s.recording_id,m.start_sample,m.end_sample)
          FROM lab.metrics m JOIN lab.signals s USING(recording_id) ORDER BY recording_id""").fetchall()
        assert len(rows) == 2
        assert all(bytes(r[4]) == RAW and r[1:3] == (1024,0.5) for r in rows)
        return {'compressed_chunks': chunks, 'drilldown_rows': len(rows), 'binary_equal': True}


def bridge_diagnostics():
    with connect() as c:
        before = c.execute("SELECT rms FROM lab.metrics WHERE recording_id='base'").fetchone()[0]
        cursor = c.execute("""INSERT INTO lab.metrics
          SELECT time,tenant,recording_id,start_sample,end_sample,calculation_version,n,mean,99
          FROM lab.metrics WHERE recording_id='base' ON CONFLICT DO NOTHING""")
        after = c.execute("SELECT rms FROM lab.metrics WHERE recording_id='base'").fetchone()[0]
        assert cursor.rowcount == 0 and after == before
        c.execute('CREATE TABLE lab.nano(time_ns bigint NOT NULL, display_time timestamptz, value int)')
        c.execute("SELECT create_hypertable('lab.nano','time_ns',chunk_time_interval=>86400000000000::bigint)")
        c.execute("""INSERT INTO lab.nano
          SELECT t,to_timestamp(t::numeric / 1000000000),1
          FROM unnest(ARRAY[1767225600000000000,1767225600000000003]::bigint[]) t""")
        counts = c.execute('SELECT count(DISTINCT time_ns),count(DISTINCT display_time) FROM lab.nano').fetchone()
        assert counts == (2,1), counts
        return {'changed_metric_retry': 'silently ignored by prototype ON CONFLICT DO NOTHING',
                'rows_inserted': cursor.rowcount, 'stored_rms': after,
                'distinct_ns_and_display_timestamps': counts}


SEARCH_VALUES = [float(((i // 128) % 7 + 1) * (1 if i % 2 else -1)) for i in range(8192)]
SEARCH_RAW = struct.pack('<8192d', *SEARCH_VALUES)


def check_search(c):
    hit = c.execute("""SELECT recording_id,start_sample,end_sample,distance
      FROM morlet_search_envelope_interval('lab.search_signals','query',0,4096,'{}',1)""").fetchone()
    assert hit and hit[0] == 'match' and hit[1:3] == (0,4096) and abs(hit[3]) < 1e-6, hit
    recovered = c.execute("SELECT morlet_read_interval_raw('lab.search_signals',%s,%s,%s)",hit[:3]).fetchone()[0]
    assert bytes(recovered) == SEARCH_RAW[:4096*8]
    return {'hit': hit, 'binary_equal': True}


def search_drilldown():
    with connect() as c:
        c.execute("SELECT morlet_create_stream_collection('lab','search_signals')")
        c.execute("SELECT morlet_enable_envelopes('lab.search_signals')")
        for rid in ['query','match']:
            c.execute("SELECT morlet_start_recording('lab.search_signals',%s,1024,'vibration','g',extractor=>'none')",(rid,))
            c.execute("SELECT morlet_append_chunk('lab.search_signals',%s,0,%s::float8[])",(rid,SEARCH_VALUES))
            c.execute("SELECT morlet_build_envelopes('lab.search_signals',%s)",(rid,))
        return check_search(c)


def permissions():
    with connect() as c:
        c.execute('CREATE ROLE tc1_reader')
        for name, early in [('secure_early',True), ('secure_late',False)]:
            c.execute(sql.SQL("SELECT morlet_create_stream_collection('lab',{})").format(sql.Literal(name)))
            if early:
                c.execute(sql.SQL('ALTER TABLE lab.{} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(name)))
                c.execute(sql.SQL("CREATE POLICY tenant ON lab.{} USING (metadata->>'tenant'='a')").format(sql.Identifier(name)))
                c.execute('SELECT morlet_enable_row_security(%s::regclass)', ('lab.'+name,))
            c.execute('SELECT morlet_enable_chunk_summaries(%s::regclass)', ('lab.'+name,))
            c.execute('SELECT morlet_enable_rollups(%s::regclass)', ('lab.'+name,))
            if not early:
                c.execute(sql.SQL('ALTER TABLE lab.{} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(name)))
                c.execute(sql.SQL("CREATE POLICY tenant ON lab.{} USING (metadata->>'tenant'='a')").format(sql.Identifier(name)))
                c.execute('SELECT morlet_enable_row_security(%s::regclass)', ('lab.'+name,))
            for tenant in ['a','b']:
                c.execute("SELECT morlet_start_recording(%s::regclass,%s,1024,'vibration','g',%s,extractor=>'none')",
                          ('lab.'+name,tenant,Jsonb({'tenant':tenant})))
                c.execute('SELECT morlet_append_chunk(%s::regclass,%s,0,%s::float8[])',('lab.'+name,tenant,SAMPLES))
        c.execute('CREATE TABLE lab.secure_metrics(time timestamptz NOT NULL,tenant text,value float8)')
        c.execute("SELECT create_hypertable('lab.secure_metrics','time')")
        c.execute("INSERT INTO lab.secure_metrics VALUES ('2026-01-01','a',1),('2026-01-01','b',2)")
        c.execute("ALTER TABLE lab.secure_metrics ENABLE ROW LEVEL SECURITY")
        c.execute("CREATE POLICY tenant ON lab.secure_metrics USING (tenant='a')")
        c.execute('GRANT USAGE ON SCHEMA lab TO tc1_reader')
        # Grant only the tested RLS-protected surfaces, never the unrestricted aggregate.
        for table in ['secure_early','secure_early_chunks','secure_early_segments','secure_early_rollups',
                      'secure_late','secure_late_chunks','secure_late_segments','secure_late_rollups','secure_metrics']:
            c.execute(sql.SQL('GRANT SELECT ON lab.{} TO tc1_reader').format(sql.Identifier(table)))
        with c.transaction():
            c.execute('SET LOCAL ROLE tc1_reader')
            for name in ['secure_early','secure_late']:
                for suffix in ['', '_chunks']:
                    rows = c.execute(sql.SQL('SELECT recording_id FROM lab.{}').format(sql.Identifier(name+suffix))).fetchall()
                    assert rows == [('a',)], rows
                data = c.execute('SELECT morlet_read_interval_raw(%s::regclass,\'a\',0,1024)',('lab.'+name,)).fetchone()[0]
                assert bytes(data) == RAW
            assert c.execute('SELECT tenant,value FROM lab.secure_metrics').fetchall() == [('a',1.0)]
        agg = """CREATE MATERIALIZED VIEW lab.secure_minute WITH (timescaledb.continuous) AS
          SELECT time_bucket('1 minute',time) bucket,tenant,avg(value) value
          FROM lab.secure_metrics GROUP BY 1,2 WITH NO DATA"""
        rls_before = probe(c,agg)
        rls_compression = probe(c,"ALTER TABLE lab.secure_metrics SET (timescaledb.compress,timescaledb.compress_segmentby='tenant')")
        c.execute('CREATE TABLE lab.later(time timestamptz NOT NULL,tenant text,value float8)')
        c.execute("SELECT create_hypertable('lab.later','time')")
        c.execute(agg.replace('secure_minute','later_minute').replace('secure_metrics','later'))
        rls_after = probe(c,'ALTER TABLE lab.later ENABLE ROW LEVEL SECURITY')
        return {'non_owner_direct_reads_both_enablement_orders': True,
                'rls_before_cagg': rls_before, 'rls_after_cagg': rls_after,
                'compression_on_rls': rls_compression,
                'note': 'No aggregate granted to tenant; acceptance alone is not authorization support.'}


def retention():
    with connect() as c:
        dropped = c.execute("SELECT drop_chunks('lab.telemetry',older_than=>'2026-01-02'::timestamptz)").fetchall()
        remaining = c.execute('SELECT count(*) FROM lab.telemetry').fetchone()[0]
        before = c.execute('SELECT n,mean FROM lab.minute').fetchall()
        c.execute(REFRESH)
        after = c.execute('SELECT n,mean FROM lab.minute').fetchall()
        assert remaining == 0 and raw(c,'base') == RAW
        assert before == [(3,6.0)]
        assert after == []
        return {'dropped':dropped, 'source_rows':remaining,'aggregate_before_refresh':before,
                'aggregate_after_refresh':after,'morlet_binary_equal':True}


def restart():
    global PORT
    docker('restart',NAME)
    # Docker Desktop may allocate a new host port when restarting an ephemeral mapping.
    PORT = int(docker('port',NAME,'5432/tcp').decode().strip().split(':')[-1])
    ready()
    with connect() as c:
        assert raw(c,'base') == RAW and raw(c,'interrupted') == RAW
        assert c.execute('SELECT count(*) FROM lab.metrics').fetchone()[0] == 2
        return {'samples_binary_equal':True,'committed_metrics':2,'search':check_search(c)}


def restore():
    dump = docker('exec',NAME,'pg_dump','-U','postgres','-Fc','tc1')
    with connect('postgres') as c:
        c.execute('CREATE DATABASE tc1_restored')
    with connect('tc1_restored') as c:
        c.execute('CREATE EXTENSION timescaledb')
        c.execute('SELECT timescaledb_pre_restore()')
    docker('exec','-i',NAME,'pg_restore','-U','postgres','--exit-on-error','-d','tc1_restored',data=dump)
    with connect('tc1_restored') as c:
        c.execute('SELECT timescaledb_post_restore()')
        assert raw(c,'base') == RAW and raw(c,'interrupted') == RAW
        assert c.execute('SELECT count(*) FROM lab.metrics').fetchone()[0] == 2
        assert c.execute('SELECT count(*) FROM lab.minute').fetchone()[0] == 0
        return {'dump_bytes':len(dump),'samples_binary_equal':True,'metric_rows':2,'aggregate_rows':0,
                'search':check_search(c)}


def main():
    global PORT
    REPORT['checkout_revision'] = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    # Inspect only immutable image metadata, never container Env (contains credentials).
    REPORT['image'] = json.loads(docker('image','inspect',IMAGE,'--format','{{json .Id}}'))
    REPORT['architecture'] = docker('image','inspect',IMAGE,'--format','{{.Architecture}}').decode().strip()
    REPORT['morlet_release_labels'] = json.loads(docker('image','inspect',IMAGE,'--format','{{json .Config.Labels}}'))
    env = dict(os.environ,POSTGRES_PASSWORD=PASSWORD)
    docker('run','-d','--name',NAME,'--memory','1g','--cpus','2',
           '-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD',IMAGE,env=env)
    try:
        PORT = int(docker('port',NAME,'5432/tcp').decode().strip().split(':')[-1])
        ready()
        REPORT['timescale_source_revision'] = docker('exec',NAME,'cat','/usr/share/doc/timescaledb-source-revision').decode().strip()
        with connect('postgres') as c:
            REPORT['postgres'] = c.execute('SELECT version()').fetchone()[0]
        case('morlet_then_timescale',lambda:create_db('tc1',['pg_morlet','timescaledb']))
        case('timescale_then_morlet',lambda:create_db('tc1_reverse',['timescaledb','pg_morlet']))
        for name, fn in [('atomicity_and_retries',atomicity),('terminated_writer_resume',interrupted),
                         ('late_telemetry_refresh',aggregates),('bridge_contract_diagnostics',bridge_diagnostics),
                         ('compressed_metrics_drilldown',compression),('exact_search_drilldown',search_drilldown),
                         ('permissions_and_supported_combinations',permissions),('retention_refresh',retention),
                         ('restart',restart),('logical_restore',restore)]:
            case(name,fn)
    finally:
        REPORT['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
        docker('rm','-f','-v',NAME)
    print(str(OUT),flush=True)
    return int(any(c['status']=='fail' for c in REPORT['cases']))


if __name__ == '__main__':
    raise SystemExit(main())
