#!/usr/bin/env python3
"""VR1, post hoc (decides nothing; RESULTS.md "Post hoc: where text recall is lost").

For the 200 frozen queries, text arm, owner role, ef_search 200: tie-aware recall@10 of the HNSW
index against the number of other records whose text vector is identical to the query's
(distance 0, same patient excluded). Many exact duplicates are a known weak spot of HNSW graphs.
No index setting is changed.

  posthoc_ties.py OUT     (writes OUT/posthoc_ties.json; MORLET_DSN as for vr1.py)
"""
import json
import sys
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vr1  # noqa: E402


def main(out):
    out = Path(out)
    with psycopg.connect(vr1.DSN, autocommit=True) as con:
        cur = con.cursor()
        ids = [r[0] for r in cur.execute("SELECT ecg_id FROM vr1.ecg ORDER BY ecg_id").fetchall()]
        sample = sorted(np.random.default_rng(vr1.SEED).choice(ids, vr1.QUERIES, replace=False).tolist())
        rows = []
        for q in vr1.pick_queries(cur, sample):
            with con.transaction():
                ref = vr1.exact(cur, "text", q)
                got = vr1.approximate(cur, "text", q, 200)
                dup = cur.execute("SELECT count(*) FROM vr1.ecg WHERE text_vec = %(v)s::vector "
                                  "AND ecg_id <> %(id)s AND patient_id <> %(p)s",
                                  {"v": q["text"], "id": q["id"], "p": q["patient"]}).fetchone()[0]
            rows.append({"ecg_id": q["id"], "duplicates": dup, "recall": vr1.recall(got, ref)})
    groups = {"0 duplicates": lambda d: d == 0, "1-9": lambda d: 1 <= d <= 9,
              "10-99": lambda d: 10 <= d <= 99, ">= 100": lambda d: d >= 100}
    summary = {}
    for name, f in groups.items():
        rs = [r["recall"] for r in rows if f(r["duplicates"])]
        summary[name] = {"queries": len(rs), "mean_recall": float(np.mean(rs)) if rs else None,
                         "queries_below_1": sum(1 for x in rs if x < 1)}
    res = {"note": "post hoc; decides nothing", "ef_search": 200, "by_duplicates": summary, "per_query": rows}
    (out / "posthoc_ties.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
