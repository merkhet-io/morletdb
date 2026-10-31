#!/usr/bin/env python3
"""Re-encode every stored chunk of a collection with the installed pg_morlet and compare.

For each chunk: decode the stored bytes, re-encode them with the recording's sample_quantum
(morlet_encode_chunk), check that the new encoding decodes bitwise to the same samples, and
record its size and kind. Encoding time is measured in the server.

  .venv-seismic/bin/python -I evaluation/compression/offset_grid/reencode.py OUT.json DB:COLLECTION [DB:COLLECTION ...]
"""
import json
import sys
import time

import psycopg

SQL = """
WITH c AS (
  SELECT c.recording_id, c.start_sample, c.sample_count, c.encoded, r.sample_quantum
  FROM {coll}_chunks c JOIN {coll} r USING (recording_id)),
e AS (
  SELECT c.*, morlet_encode_chunk(morlet_decode_chunk(c.encoded), c.sample_quantum) AS re FROM c)
SELECT count(*), sum(sample_count), sum(octet_length(encoded)), sum(octet_length(re)),
  count(*) FILTER (WHERE morlet_encoded_chunk_kind(re) = 'grid'),
  count(*) FILTER (WHERE morlet_encoded_chunk_kind(re) = 'float'),
  count(*) FILTER (WHERE morlet_encoded_chunk_kind(re) = 'raw'),
  count(*) FILTER (WHERE get_byte(re, 0) = 3),
  count(*) FILTER (WHERE (SELECT array_agg(float8send(v)) FROM unnest(morlet_decode_chunk(re)) v)
                      IS DISTINCT FROM (SELECT array_agg(float8send(v)) FROM unnest(morlet_decode_chunk(encoded)) v)),
  count(*) FILTER (WHERE re = encoded)
FROM e"""
TIME = "SELECT sum(octet_length(morlet_encode_chunk(morlet_decode_chunk(c.encoded), r.sample_quantum))) FROM {coll}_chunks c JOIN {coll} r USING (recording_id)"
DECODE = "SELECT sum(cardinality(morlet_decode_chunk(c.encoded))) FROM {coll}_chunks c"


def main():
    out, targets = sys.argv[1], sys.argv[2:]
    res = {}
    for t in targets:
        db, coll = t.split(":")
        with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
            c.execute("SET max_parallel_workers_per_gather = 0")
            row = c.execute(SQL.format(coll=coll)).fetchone()
            t0 = time.perf_counter(); c.execute(DECODE.format(coll=coll)).fetchone(); dec = time.perf_counter() - t0
            t0 = time.perf_counter(); c.execute(TIME.format(coll=coll)).fetchone(); both = time.perf_counter() - t0
        chunks, samples, stored, re, grid, flt, raw, v3, mismatched, same = row
        res[t] = {"chunks": chunks, "samples": int(samples), "stored_bytes": int(stored), "reencoded_bytes": int(re),
                  "stored_bits_per_sample": 8 * int(stored) / int(samples), "reencoded_bits_per_sample": 8 * int(re) / int(samples),
                  "kinds": {"grid": grid, "float": flt, "raw": raw}, "version3_chunks": v3,
                  "bitwise_mismatched_chunks": mismatched, "byte_identical_chunks": same,
                  "encode_msamples_per_s": int(samples) / max(both - dec, 1e-9) / 1e6}
        print(t, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[t].items()}, flush=True)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
