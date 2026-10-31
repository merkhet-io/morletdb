#!/usr/bin/env python3
"""R2 load (PROTOCOL.md): recordings per station-day-segment, raw append, envelopes (4096/2048), the halfvec
side table and its HNSW index. Timings and sizes go to OUT/load.json.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/load.py data/cx-scale evaluation/retrieval_scaling/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb as Json

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_r2")
COLL, CHUNK, WINDOW, STRIDE = "public.r2", 4096, 4096, 2048
TIER_DAYS = {1: 1, 2: 4, 3: 14}            # tier 0 is PB01 day 1


def tiers(station, day):
    t = [k for k, last in TIER_DAYS.items() if day <= last]
    return ([0] if station == "PB01" and day == 1 else []) + t


def recordings(root):
    for m in json.loads((root / "manifest.json").read_text()):
        for i, seg in enumerate(m["segments"]):
            if seg["samples"] < WINDOW:
                continue
            yield (f"{m['station']}.d{m['day']:02d}.{i}", m["station"], m["day"], seg, root / seg["file"])


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    # --resume-side-table JSON: raw and envelopes are complete (their timings given); rebuild side table and index
    resume = json.loads(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--resume-side-table" else None
    res = dict(resume or {})
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        if conn.execute("SELECT to_regclass(%s)", (COLL,)).fetchone()[0] is None:
            conn.execute("SELECT morlet_create_stream_collection('public', 'r2')")
            conn.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
        if resume:
            conn.execute("DROP TABLE IF EXISTS public.r2_hv")
        t = time.perf_counter()
        n_samples = n_rec = 0
        for rid, st, day, seg, path in ([] if resume else recordings(root)):
            x = np.load(path).astype(np.float64)
            with conn.transaction():
                if conn.execute(f"SELECT 1 FROM {COLL} WHERE recording_id = %s", (rid,)).fetchone() is None:
                    conn.execute("SELECT morlet_start_recording(%s, %s, %s, 'HHZ', 'counts', %s, 512, 256, 'zscore', 'none', 1::float8)",
                                 (COLL, rid, seg["rate"], Json({"station": st, "day": day, "start": seg["start"],
                                                                "tiers": tiers(st, day)})))
                for s in range(0, len(x), CHUNK):   # identical retries are no-ops
                    conn.execute("SELECT morlet_append_chunk(%s, %s, %s, %s)", (COLL, rid, s, x[s:s + CHUNK].tolist()))
            n_samples += len(x)
            n_rec += 1
        if not resume:
            res["append_s"], res["recordings"], res["samples"] = time.perf_counter() - t, n_rec, n_samples
        print("appended", res, flush=True)

        t = time.perf_counter()
        for (rid,) in conn.execute(f"SELECT recording_id FROM {COLL} ORDER BY recording_id").fetchall():
            while conn.execute("SELECT morlet_build_envelopes(%s, %s, %s, %s, 100000)", (COLL, rid, WINDOW, STRIDE)).fetchone()[0]:
                pass
        if not resume:
            res["envelope_build_s"] = time.perf_counter() - t
        res["windows"] = conn.execute("SELECT count(*) FROM public.r2_envelopes").fetchone()[0]
        print("envelopes", res["windows"], round(res["envelope_build_s"], 1), flush=True)

        t = time.perf_counter()
        conn.execute("""CREATE TABLE IF NOT EXISTS public.r2_hv (recording_id text NOT NULL, start_sample bigint NOT NULL,
                        tiers int[] NOT NULL, v halfvec(410) NOT NULL, PRIMARY KEY (recording_id, start_sample))""")
        conn.execute("""INSERT INTO public.r2_hv
                        SELECT e.recording_id, e.start_sample,
                               ARRAY(SELECT jsonb_array_elements_text(r.metadata->'tiers')::int),
                               morlet_envelope_decode(e.envelope)::halfvec(410)
                        FROM public.r2_envelopes e JOIN public.r2 r USING (recording_id)
                        WHERE e.window_size = %s ON CONFLICT DO NOTHING""", (WINDOW,))
        res["side_table_s"] = time.perf_counter() - t
        t = time.perf_counter()
        conn.execute("SET maintenance_work_mem = '2GB'")
        conn.execute("CREATE INDEX IF NOT EXISTS r2_hv_hnsw ON public.r2_hv USING hnsw (v halfvec_cosine_ops) "
                     "WITH (m = 16, ef_construction = 64)")
        res["hnsw_build_s"] = time.perf_counter() - t
        conn.execute("VACUUM ANALYZE public.r2_chunks")
        conn.execute("VACUUM ANALYZE public.r2_envelopes")
        conn.execute("VACUUM ANALYZE public.r2_hv")
        size = lambda rel: conn.execute("SELECT pg_total_relation_size(%s::regclass)", (rel,)).fetchone()[0]
        res["bytes"] = {"raw_chunks": size("public.r2_chunks"), "envelopes": size("public.r2_envelopes"),
                        "halfvec_table": conn.execute("SELECT pg_table_size('public.r2_hv')").fetchone()[0],
                        "hnsw_index": conn.execute("SELECT pg_relation_size('public.r2_hv_hnsw')").fetchone()[0]}
        res["windows_per_tier"] = {t: conn.execute("SELECT count(*) FROM public.r2_hv WHERE tiers @> ARRAY[%s]::int[]", (t,)).fetchone()[0]
                                   for t in range(4)}
    json.dump(res, open(out / "load.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
