#!/usr/bin/env python3
"""Compare what the MQTT collector stored with the publisher's deterministic signal.

  python examples/collectors/mqtt/check.py --collection public.mqtt
"""
import argparse
import json
import os
import sys

import numpy as np
import psycopg

import contract


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--collection", required=True)
    a.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    o = a.parse_args()
    out, bad = [], []
    with psycopg.connect(o.dsn) as c:
        rows = c.execute(f"SELECT recording_id, (metadata->>'segment_start')::bigint, (checkpoint->>'end')::bigint, "
                         f"sample_rate_hz FROM {o.collection} WHERE metadata->>'source' = 'mqtt' ORDER BY 1").fetchall()
        for rid, seg, n, rate in rows:
            got = np.concatenate([np.asarray(c.execute("SELECT samples FROM morlet_read_interval(%s::regclass,%s,%s,%s)",
                                                       (o.collection, rid, s, min(s + 32768, n))).fetchone()[0])
                                  for s in range(0, n, 32768)]) if n else np.zeros(0)
            want = np.concatenate([contract.signal(seg + s, 512, rate) for s in range(0, n, 512)])[:n] if n else got
            ok = bool(np.array_equal(got, want))
            ctx = dict(c.execute(f"SELECT key, count(*) FROM {o.collection}_ctx WHERE recording_id=%s GROUP BY 1",
                                 (rid,)).fetchall())
            out.append({"recording_id": rid, "segment_start": seg, "samples": n, "samples_identical": ok, "context": ctx})
            if not ok:
                bad.append(rid)
    print(json.dumps({"recordings": out, "problems": bad}, indent=2))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
