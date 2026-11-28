#!/usr/bin/env python3
"""Optional Timescale adapter. All writes use the caller's privileges.

A database is one trust domain; aggregate access does not inherit recording RLS.
The writer is trusted to maintain the adapter tables, as with Morlet managed tables.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA_VERSION = 1
CALCULATION = 'interval-stats-v1'
DAY_NS = 86_400_000_000_000
MINUTE_NS = 60_000_000_000
# Stable lock names, independent of relation OIDs and safe across dump/restore.
LOCK = 'morlet-timescale-lifecycle-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False, default=str).encode()).hexdigest()


def integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer')
    return value


class Bridge:
    def __init__(self, connection):
        self.conn = connection
        self._publishing = False

    def one(self, query, params=()):
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def all(self, query, params=()):
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    @contextmanager
    def transaction(self):
        """One snapshot for source, provenance, metric and progress; caller may append here."""
        if self.conn.info.transaction_status != TransactionStatus.IDLE or not self.conn.autocommit:
            raise ValueError('bridge.transaction needs an idle autocommit connection')
        # Acquire outside the transaction: a REPEATABLE READ snapshot taken before
        # waiting for retention could otherwise see an obsolete expiry floor.
        self.conn.execute('SELECT pg_advisory_lock_shared(hashtextextended(%s,0))', (LOCK,))
        try:
            with self.conn.transaction():
                self.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                self.conn.execute('SET LOCAL statement_timeout = 30000')
                self._publishing = True
                yield self
        finally:
            self._publishing = False
            if not self.conn.closed:
                self.conn.execute('SELECT pg_advisory_unlock_shared(hashtextextended(%s,0))', (LOCK,))

    def _write_context(self):
        if not self._publishing or self.conn.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError('use bridge.transaction() for publication')
        isolation = self.one('SHOW transaction_isolation')['transaction_isolation']
        if isolation not in ('repeatable read', 'serializable'):
            raise ValueError('publication requires a repeatable-read snapshot')
        version = self.one('SELECT schema_version FROM morlet_metrics.config')['schema_version']
        if version != SCHEMA_VERSION:
            raise ValueError('unsupported adapter schema version')

    def install(self):
        """Explicit, fresh installation only. Does not change existing sources or grants."""
        if self.conn.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError('install needs an idle connection')
        with self.conn.transaction():
            self.conn.execute(Path(__file__).with_name('schema.sql').read_text())

    def source(self, collection, recording):
        rel = self.one('''SELECT n.nspname, c.relname, c.relrowsecurity,
            format('%%I.%%I',n.nspname,c.relname) AS qualified
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=to_regclass(%s) AND c.relkind='r' ''', (collection,))
        if not rel:
            raise ValueError('source collection unavailable')
        if rel['relrowsecurity']:
            raise ValueError('aggregate adapter requires a dedicated trust-domain database; RLS sources are unsupported')
        row = self.one(sql.SQL('SELECT * FROM {} WHERE recording_id=%s').format(
            sql.Identifier(rel['nspname'], rel['relname'])), (recording,))
        if not row:
            raise ValueError('source recording unavailable')
        spec = {k: v for k, v in row.items() if k != 'checkpoint'}
        axis = self.one('SELECT public.morlet_time_axis_version(%s::regclass,%s) AS version',
                        (rel['qualified'], recording))['version']
        return rel['qualified'], spec, axis, int(row['checkpoint']['end'])

    def register(self, collection, recording, asset, window_samples=4096):
        self._write_context()
        integer(window_samples, 'window_samples')
        if not 1 <= window_samples <= 32768 or not isinstance(asset, str) or not asset:
            raise ValueError('nonempty asset and window_samples in 1..32768 required')
        qualified, spec, axis, _ = self.source(collection, recording)
        self.conn.execute('''INSERT INTO morlet_metrics.sources
            (collection,recording_id,asset,window_samples,calculation_version,specification,time_axis_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (qualified, recording, asset, window_samples, CALCULATION, Jsonb(spec), axis))
        row = self.one('''SELECT * FROM morlet_metrics.sources
            WHERE collection=%s AND recording_id=%s AND calculation_version=%s FOR UPDATE''',
            (qualified, recording, CALCULATION))
        if (row['asset'],row['window_samples'],row['specification'],row['time_axis_version']) != (asset,window_samples,spec,axis):
            raise ValueError('conflicting source registration; use a separately versioned recording for corrected interpretation')
        return row['source_id']

    def _registered(self, source_id):
        row = self.one('SELECT * FROM morlet_metrics.sources WHERE source_id=%s', (source_id,))
        if not row:
            raise ValueError('source registration unavailable')
        _, spec, axis, received = self.source(row['collection'], row['recording_id'])
        if spec != row['specification'] or axis != row['time_axis_version']:
            raise ValueError('source specification or time axis changed; old metrics require review')
        return row, received

    def _time(self, row, sample):
        if row['time_axis_version'] == 'start+rate':
            # Use the stable mapping API: the beta.3 image's experimental UTC
            # mapper has the no-anchor-table defect fixed by upstream #562.
            origin = row['specification']['metadata'].get('start_ns')
            integer(origin, 'recording metadata.start_ns')
            return self.all('''SELECT public.morlet_sample_time_ns(%s,%s,%s) AS utc_ns,
                NULL::numeric AS uncertainty_ns, 'start+rate' AS basis,
                false AS assumes_linear_clock, false AS extrapolated, 0 AS segment''',
                (row['specification']['sample_rate_hz'], origin, sample))
        return self.all('SELECT * FROM public.morlet_sample_utc(%s::regclass,%s,%s)',
                        (row['collection'], row['recording_id'], sample))

    def publish(self, source_id, start, end):
        """Compute metrics from original bytes; clients cannot supply replacement values."""
        self._write_context()
        integer(start, 'start'); integer(end, 'end')
        row, received = self._registered(source_id)
        if start < 0 or end <= start or end-start > 32768 or end > received:
            raise ValueError('interval must contain 1..32768 available samples')
        if start % row['window_samples'] or end-start != row['window_samples']:
            raise ValueError('publication must use a complete, aligned registered window; overlapping metrics are refused')
        # Serialize competing publications for this source. Under REPEATABLE READ,
        # a concurrent commit raises serialization_failure; retry the whole transaction.
        self.conn.execute('SELECT source_id FROM morlet_metrics.sources WHERE source_id=%s FOR UPDATE', (source_id,))
        args = (row['collection'],row['recording_id'])
        a = self._time(row,start)
        b = self._time(row,end)
        if len(a)!=1 or len(b)!=1 or a[0]['segment']!=b[0]['segment']:
            raise ValueError('missing/ambiguous time or interval crosses a clock break')
        t0,t1 = a[0]['utc_ns'],b[0]['utc_ns']
        if any(v != v.to_integral_value() or not -(2**63) <= v < 2**63 for v in (t0,t1)) or t1<=t0:
            raise ValueError('interval endpoints must be increasing exact bigint nanoseconds; no rounding')
        time_ns,end_ns = int(t0),int(t1)
        floor = self.one('SELECT pruned_before_ns FROM morlet_metrics.config')['pruned_before_ns']
        if time_ns < floor:
            raise ValueError('metric interval precedes retention floor; historical publication refused')
        raw = bytes(self.one('SELECT public.morlet_read_interval_raw(%s::regclass,%s,%s,%s) AS raw',
                             (*args,start,end))['raw'])
        stats = self.one('SELECT * FROM public.morlet_interval_stats(%s::regclass,%s,%s,%s)', (*args,start,end))
        if len(raw)!=8*(end-start) or stats['sample_count']!=end-start:
            raise ValueError('incomplete source interval')
        source_sha = hashlib.sha256(raw).hexdigest()
        provenance = {'start':a[0], 'end':b[0], 'axis_version':row['time_axis_version']}
        payload = digest({'source_sha256':source_sha,'stats':stats,'time':provenance,
                          'specification':row['specification'],'calculation':CALCULATION})
        self.conn.execute('''INSERT INTO morlet_metrics.evidence
            (source_id,start_sample,end_sample,time_ns,end_time_ns,source_sha256,payload_sha256,time_provenance)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (source_id,start,end,time_ns,end_ns,source_sha,payload,Jsonb(json.loads(json.dumps(provenance,default=str)))))
        evidence = self.one('''SELECT * FROM morlet_metrics.evidence
            WHERE source_id=%s AND start_sample=%s AND end_sample=%s FOR UPDATE''',(source_id,start,end))
        if evidence['payload_sha256']!=payload:
            raise ValueError('conflicting metric retry; source or calculated payload changed')
        self.conn.execute('''INSERT INTO morlet_metrics.metrics
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (time_ns,evidence['evidence_id'],source_id,stats['sample_count'],stats['min'],stats['max'],stats['mean'],stats['rms']))
        stored = self.one('SELECT * FROM morlet_metrics.metrics WHERE time_ns=%s AND evidence_id=%s',
                          (time_ns,evidence['evidence_id']))
        if tuple(stored[k] for k in ('sample_count','minimum','maximum','mean','rms')) != tuple(stats[k] for k in ('sample_count','min','max','mean','rms')):
            raise ValueError('conflicting stored metric payload')
        return evidence['evidence_id']

    def drain(self, source_id, max_windows=100):
        """One transaction per complete window, including progress. Restart-safe and bounded."""
        integer(max_windows,'max_windows')
        if not 1<=max_windows<=1000:
            raise ValueError('max_windows must be in 1..1000')
        done=0
        for _ in range(max_windows):
            with self.transaction():
                self._write_context()
                row,received=self._registered(source_id)
                start=row['processed_end']; end=start+row['window_samples']
                if end>received:
                    break
                self.publish(source_id,start,end)
                self.conn.execute('UPDATE morlet_metrics.sources SET processed_end=%s WHERE source_id=%s',(end,source_id))
            done+=1
        return {'committed_windows':done,**self.status(source_id)}

    def status(self, source_id):
        row,received=self._registered(source_id)
        complete=received//row['window_samples']*row['window_samples']
        return {'source_id':source_id,'received_end':received,'processed_end':row['processed_end'],
                'pending_complete_samples':complete-row['processed_end'],'partial_tail_samples':received-complete,
                'complete':complete==row['processed_end']}

    @contextmanager
    def maintenance(self):
        if self.conn.info.transaction_status != TransactionStatus.IDLE or not self.conn.autocommit:
            raise ValueError('maintenance requires an idle autocommit connection')
        self.conn.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(LOCK,))
        try:
            yield
        finally:
            self.conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(LOCK,))

    def _refresh(self,start,end):
        floor=self.one('SELECT pruned_before_ns FROM morlet_metrics.config')['pruned_before_ns']
        if start<floor or start>=end or start%MINUTE_NS or end%MINUTE_NS:
            raise ValueError('refresh needs whole minute boundaries at or after the retention floor')
        self.conn.execute("CALL public.refresh_continuous_aggregate('morlet_metrics.minute',%s::bigint,%s::bigint)",(start,end))
        self.conn.execute('INSERT INTO morlet_metrics.refreshes(start_ns,end_ns) VALUES (%s,%s)',(start,end))

    def refresh(self,start,end):
        integer(start,'start'); integer(end,'end')
        with self.maintenance():
            self._refresh(start,end)

    def retention_plan(self,cutoff):
        integer(cutoff,'cutoff')
        if cutoff%DAY_NS:
            raise ValueError('cutoff must be an exact UTC day boundary')
        floor=self.one('SELECT pruned_before_ns FROM morlet_metrics.config')['pruned_before_ns']
        if cutoff<=floor:
            raise ValueError('cutoff must advance the retention floor')
        chunks=self.all('''SELECT chunk_schema,chunk_name,range_start_integer,range_end_integer
            FROM timescaledb_information.chunks WHERE hypertable_schema='morlet_metrics'
            AND hypertable_name='metrics' AND range_end_integer<=%s ORDER BY range_start_integer''',(cutoff,))
        plan={'schema_version':SCHEMA_VERSION,'database':self.one('SELECT current_database() AS db')['db'],
              'cutoff_ns':cutoff,'previous_floor_ns':floor,'chunks':chunks,
              'effect':'expire derived metric rows only; preserve ledger, aggregates and all waveform samples'}
        return {**plan,'sha256':digest(plan)}

    def apply_retention(self,plan):
        with self.maintenance():
            fresh=self.retention_plan(plan['cutoff_ns'])
            if fresh!=plan:
                raise ValueError('retention plan changed; preview it again')
            for source in self.all('SELECT source_id FROM morlet_metrics.sources'):
                if not self.status(source['source_id'])['complete']:
                    raise ValueError('finish pending complete metric windows before retention')
            if plan['chunks']:
                start=max(plan['previous_floor_ns'],plan['chunks'][0]['range_start_integer'])
                self._refresh(start,plan['cutoff_ns'])
            # If interrupted after refresh, retrying this same plan is safe. Floor,
            # chunk expiry and audit commit atomically, never in the opposite order.
            with self.conn.transaction():
                self.conn.execute("SELECT public.drop_chunks('morlet_metrics.metrics',older_than=>%s::bigint)",(plan['cutoff_ns'],))
                self.conn.execute('UPDATE morlet_metrics.config SET pruned_before_ns=%s',(plan['cutoff_ns'],))
                self.conn.execute('INSERT INTO morlet_metrics.retention_log(plan) VALUES (%s)',(Jsonb(plan),))
        return plan

    def resolve(self,evidence_id):
        if self.conn.info.transaction_status == TransactionStatus.IDLE:
            with self.conn.transaction():
                self.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return self._resolve(evidence_id)
        if self.one('SHOW transaction_isolation')['transaction_isolation'] not in ('repeatable read','serializable'):
            raise ValueError('evidence resolution requires an idle connection or repeatable-read snapshot')
        return self._resolve(evidence_id)

    def _resolve(self,evidence_id):
        evidence=self.one('SELECT * FROM morlet_metrics.evidence WHERE evidence_id=%s',(evidence_id,))
        if not evidence:
            return {'state':'unavailable'}
        row=self.one('SELECT * FROM morlet_metrics.sources WHERE source_id=%s',(evidence['source_id'],))
        try:
            # Savepoint leaves the caller's transaction usable on missing evidence.
            with self.conn.transaction():
                self._registered(row['source_id'])
                raw=bytes(self.one('SELECT public.morlet_read_interval_raw(%s::regclass,%s,%s,%s) AS raw',
                    (row['collection'],row['recording_id'],evidence['start_sample'],evidence['end_sample']))['raw'])
        except psycopg.errors.InsufficientPrivilege:
            return {'state':'unavailable'}
        except ValueError as exc:
            return {'state':'source_changed' if 'changed' in str(exc) else 'unavailable'}
        except psycopg.errors.RaiseException:
            return {'state':'raw_unavailable'}
        if hashlib.sha256(raw).hexdigest()!=evidence['source_sha256']:
            return {'state':'source_changed'}
        return {'state':'raw_available','samples':raw,'collection':row['collection'],
                'recording_id':row['recording_id'],'start_sample':evidence['start_sample'],
                'end_sample':evidence['end_sample'],'time_ns':evidence['time_ns']}

    def viewer_url(self,evidence_id,base='http://127.0.0.1:8765'):
        evidence=self.resolve(evidence_id)
        if evidence['state']!='raw_available':
            return None
        return base.rstrip('/')+'/?'+urlencode({'collection':evidence['collection'],
            'recording':evidence['recording_id'],'start':evidence['start_sample'],'end':evidence['end_sample']})
