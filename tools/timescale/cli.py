#!/usr/bin/env python3
"""Manage the optional Timescale adapter. DATABASE_URL supplies the connection.

The adapter never deletes waveform samples. Retention applies only a previously
saved, revalidated plan for derived metrics. All privileges are the caller's.
"""
import argparse
import json
import os
from pathlib import Path

import psycopg

from bridge import Bridge, MINUTE_NS


def tick(bridge, max_windows, max_sources, refresh_minutes):
    if not (1<=max_windows<=1000 and 1<=max_sources<=1000 and 1<=refresh_minutes<=10080):
        raise ValueError('tick budgets outside supported limits')
    run=bridge.one("INSERT INTO morlet_metrics.worker_runs(state) VALUES ('started') RETURNING id")['id']
    try:
        cursor=bridge.one('SELECT worker_cursor FROM morlet_metrics.config')['worker_cursor']
        sources=bridge.all('SELECT source_id FROM morlet_metrics.sources ORDER BY (source_id<=%s),source_id LIMIT %s',
                           (cursor,max_sources))
        statuses=[]
        for row in sources:
            statuses.append(bridge.drain(row['source_id'],max_windows))
            bridge.conn.execute('UPDATE morlet_metrics.config SET worker_cursor=%s',(row['source_id'],))
        now=bridge.one('SELECT morlet_metrics.now_ns() AS ns')['ns']
        floor=bridge.one('SELECT pruned_before_ns FROM morlet_metrics.config')['pruned_before_ns']
        end=now//MINUTE_NS*MINUTE_NS
        start=max(floor,end-refresh_minutes*MINUTE_NS)
        if start<end:
            bridge.refresh(start,end)
        result={'sources':statuses,'refresh_start_ns':start,'refresh_end_ns':end,
                'refresh_performed':start<end,
                'more_sources':bridge.one('SELECT count(*) AS n FROM morlet_metrics.sources')['n']>len(sources),
                'note':'Historical publication outside this recent refresh window needs explicit refresh.'}
        bridge.conn.execute("UPDATE morlet_metrics.worker_runs SET state='complete',finished_at=clock_timestamp(),details=%s WHERE id=%s",
                            (psycopg.types.json.Jsonb(result),run))
        return result
    except Exception as exc:
        bridge.conn.execute("UPDATE morlet_metrics.worker_runs SET state='failed',finished_at=clock_timestamp(),details=%s WHERE id=%s",
                            (psycopg.types.json.Jsonb({'error':str(exc)}),run))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('install')
    register=sub.add_parser('register')
    register.add_argument('collection'); register.add_argument('recording'); register.add_argument('asset')
    register.add_argument('--window-samples',type=int,default=4096)
    drain=sub.add_parser('drain'); drain.add_argument('source_id',type=int)
    drain.add_argument('--max-windows',type=int,default=100)
    status=sub.add_parser('status'); status.add_argument('source_id',type=int)
    refresh=sub.add_parser('refresh'); refresh.add_argument('start_ns',type=int); refresh.add_argument('end_ns',type=int)
    plan=sub.add_parser('retention-plan'); plan.add_argument('cutoff_ns',type=int)
    apply=sub.add_parser('apply-retention'); apply.add_argument('plan_file',type=Path)
    resolve=sub.add_parser('resolve'); resolve.add_argument('evidence_id',type=int)
    resolve.add_argument('--viewer',default='http://127.0.0.1:8765')
    worker=sub.add_parser('tick')
    worker.add_argument('--max-windows',type=int,default=10)
    worker.add_argument('--max-sources',type=int,default=100)
    worker.add_argument('--refresh-minutes',type=int,default=60)
    a=p.parse_args()
    if a.command=='tick' and not (1<=a.max_sources<=1000 and 1<=a.refresh_minutes<=10080):
        p.error('max-sources must be 1..1000 and refresh-minutes 1..10080')
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,
                        options='-c statement_timeout=30000 -c lock_timeout=5000') as c:
        b=Bridge(c)
        if a.command=='install':
            b.install(); result={'schema_version':1}
        elif a.command=='register':
            with b.transaction():
                result={'source_id':b.register(a.collection,a.recording,a.asset,a.window_samples)}
        elif a.command=='drain':
            result=b.drain(a.source_id,a.max_windows)
        elif a.command=='status':
            result=b.status(a.source_id)
        elif a.command=='refresh':
            b.refresh(a.start_ns,a.end_ns); result={'refreshed':[a.start_ns,a.end_ns]}
        elif a.command=='retention-plan':
            result=b.retention_plan(a.cutoff_ns)
        elif a.command=='apply-retention':
            result=b.apply_retention(json.loads(a.plan_file.read_text()))
        elif a.command=='resolve':
            result=b.resolve(a.evidence_id)
            result.pop('samples',None)
            result['viewer_url']=b.viewer_url(a.evidence_id,a.viewer)
        else:
            result=tick(b,a.max_windows,a.max_sources,a.refresh_minutes)
    print(json.dumps(result,default=str,indent=2))


if __name__=='__main__':
    main()
