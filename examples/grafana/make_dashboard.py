#!/usr/bin/env python3
"""Writes dashboards/morletdb.json, the minimal MorletDB dashboard. Edit the queries here, then run it."""
import json
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "morletdb"}
C = "${collection:raw}"
START = "COALESCE(metadata->>'start', metadata->>'starttime', metadata->>'start_time')::timestamptz"


def target(sql, fmt="table"):
    return [{"refId": "A", "datasource": DS, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": sql}]


def stat(pid, title, sql, x, desc):
    return {"id": pid, "type": "stat", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"h": 4, "w": 6, "x": x, "y": 0}, "targets": target(sql),
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "none", "graphMode": "none", "textMode": "value"},
            "fieldConfig": {"defaults": {"unit": "none", "decimals": 0}, "overrides": []}}


panels = [
    stat(1, "Recordings", f"SELECT count(*) AS recordings FROM {C}", 0, "Recordings in the collection."),
    stat(2, "Samples", f"SELECT coalesce(sum((checkpoint->>'end')::bigint), 0) AS samples FROM {C}", 6,
         "Samples stored, all recordings."),
    stat(3, "Search windows", f"SELECT count(*) AS windows FROM {C}_envelopes", 12,
         "Stored envelope windows available to similarity search."),
    stat(4, "Recordings not yet searchable",
         f"SELECT count(*) AS waiting FROM {C} r WHERE NOT EXISTS "
         f"(SELECT 1 FROM {C}_envelopes e WHERE e.recording_id = r.recording_id)", 18,
         "Recordings without any envelope window yet (search readiness)."),
    {"id": 5, "type": "timeseries", "title": "Samples recorded over time", "datasource": DS,
     "description": "Samples per time bucket (Grafana's interval for the range), by recording start time in metadata "
                    "(start, starttime or start_time); empty buckets are gaps.", "maxDataPoints": 500,
     "gridPos": {"h": 8, "w": 24, "x": 0, "y": 4},
     "targets": target(f"SELECT $__timeGroupAlias(t0, $__interval), sum(n) AS samples FROM "
                       f"(SELECT {START} AS t0, (checkpoint->>'end')::bigint AS n FROM {C}) r "
                       f"WHERE $__timeFilter(t0) GROUP BY 1 ORDER BY 1", "time_series"),
     "fieldConfig": {"defaults": {"unit": "short", "custom": {"drawStyle": "bars", "fillOpacity": 60,
                                                             "lineWidth": 1}}, "overrides": []},
     "options": {"legend": {"showLegend": False}, "tooltip": {"mode": "single"}}},
    {"id": 6, "type": "table", "title": "Recordings in the time range (up to 500; click one to open the viewer)",
     "datasource": DS, "gridPos": {"h": 14, "w": 24, "x": 0, "y": 12},
     "description": "Bounded to 500 rows. Search windows counted per recording.",
     "targets": target(
         f"SELECT recording_id, start, duration_s, rate_hz, channel, unit, samples, "
         f"(SELECT count(*) FROM {C}_envelopes e WHERE e.recording_id = r.recording_id) AS search_windows, metadata "
         f"FROM (SELECT recording_id, {START} AS start, "
         f"round(((checkpoint->>'end')::bigint / sample_rate_hz)::numeric, 3) AS duration_s, sample_rate_hz AS rate_hz, "
         f"channel, unit, (checkpoint->>'end')::bigint AS samples, metadata::text AS metadata FROM {C}) r "
         f"WHERE $__timeFilter(start) ORDER BY start LIMIT 500"),
     "fieldConfig": {"defaults": {}, "overrides": [
         {"matcher": {"id": "byName", "options": "recording_id"},
          "properties": [{"id": "links", "value": [{
              "title": "Open in the MorletDB viewer",
              "url": "${viewer}/?collection=${collection}&recording=${__value.raw}", "targetBlank": True}]}]},
         {"matcher": {"id": "byName", "options": "start"}, "properties": [{"id": "unit", "value": "dateTimeAsIso"}]}]},
     "options": {"showHeader": True, "cellHeight": "sm"}},
]

dashboard = {
    "uid": "morletdb-overview", "title": "MorletDB recordings", "tags": ["morletdb"], "editable": False,
    "schemaVersion": 39, "version": 1, "refresh": "", "timezone": "utc",
    "time": {"from": "now-30y", "to": "now"},
    "templating": {"list": [
        {"name": "collection", "label": "Collection", "type": "query", "datasource": DS, "refresh": 1,
         "query": "SELECT format('%I.%I', c.table_schema, c.table_name) FROM information_schema.columns c "
                  "WHERE c.column_name = 'checkpoint' "
                  "AND to_regclass(format('%I.%I', c.table_schema, c.table_name || '_envelopes')) IS NOT NULL "
                  "AND has_table_privilege(format('%I.%I', c.table_schema, c.table_name), 'SELECT') ORDER BY 1",
         "includeAll": False, "multi": False},
        {"name": "viewer", "label": "Viewer URL", "type": "textbox", "query": "http://127.0.0.1:8765",
         "current": {"text": "http://127.0.0.1:8765", "value": "http://127.0.0.1:8765"}},
    ]},
    "annotations": {"list": []},
    "panels": panels,
}
out = Path(__file__).resolve().parent / "dashboards" / "morletdb.json"
out.write_text(json.dumps(dashboard, indent=1) + "\n")
print(out)
