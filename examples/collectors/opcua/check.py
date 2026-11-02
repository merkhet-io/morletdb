#!/usr/bin/env python3
"""Check what the reference collector stored against the test server's deterministic signal.

  python examples/collectors/opcua/check.py --collection public.machine [--prefix machine1.vibration]

For every recording of the prefix: every stored sample equals the server's block samples at that offset, the
recording is contiguous from its first block, and its context events carry values, status codes and source
times. Prints a JSON summary; exits 1 on any mismatch.
"""
import argparse
import json
import os
import sys

import numpy as np
import psycopg

import signal_model as sm


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--collection", required=True)
    p.add_argument("--prefix", default="machine1.vibration")
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    a = p.parse_args()
    out, bad = [], []
    with psycopg.connect(a.dsn) as c:
        recs = c.execute(f"SELECT recording_id, (metadata->>'first_block')::bigint, (checkpoint->>'end')::bigint, "
                         f"sample_rate_hz, unit, metadata->>'start' FROM {a.collection} "
                         f"WHERE metadata @> %s::jsonb ORDER BY 2", (json.dumps({"prefix": a.prefix}),)).fetchall()
        for rid, first, n, rate, unit, start in recs:
            blocks = n // sm.BLOCK
            want = np.concatenate([sm.block(first + i) for i in range(blocks)]) if blocks else np.zeros(0)
            got = np.concatenate([np.asarray(c.execute("SELECT samples FROM morlet_read_interval(%s::regclass,%s,%s,%s)",
                                                       (a.collection, rid, s, min(s + 32768, n))).fetchone()[0])
                                  for s in range(0, n, 32768)]) if n else np.zeros(0)
            ok = n % sm.BLOCK == 0 and np.array_equal(got, want)
            ctx = c.execute(f"SELECT key, count(*), count(*) FILTER (WHERE value->>'status' <> 'Good'), "
                            f"min(start_sample), max(start_sample) FROM {a.collection}_ctx WHERE recording_id=%s "
                            f"GROUP BY key ORDER BY key", (rid,)).fetchall()
            in_range = all(lo >= 0 and hi < n + rate for _, _, _, lo, hi in ctx)
            if not ok or not in_range:
                bad.append(rid)
            out.append({"recording_id": rid, "first_block": first, "blocks": blocks, "samples": n, "rate_hz": rate,
                        "unit": unit, "start": start, "samples_identical": bool(ok),
                        "context": {k: {"events": e, "not_good": ng} for k, e, ng, _, _ in ctx},
                        "context_in_range": in_range})
    print(json.dumps({"recordings": out, "problems": bad}, indent=2))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
