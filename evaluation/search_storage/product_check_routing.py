#!/usr/bin/env python3
"""Product check for routed probe search (morlet_search_envelope_probes with exact_threshold 5000) at the five SS3
filter levels (evaluation SS3, PR #66): recall@10 against morlet_search_envelope_interval, filter violations, p95.
Not a new study; it checks that the product function reproduces SS3's router.

  .venv-seismic/bin/python -I evaluation/search_storage/product_check_routing.py morlet_bl1c <queries.json> OUT
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

COLL, W = "public.r2", 4096
LEVELS = ("L1", "L2", "L3", "L4", "L5")
EXACT = "SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s, %s, %s, %s, %s, 10, 0.075, true, true, 1000000)"
ROUTED = "SELECT recording_id, start_sample, metadata FROM morlet_search_envelope_probes(%s, %s, %s, %s, %s)"


def level_filter(level, rid):
    st = rid.split(".")[0]
    return {"L1": {"station": st, "day": 1}, "L2": {"station": st, "tiers": [2]}, "L3": {"station": st},
            "L4": {"tiers": [2]}, "L5": {}}[level]


def satisfies(md, flt):
    return all(set(v) <= set(md.get(k, [])) if isinstance(v, list) else md.get(k) == v for k, v in flt.items())


def main():
    db, qpath, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    queries = [(q["recording_id"], int(q["start"])) for q in json.loads(qpath.read_text())["queries"]]
    res = {"database": db, "levels": {}}
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
        for level in LEVELS:
            rec, ms, viol = [], [], 0
            for timed in (0, 1):
                for rid, s in queries:
                    flt = level_filter(level, rid)
                    t = time.perf_counter()
                    rows = c.execute(ROUTED, (COLL, rid, s, s + W, Jsonb(flt))).fetchall()
                    if timed:
                        ms.append(1e3 * (time.perf_counter() - t))
                        ref = {(r, int(st)) for r, st in c.execute(EXACT, (COLL, rid, s, s + W, Jsonb(flt))).fetchall()}
                        rec.append(len({(r, int(st)) for r, st, _ in rows} & ref) / max(len(ref), 1))
                        viol += sum(not satisfies(md, flt) for _, _, md in rows)
            res["levels"][level] = {"recall_mean": float(np.mean(rec)), "recall_min": float(min(rec)), "filter_violations": viol,
                                    "p50_ms": float(np.percentile(ms, 50)), "p95_ms": float(np.percentile(ms, 95))}
            print(level, json.dumps({k: round(v, 3) for k, v in res["levels"][level].items()}), flush=True)
    json.dump(res, open(out / f"product_routing_{db}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
