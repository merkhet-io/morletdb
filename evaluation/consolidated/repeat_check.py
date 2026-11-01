"""Repeatability check (artifact step 6), on an isolated copy of the development database.

E1 rebuilt the probe index at its end, so the index now in the database is the one D1 searched. This script
re-runs, for the 100 dev queries, the product exact and probe functions and compares
  (a) exact results with E1's stored exact lists        -> exact search must reproduce exactly;
  (b) probe results with D1's stored probe hits          -> re-search on the preserved (same) index;
  (c) probe results with E1's stored probe lists         -> same data, but an index built before the rebuild.
It writes no data to the database.

  createdb -T morlet_r2 morlet_r2_check      # isolated copy; drop it afterwards
  .venv-seismic/bin/python -I evaluation/consolidated/repeat_check.py morlet_r2_check evaluation/consolidated/results out.json
"""
import json
import sys
from pathlib import Path

import psycopg

COLL, W = "public.r2", 4096
EXACT = ("SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s, %s, %s, %s, '{}'::jsonb, 10, 0.075,"
         " true, true, 1000000)")
PROBE = "SELECT recording_id, start_sample FROM morlet_search_envelope_probes(%s, %s, %s, %s, '{}'::jsonb, 10)"


def main(db, results, out):
    results = Path(results)
    e1 = json.loads((results / "e1_dev.json").read_text())["per_query"]
    d1 = json.loads((results / "d1_dev.json").read_text())["per_query"]
    res = {"queries": len(e1), "exact_equal_E1": 0, "probe_equal_D1_same_index": 0, "probe_equal_E1_before_rebuild": 0,
           "probe_recall_now": [], "differences": []}
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as conn:
        for key, rec in e1.items():
            rid, s = key.rsplit(":", 1)
            s = int(s)
            exact = [[r, int(st)] for r, st in conn.execute(EXACT, (COLL, rid, s, s + W)).fetchall()]
            probe = [[r, int(st)] for r, st in conn.execute(PROBE, (COLL, rid, s, s + W)).fetchall()]
            d1_hits = [list(h) for h in d1[key]["passes"][0]["profile"]["hits"]]
            res["exact_equal_E1"] += exact == rec["top"]["EX-2"]
            res["probe_equal_D1_same_index"] += probe == d1_hits
            res["probe_equal_E1_before_rebuild"] += probe == rec["top"]["PR"]
            res["probe_recall_now"].append(len({tuple(x) for x in probe} & {tuple(x) for x in exact}) / 10)
            if probe != rec["top"]["PR"] or probe != d1_hits or exact != rec["top"]["EX-2"]:
                res["differences"].append({"query": key, "exact_equal_E1": exact == rec["top"]["EX-2"],
                                           "probe_equal_D1": probe == d1_hits, "probe_equal_E1": probe == rec["top"]["PR"]})
    res["probe_recall_now_mean"] = sum(res["probe_recall_now"]) / len(res["probe_recall_now"])
    Path(out).write_text(json.dumps(res, indent=1))
    print({k: v for k, v in res.items() if k not in ("probe_recall_now", "differences")}, "differences:", len(res["differences"]))


if __name__ == "__main__":
    main(*sys.argv[1:4])
