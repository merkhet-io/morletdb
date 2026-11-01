#!/usr/bin/env python3
"""E4 load (PROTOCOL.md): an R2-identical gallery for days 1..LAST in a new database, without R2's study side
tables. Recording ids, metadata, chunking and envelopes follow evaluation/retrieval_scaling/load.py; then
product probe search is enabled as evaluation/consolidated/prepare_db.sh does.

  .venv-seismic/bin/python -I evaluation/scaling_crossover/load_e4.py morlet_e4_30 30 out.json data/cx-scale data/cx-scale-e4
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb as Json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "retrieval_scaling"))
from load import COLL, CHUNK, WINDOW, STRIDE, tiers  # noqa: E402  R2's constants and metadata


def recordings(roots, last):
    for root in roots:
        for m in json.loads((root / "manifest.json").read_text()):
            if m["day"] > last:
                continue
            for i, seg in enumerate(m["segments"]):
                if seg["samples"] < WINDOW:
                    continue
                yield (f"{m['station']}.d{m['day']:02d}.{i}", m["station"], m["day"], seg, root / seg["file"])


def main(db, last, out, *roots):
    last, roots, res = int(last), [Path(r) for r in roots], {"database": db, "last_day": int(last)}
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as conn:
        conn.execute("CREATE EXTENSION pg_morlet")
        conn.execute("CREATE EXTENSION vector")
        conn.execute("SELECT morlet_create_stream_collection('public', 'r2')")
        conn.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
        t, n_rec, n_samples = time.perf_counter(), 0, 0
        for rid, st, day, seg, path in recordings(roots, last):
            x = np.load(path).astype(np.float64)
            with conn.transaction():
                conn.execute("SELECT morlet_start_recording(%s, %s, %s, 'HHZ', 'counts', %s, 512, 256, 'zscore', 'none', 1::float8)",
                             (COLL, rid, seg["rate"], Json({"station": st, "day": day, "start": seg["start"], "tiers": tiers(st, day)})))
                for s in range(0, len(x), CHUNK):
                    conn.execute("SELECT morlet_append_chunk(%s, %s, %s, %s)", (COLL, rid, s, x[s:s + CHUNK].tolist()))
            n_rec, n_samples = n_rec + 1, n_samples + len(x)
        res.update(append_s=time.perf_counter() - t, recordings=n_rec, samples=n_samples)
        print("appended", res, flush=True)
        t = time.perf_counter()
        for (rid,) in conn.execute(f"SELECT recording_id FROM {COLL} ORDER BY recording_id").fetchall():
            while conn.execute("SELECT morlet_build_envelopes(%s, %s, %s, %s, 100000)", (COLL, rid, WINDOW, STRIDE)).fetchone()[0]:
                pass
        res["envelope_build_s"] = time.perf_counter() - t
        t = time.perf_counter()
        conn.execute("SET maintenance_work_mem = '4GB'")
        conn.execute("SELECT morlet_enable_envelope_probes(%s, %s)", (COLL, WINDOW))
        res["enable_probes_s"] = time.perf_counter() - t
        for rel in ("public.r2_chunks", "public.r2_envelopes", "public.r2_probes_4096"):
            conn.execute(f"VACUUM ANALYZE {rel}")
        q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
        res["windows"] = q("SELECT count(*) FROM public.r2_envelopes WHERE window_size = %s", WINDOW)
        res["windows_days_1_14"] = q("SELECT count(*) FROM public.r2_envelopes e JOIN public.r2 r USING (recording_id)"
                                     " WHERE e.window_size = %s AND (r.metadata->>'day')::int <= 14", WINDOW)
        res["status"] = list(conn.execute("SELECT window_size, envelopes, probe_rows, probe_backlog FROM morlet_collection_status(%s)",
                                          (COLL,)).fetchone())
        res["bytes"] = {k: q("SELECT pg_total_relation_size(%s::regclass)", rel) for k, rel in
                        (("raw_chunks", "public.r2_chunks"), ("envelopes", "public.r2_envelopes"), ("probe_table", "public.r2_probes_4096"))}
        res["bytes"]["probe_index"] = q("SELECT pg_relation_size('public.r2_probes_4096_v_idx'::regclass)")
        res["database_bytes"] = q("SELECT pg_database_size(current_database())")
    Path(out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
