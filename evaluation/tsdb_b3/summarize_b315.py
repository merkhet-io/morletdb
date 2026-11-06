#!/usr/bin/env python3
"""B3.15 summary: QuestDB pair with the released MorletDB 0.3.7-alpha.1 reading with compressed transfer
(morletdb-i2), the same release with raw transfer (morletdb-i1), and the previous release 0.3.6-alpha.1, in one
session. B3's statement rule as in summarize_b313.py (ingestion: >= 10% in all loads; latencies: at p50 and p95).

  python3 evaluation/tsdb_b3/summarize_b315.py [--json]
"""
import json
import statistics
import sys

import summarize_b313 as B
from summarize_b314 import compare, side

if __name__ == "__main__":
    i2 = side("b315-questdb", "morletdb-i2", "reads", "analytics")
    i1 = side("b315-questdb", "morletdb-i1", "reads", "analytics")
    prev = side("b315-previous", "morletdb-i1", "reads", "analytics")
    qdb = side("b315-questdb", "questdb", "reads_native", "analytics")
    la = B.start_load_averages("b315-questdb") + B.start_load_averages("b315-previous")
    if "--json" in sys.argv:
        print(json.dumps({"i2": i2, "i1": i1, "previous": prev, "questdb": qdb, "load_average": la}, indent=1))
    else:
        print("\n\n".join([compare(i2, qdb, "MorletDB 0.3.7, compressed transfer", "QuestDB"),
                           compare(i1, qdb, "MorletDB 0.3.7, raw transfer", "QuestDB"),
                           compare(i2, i1, "compressed transfer", "raw transfer (0.3.7)"),
                           compare(i2, prev, "MorletDB 0.3.7, compressed transfer", "MorletDB 0.3.6-alpha.1"),
                           f"1-minute load average before a load: median {statistics.median(la):.2f}, max {max(la):.2f} (n = {len(la)})"]))
