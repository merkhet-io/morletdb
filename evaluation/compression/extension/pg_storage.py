"""Usage: pg_storage.py CHUNK_DIR N_CHUNKS [CHUNK_SIZE] (needs psycopg and a server with pg_morlet; DSN in $MORLET_DSN).

Ingest the same held-out chunks into a legacy float8[] collection and an encoded collection; compare sizes."""
import json, os, struct, sys, time
import numpy as np, psycopg
D = sys.argv[1]; DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=codec_debug")
N_CHUNKS = int(sys.argv[2])  # chunks per dataset
CH = int(sys.argv[3]) if len(sys.argv) > 3 else 4096
SETS = {"cx": (100.0, "morlet-energy-lowrate-v1", 2048, 512), "ims": (20000.0, "morlet-energy-v1", 512, 256),
        "paderborn-counts": (64000.0, "morlet-energy-v1", 512, 256), "paderborn-physical": (64000.0, "morlet-energy-v1", 512, 256)}
LEGACY = """CREATE TABLE {t} (recording_id text PRIMARY KEY, sample_rate_hz float8 NOT NULL, channel text NOT NULL, unit text NOT NULL,
  metadata jsonb NOT NULL, checkpoint jsonb NOT NULL);
CREATE TABLE {t}_chunks (recording_id text NOT NULL REFERENCES {t} ON DELETE CASCADE, start_sample bigint NOT NULL,
  samples float8[] NOT NULL CHECK(array_ndims(samples)=1 AND array_lower(samples,1)=1 AND public.morlet_valid_chunk(samples)),
  PRIMARY KEY(recording_id,start_sample));
CREATE TABLE {t}_segments (recording_id text NOT NULL REFERENCES {t} ON DELETE CASCADE, start_sample bigint NOT NULL,
  features float8[] NOT NULL, PRIMARY KEY(recording_id,start_sample));"""

def chunks(name):
    b = open(f"{D}/{name}-{CH}.bin", "rb").read(); pos = 0; out = []
    while pos < len(b) and len(out) < N_CHUNKS:
        n, q = struct.unpack_from("<Id", b, pos); pos += 12
        out.append((np.frombuffer(b, "<f8", n, pos).tolist(), q)); pos += 8 * n
    return out

res = {}
with psycopg.connect(DSN, autocommit=True) as conn:
    for name, (rate, extractor, window, stride) in SETS.items():
        data = chunks(name); q = data[0][1]
        r = {"chunks": len(data), "samples": sum(len(c) for c, _ in data), "quantum": q}
        for layout in ("legacy", "encoded"):
            t = f"{layout}_{name.replace('-', '_')}"
            conn.execute(f"DROP TABLE IF EXISTS {t}_chunks, {t}_segments, {t}")
            if layout == "legacy":
                conn.execute(LEGACY.format(t=t))
                conn.execute("SELECT morlet_start_recording(%s,'r',%s,'x','u','{}',%s,%s,'zscore',%s)", (t, rate, window, stride, extractor))
            else:
                conn.execute("SELECT morlet_create_stream_collection('public',%s)", (t,))
                conn.execute("SELECT morlet_start_recording(%s,'r',%s,'x','u','{}',%s,%s,'zscore',%s,%s)", (t, rate, window, stride, extractor, q))
            start = 0; t0 = time.perf_counter()
            for i in range(0, len(data), 16):
                batch = data[i:i + 16]
                rows = []
                for c, _ in batch:
                    rows.append((start, c)); start += len(c)
                with conn.transaction():
                    for s0, c in rows:
                        conn.execute("SELECT morlet_append_chunk(%s,'r',%s,%s::float8[])", (t, s0, c))
            secs = time.perf_counter() - t0
            conn.execute(f"VACUUM ANALYZE {t}_chunks")
            size = conn.execute("SELECT pg_total_relation_size(%s::regclass)", (f"{t}_chunks",)).fetchone()[0]
            st = conn.execute("SELECT chunks,samples,grid_chunks,float_chunks,raw_chunks,encoded_bytes,stored_value_bits_per_sample FROM morlet_recording_storage(%s,'r')", (t,)).fetchone()
            # Read back a random interval and compare bitwise with the source.
            flat = np.concatenate([np.asarray(c) for c, _ in data])
            for a in (0, 4095, len(flat) // 2 + 17, len(flat) - 20000):
                got = conn.execute("SELECT samples FROM morlet_read_interval(%s,'r',%s,%s)", (t, a, a + 20000)).fetchone()[0]
                assert np.array_equal(np.asarray(got).view("<u8"), flat[a:a + 20000].view("<u8")), (t, a)
            r[layout] = {"table_bytes": size, "table_bits_per_sample": 8 * size / r["samples"], "value_bits_per_sample": st[6],
                         "grid_chunks": st[2], "float_chunks": st[3], "raw_chunks": st[4], "append_s": secs, "samples_per_s": r["samples"] / secs}
            print(name, layout, json.dumps(r[layout]), flush=True)
        r["table_reduction"] = r["legacy"]["table_bytes"] / r["encoded"]["table_bytes"]
        res[name] = r
json.dump(res, open(f"{D}/pg_storage-{CH}.json", "w"), indent=1)
print(json.dumps({k: round(v["table_reduction"], 2) for k, v in res.items()}))
