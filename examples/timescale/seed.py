#!/usr/bin/env python3
"""Create a synthetic single-trust-domain database; reruns verify source identity."""
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/timescale'))
from bridge import Bridge, MINUTE_NS


def seed(dsn, reader_password, viewer_base='http://127.0.0.1:8765'):
    with psycopg.connect(dsn,autocommit=True) as c:
        c.execute('CREATE EXTENSION IF NOT EXISTS pg_morlet; CREATE EXTENSION IF NOT EXISTS timescaledb')
        b=Bridge(c)
        if not c.execute("SELECT to_regnamespace('morlet_metrics')").fetchone()[0]:
            b.install()
        if not c.execute("SELECT to_regclass('public.signals')").fetchone()[0]:
            c.execute("SELECT morlet_create_stream_collection('public','signals')")
            c.execute("SELECT morlet_enable_envelopes('signals')")
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='timescale_demo_reader'").fetchone():
            c.execute(sql.SQL('CREATE ROLE timescale_demo_reader LOGIN PASSWORD {}').format(sql.Literal(reader_password)))
        db=c.execute('SELECT current_database()').fetchone()[0]
        c.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(db)))
        c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO timescale_demo_reader').format(sql.Identifier(db)))
        # Persist the origin so rerunning the synthetic seed has identical identity.
        first=c.execute("SELECT metadata->>'start_ns' FROM signals WHERE recording_id='capture & A'").fetchone()
        origin=int(first[0]) if first else (c.execute('SELECT morlet_metrics.now_ns()').fetchone()[0]//MINUTE_NS-10)*MINUTE_NS
        for number,rid in enumerate(['capture & A','capture/B']):
            ns=origin+number*MINUTE_NS
            values=[float(((i//128)%7+1)*(1 if i%2 else -1)) for i in range(8192)]
            with b.transaction():
                c.execute("SELECT morlet_ensure_recording('signals',%s,1024,'vibration','g',%s,extractor=>'none')",
                          (rid,Jsonb({'asset':'pump-A','start_ns':ns,'synthetic':True})))
                c.execute("SELECT morlet_append_chunk('signals',%s,0,%s::float8[])",(rid,values))
                c.execute("SELECT morlet_build_envelopes('signals',%s)",(rid,))
                sid=b.register('signals',rid,'pump-A',4096)
            b.drain(sid)
        # Deliberately sparse and out of timestamp order, with exact integer counter.
        for offset,temp,counter,state in [(40,23.0,9007199254740995,'running'),(10,21.0,9007199254740993,'idle'),(25,22.0,9007199254740994,'running')]:
            c.execute('''INSERT INTO morlet_metrics.telemetry VALUES (%s,'pump-A',%s,%s,%s)
                         ON CONFLICT DO NOTHING''',(origin+offset*10**9,temp,counter,state))
        b.refresh(origin,origin+2*MINUTE_NS)
        # URL escaping happens before Grafana interpolation. Only generated URLs
        # to a fixed local viewer are stored; no arbitrary source metadata HTML.
        c.execute('CREATE TABLE IF NOT EXISTS morlet_metrics.viewer_links(evidence_id bigint PRIMARY KEY REFERENCES morlet_metrics.evidence,url text NOT NULL)')
        for row in b.all('SELECT evidence_id FROM morlet_metrics.evidence'):
            url=b.viewer_url(row['evidence_id'],viewer_base)
            c.execute('INSERT INTO morlet_metrics.viewer_links VALUES (%s,%s) ON CONFLICT (evidence_id) DO UPDATE SET url=excluded.url',
                      (row['evidence_id'],url))
        c.execute('GRANT USAGE ON SCHEMA public,morlet_metrics TO timescale_demo_reader')
        c.execute('GRANT SELECT ON signals,signals_chunks,signals_segments,signals_envelopes TO timescale_demo_reader')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA morlet_metrics TO timescale_demo_reader')
        c.execute('GRANT EXECUTE ON FUNCTION morlet_metrics.now_ns() TO timescale_demo_reader')
        # No writes, maintenance ownership or access to another tenant database.
        return origin


if __name__=='__main__':
    print(seed(os.environ['DATABASE_URL'],os.environ['READER_PASSWORD'],os.environ.get('VIEWER_URL','http://127.0.0.1:8765')))
