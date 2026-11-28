#!/usr/bin/env python3
"""Generate the fixed-schema, read-only Timescale integration dashboard."""
import json
from pathlib import Path

DS={'type':'grafana-postgresql-datasource','uid':'morlet-timescale'}

def panel(i,title,query,kind='table'):
    return {'id':i,'title':title,'type':kind,'datasource':DS,
            'gridPos':{'x':0,'y':(i-1)*8,'w':24,'h':8},
            'targets':[{'refId':'A','datasource':DS,'format':'table','editorMode':'code',
                        'rawQuery':True,'rawSql':query}],
            'options':{'showHeader':True},'fieldConfig':{'defaults':{},'overrides':[]}}

panels=[
    panel(1,'Irregular telemetry (temperature, exact energy counter, state)',
          "SELECT to_timestamp(time_ns/1000000000.0) AS time,asset,temperature,energy_counter::text,state FROM morlet_metrics.telemetry ORDER BY time_ns DESC LIMIT 500"),
    panel(2,'Minute waveform summaries — materialized results',
          "SELECT to_timestamp(bucket_ns/1000000000.0) AS time,s.asset,s.recording_id,m.samples,m.minimum,m.maximum,m.mean,m.rms FROM morlet_metrics.minute m JOIN morlet_metrics.sources s USING(source_id) ORDER BY bucket_ns DESC LIMIT 500"),
    panel(3,'Metric intervals — open original waveform',
          """SELECT e.evidence_id,s.asset,s.recording_id,e.start_sample,e.end_sample,
          e.time_ns::text AS exact_time_ns,m.rms,
          CASE WHEN m.evidence_id IS NULL THEN 'metric expired' ELSE 'metric retained' END AS metric_state,
          CASE WHEN r.recording_id IS NULL THEN 'source unavailable'
            WHEN (SELECT coalesce(sum(least(c.start_sample+c.sample_count,e.end_sample)-greatest(c.start_sample,e.start_sample)),0)
                  FROM signals_chunks c WHERE c.recording_id=s.recording_id
                    AND c.start_sample<e.end_sample AND c.start_sample+c.sample_count>e.start_sample)=e.end_sample-e.start_sample
            THEN 'samples present; verify on read' ELSE 'raw samples unavailable' END AS source_state,
          l.url AS waveform_url
          FROM morlet_metrics.evidence e JOIN morlet_metrics.sources s USING(source_id)
          LEFT JOIN signals r USING(recording_id)
          LEFT JOIN morlet_metrics.metrics m ON m.time_ns=e.time_ns AND m.evidence_id=e.evidence_id
          LEFT JOIN morlet_metrics.viewer_links l ON l.evidence_id=e.evidence_id
          ORDER BY e.time_ns DESC,e.evidence_id LIMIT 500"""),
    panel(4,'Publication progress — pending is not healthy',
          """SELECT s.asset,s.recording_id,(r.checkpoint->>'end')::bigint AS received_end,s.processed_end,
          (r.checkpoint->>'end')::bigint/s.window_samples*s.window_samples-s.processed_end AS pending_complete_samples,
          (r.checkpoint->>'end')::bigint%s.window_samples AS partial_tail_samples
          FROM morlet_metrics.sources s LEFT JOIN signals r USING(recording_id) ORDER BY s.source_id LIMIT 500"""),
    panel(5,'Aggregate refresh — source coverage is separate',
          "SELECT start_ns::text,end_ns::text,finished_at,extract(epoch FROM clock_timestamp()-finished_at) AS seconds_since_refresh FROM morlet_metrics.refreshes ORDER BY id DESC LIMIT 20"),
]
panels[2]['fieldConfig']['overrides']=[{'matcher':{'id':'byName','options':'waveform_url'},
    # The URL is already encoded by urllib.urlencode. Explicit :raw prevents
    # Grafana from encoding '%' again (capture%2FB becoming capture%252FB).
    'properties':[{'id':'links','value':[{'title':'Open exact interval','url':'${__value.raw:raw}','targetBlank':True}]}]}]
dashboard={'uid':'morlet-timescale','title':'MorletDB + TimescaleDB investigation','schemaVersion':39,
           'version':1,'editable':False,'timezone':'utc','refresh':'10s',
           'time':{'from':'now-1h','to':'now'},'panels':panels,'annotations':{'list':[]},
           'tags':['morletdb','timescaledb'],'templating':{'list':[]}}
if __name__=='__main__':
    (Path(__file__).parent/'dashboards/morlet-timescale.json').write_text(json.dumps(dashboard,indent=2)+'\n')
