#!/usr/bin/env python3
"""#280: storage of the B3 benchmark set under pglz and lz4 TOAST compression.

  python evaluation/toast_compression/measure.py --dsn "..." --arm pglz|lz4|default --out results/<name>.json

Loads the 124 Paderborn acquisitions of B3 (evaluation/tsdb_b3/workload.py; every channel, B3's storage hints)
into a fresh stream collection with envelopes (window 4096, hop 2048), with the bulk columns at the given TOAST
compression: pglz (the pin, the shipped behaviour), lz4 (what PostgreSQL 19's default would give without the pin),
or default (no ALTER at all, to see what the server picks). It also stores the same samples in the legacy layout
(float8[] chunks of 32768) to measure the case where TOAST compression decides the size. Every recording is read
back and compared bitwise. Storage only: the builds are assertion-enabled pgrx test builds, so no timings.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tsdb_b3"))
import workload as wl  # noqa: E402

DATA = Path(os.environ.get("B3_DATA", wl.DATA))
wl.DATA = DATA
CHUNK = 32768
C = "tc280"


def acquisitions():
    files = sorted(DATA.glob("*/*.mat"))
    if len(files) != 2480:
        raise SystemExit(f"expected 2,480 Paderborn files in {DATA}, found {len(files)}")
    return files[::20]


def drop(c):
    names = [r[0] for r in c.execute("SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname='public' "
                                     "AND (tablename=%s OR tablename LIKE %s)", (C, C + "\\_%"))]
    if names:
        c.execute(f"DROP TABLE {', '.join(names)} CASCADE")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--arm", choices=["pglz", "lz4", "default"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    files = acquisitions()
    with psycopg.connect(a.dsn, autocommit=True) as c:
        res = {"arm": a.arm, "server": c.execute("SELECT version()").fetchone()[0],
               "default_toast_compression": c.execute("SHOW default_toast_compression").fetchone()[0],
               "extension": c.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]}
        drop(c)
        c.execute("SELECT morlet_create_stream_collection('public', %s)", (C,))
        c.execute("SELECT morlet_enable_envelopes(%s::regclass)", (C,))
        c.execute(f"CREATE TABLE {C}_legacy (recording_id text, start_sample bigint, samples float8[] COMPRESSION pglz,"
                  " PRIMARY KEY (recording_id, start_sample))")
        bulk = [(f"{C}_chunks", "encoded"), (f"{C}_envelopes", "envelope"), (f"{C}_legacy", "samples")]
        if a.arm != "pglz":
            for t, col in bulk:
                c.execute(f"ALTER TABLE {t} ALTER COLUMN {col} SET COMPRESSION {a.arm}")
        res["attcompression"] = {f"{t}.{col}": c.execute(
            "SELECT attcompression::text FROM pg_attribute WHERE attrelid=%s::regclass AND attname=%s",
            (t, col)).fetchone()[0] for t, col in bulk}
        n = 0
        digest = hashlib.sha256()
        for path in files:
            data = wl.load(path)
            for ch in sorted(data):
                x = data[ch]
                rid = f"{path.stem}/{ch}"
                g = next(g for g, s in wl.GROUPS.items() if ch in s["channels"])
                c.execute("SELECT morlet_start_recording(%s::regclass, %s, %s, %s, %s, '{}'::jsonb, 512, 256, "
                          "'zscore', 'none', %s)", (C, rid, wl.GROUPS[g]["rate"], ch, wl.UNITS[ch],
                                                     wl.QUANTUM.get(ch, 1.0)))
                with c.transaction():
                    for s in range(0, len(x), CHUNK):
                        part = x[s:s + CHUNK].tolist()
                        c.execute("SELECT morlet_append_chunk(%s::regclass, %s, %s, %s)", (C, rid, s, part))
                        c.execute(f"INSERT INTO {C}_legacy VALUES (%s, %s, %s)", (rid, s, part))
                if len(x) >= 4096:
                    c.execute("SELECT morlet_build_envelopes(%s::regclass, %s, 4096, 2048)", (C, rid))
                back = [v for s in range(0, len(x), CHUNK) for v in c.execute(
                    "SELECT samples FROM morlet_read_interval(%s::regclass, %s, %s, %s)",
                    (C, rid, s, min(s + CHUNK, len(x)))).fetchone()[0]]
                if np.asarray(back, dtype="<f8").tobytes() != x.tobytes():
                    raise SystemExit(f"{rid}: read-back is not bitwise equal")
                digest.update(x.tobytes())
                n += len(x)
        c.execute(f"VACUUM ANALYZE {C}_chunks, {C}_envelopes, {C}_legacy")
        res["samples"] = n
        res["samples_sha256"] = digest.hexdigest()
        q = lambda sql: c.execute(sql).fetchone()  # noqa: E731
        for t, col in bulk:
            size = f"8 * cardinality({col})" if col == "samples" else f"octet_length({col})"
            logical, stored, rows = q(f"SELECT sum({size}), sum(pg_column_size({col})), count(*) FROM {t}")
            methods = dict(c.execute(f"SELECT coalesce(pg_column_compression({col}), 'none'), count(*) FROM {t} "
                                     "GROUP BY 1").fetchall())
            res[t.removeprefix(C + "_")] = {
                "rows": rows, "logical_bytes": int(logical), "stored_bytes": int(stored), "methods": methods,
                "relation_bytes": q(f"SELECT pg_total_relation_size('{t}')")[0]}
        drop(c)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("arm", "default_toast_compression")}),
          {k: (res[k]["stored_bytes"], res[k]["relation_bytes"], res[k]["methods"]) for k in ("chunks", "envelopes", "legacy")})


if __name__ == "__main__":
    main()
