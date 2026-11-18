#!/usr/bin/env python3
"""CedarDB range-scan fault: a fresh load ending as round 3's did (load, settle, read-back check, then the container
removed without a shutdown), keeping its volume b3-cedardb-fresh for a file-by-file comparison with round 3's."""
import json
import common, cedar_adapter as c
import run_cedar as rc
m = common.manifest(); ks = list(range(len(m["acquisitions"])))
data, _ = rc.preload(m, ks)
conn = c.start("b3-cedardb-fresh", "D2"); c.create(conn)
for a in m["acquisitions"]:
    c.load_one(conn, a, data[a["k"]])
rc.settle(conn, rc.expected(m, ks))
r = rc.check_reads(conn, m, ks)
conn.close()
common.sh(["docker", "rm", "-f", c.NAME], check=False)   # as run_b319.sh's clean() did after round 3
print(json.dumps({"mismatches": r["mismatches"], "channels_checked": r["channels_checked"]}))
