#!/usr/bin/env python3
"""Pruning headroom: split envelope-search latency into fixed and per-candidate parts.

Runs inside the database (one PL/pgSQL timing loop per part, 100 queries each) on the
R1b CX 2022 collection `envcheck_2022` (1,826 windows of 4096 samples; loaded by
evaluation/materialized/compact/sql_check.py).

  .venv-seismic/bin/python -I evaluation/pruning/headroom.py DBNAME evaluation/pruning/results/headroom.json
"""
import json
import sys

import psycopg

BENCH = """
CREATE OR REPLACE FUNCTION pg_temp.b(q text, n int, station boolean) RETURNS float8 LANGUAGE plpgsql AS $$
DECLARE t0 timestamptz; i int; ids text[]; f jsonb[]; e float8[]; BEGIN
  SELECT array_agg(recording_id ORDER BY recording_id),
         array_agg(CASE WHEN station THEN jsonb_build_object('station',metadata->>'station') ELSE '{}'::jsonb END ORDER BY recording_id)
    INTO ids,f FROM (SELECT * FROM envcheck_2022 ORDER BY 1 LIMIT n) x;
  e:=(SELECT morlet_envelope(samples) FROM morlet_read_interval('envcheck_2022',ids[1],0,4096));
  t0:=clock_timestamp(); FOR i IN 1..n LOOP EXECUTE q USING ids[i], f[i], e; END LOOP;
  RETURN extract(epoch FROM clock_timestamp()-t0)*1000/n; END $$"""
ELIGIBLE = ("FROM envcheck_2022_envelopes e JOIN envcheck_2022 r ON r.recording_id=e.recording_id "
            "WHERE e.window_size=4096 AND r.sample_rate_hz=100 AND r.channel='HHZ' AND r.unit='counts' AND r.metadata @> $2")
PARTS = {
    "full_search": "SELECT count(*) FROM morlet_search_envelope_interval('envcheck_2022',$1,0,4096,metadata_filter=>$2) WHERE $3 IS NOT NULL",
    "query_read_and_envelope": "SELECT cardinality(morlet_envelope(samples)) FROM morlet_read_interval('envcheck_2022',$1,0,4096) WHERE $2 IS NOT NULL AND $3 IS NOT NULL",
    "candidate_count": f"SELECT count(*) {ELIGIBLE} AND $1 IS NOT NULL AND $3 IS NOT NULL",
    "scan_sort_without_scoring": f"SELECT e.recording_id,e.start_sample,length(e.envelope) d,r.metadata {ELIGIBLE} AND $1 IS NOT NULL AND $3 IS NOT NULL ORDER BY d,e.recording_id,e.start_sample",
    "scan_sort_with_scoring": f"SELECT e.recording_id,e.start_sample,morlet_envelope_distance($3,e.envelope,30) d,r.metadata {ELIGIBLE} AND $1 IS NOT NULL ORDER BY d,e.recording_id,e.start_sample",
}


def main():
    db, out = sys.argv[1], sys.argv[2]
    res = {}
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
        c.execute(BENCH)
        for name, station in (("same_station", True), ("unfiltered", False)):
            # Scored candidates per query: the eligible windows minus the excluded source.
            cands = c.execute("SELECT avg(n - 1)::float8 FROM (SELECT count(*) n FROM envcheck_2022 GROUP BY "
                              + ("metadata->>'station'" if station else "'all'::text") + ") x").fetchone()[0]
            ms = {k: c.execute("SELECT pg_temp.b(%s, 100, %s)", (q, station)).fetchone()[0] for k, q in PARTS.items()}
            scoring = ms["scan_sort_with_scoring"] - ms["scan_sort_without_scoring"]
            fixed = ms["full_search"] - scoring
            res[name] = {"candidates_per_query": cands, "ms": ms, "scoring_ms": scoring,
                         "scoring_us_per_candidate": 1e3 * scoring / cands, "non_scoring_ms": fixed,
                         "ceiling_perfect_pruning": ms["full_search"] / fixed}
    json.dump(res, open(out, "w"), indent=1)
    for k, v in res.items():
        print(k, {kk: round(vv, 2) for kk, vv in v.items() if isinstance(vv, float)})


if __name__ == "__main__":
    main()
