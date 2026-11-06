#!/usr/bin/env python3
"""B3.16 summary: B3.15 with the corrected harness. QuestDB pair with the released MorletDB 0.3.7-alpha.1 reading
with compressed transfer (morletdb-i2) and with raw transfer (morletdb-i1), in one session. B3's statement rule as in
summarize_b313.py (ingestion: >= 10% in all loads; latencies: at p50 and p95).

  python3 evaluation/tsdb_b3/summarize_b316.py [--json]
"""
import json
import statistics
import sys

import summarize_b313 as B
from summarize_b314 import compare, side

if __name__ == "__main__":
    i2 = side("b316-questdb", "morletdb-i2", "reads", "analytics")
    i1 = side("b316-questdb", "morletdb-i1", "reads", "analytics")
    qdb = side("b316-questdb", "questdb", "reads_native", "analytics")
    la = B.start_load_averages("b316-questdb")
    if "--json" in sys.argv:
        print(json.dumps({"i2": i2, "i1": i1, "questdb": qdb, "load_average": la}, indent=1))
    else:
        print("\n\n".join([compare(i2, qdb, "MorletDB 0.3.7, compressed transfer", "QuestDB"),
                           compare(i1, qdb, "MorletDB 0.3.7, raw transfer", "QuestDB"),
                           compare(i2, i1, "compressed transfer", "raw transfer (0.3.7)"),
                           f"1-minute load average before a load: median {statistics.median(la):.2f}, max {max(la):.2f} (n = {len(la)})"]))
