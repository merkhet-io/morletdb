#!/usr/bin/env python3
"""Study B8, PROTOCOL.md 4.B: table-level storage through B7's MorletDB path, with the B8 build's image.

  B8_IMAGE=morletdb:b8-<commit> .venv-b3/bin/python evaluation/storage_b8/b8_table.py SET WORK OUT

One load of force and ctrl (B7's `Morlet` arm: the supported importer core, chunk 4,096, defaults, so
`morlet.float32_chunks` at its default, on) into a fresh PostgreSQL; settled and sized as B7 does (chunk table with TOAST
and indexes plus the recordings table, server stopped); then every stored sample read back and compared with the
float32 input bit for bit (B7's `check_all`). Inputs: the runs of WORK/manifest.json (b8.py prepare SET WORK), each
channel checked against the manifest's float32 SHA-256 before loading. B7's harness is imported from its local
worktree (B7_DIR) and used unchanged, except for the container name, port and volume.
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

B7_DIR = Path(os.environ.get("B7_DIR", "/Users/zin/sophia/morletdb-b7/evaluation/storage_b7"))
IMAGE = os.environ["B8_IMAGE"]
os.environ["B7_MORLET_IMAGE"] = IMAGE
os.environ["B7_GROUPS"] = "force,ctrl"
sys.path.insert(0, str(B7_DIR))
import b7  # noqa: E402
import workload as wl  # noqa: E402
import hashlib  # noqa: E402


def units(work):
    man = json.loads((Path(work) / "manifest.json").read_text())
    iu4 = Path(os.environ.get("MORLET_DATA", "/Users/zin/sophia/morletdb/data")) / "denkena2023/npz"
    data = {g: {} for g in ("force", "ctrl")}
    for k, name in enumerate(man["runs"]):
        p = iu4 / name
        z = np.load(p)
        for g in data:
            x = {}
            for ch in wl.GROUPS[g]["channels"]:
                f = z[ch].astype(np.float32)
                want = man["channels"][f"{p.stem}/{ch}"]["sha256_float32"]
                if hashlib.sha256(f.astype("<f4").tobytes()).hexdigest() != want:
                    raise SystemExit(f"{p.stem} {ch}: differs from the manifest")
                x[ch] = f
            data[g][p.stem] = (p.stem, x, wl.start_ns(k))
    return man, data


class Morlet8(b7.Morlet):
    name, port = "b8-morlet", 28881

    def __init__(self):
        super().__init__("m-g16")
        self.volume = "b8-morlet-m-g16"


def main(which, work, out):
    man, data = units(work)
    if man["set"] != which:
        raise SystemExit(f"WORK holds the {man['set']} set, not {which}")
    m = Morlet8()
    res = {"study": "B8 table level (PROTOCOL.md 4.B)", "set": which, "image": IMAGE,
           "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        arm = b7.load_arm(m, data)
        res["load"] = arm
        res["check"] = b7.check_all(m, data)
        res["versions"] = {g: dict(m.conn.execute(
            f"SELECT get_byte(encoded,0)::text, count(*) FROM b7_{g}_chunks GROUP BY 1 ORDER BY 1").fetchall())
            for g in ("force", "ctrl")}
        res["setting"] = m.conn.execute("SELECT current_setting('morlet.float32_chunks', true)").fetchone()[0]
    finally:
        m.stop()
        m.remove()
    res["per_group"] = {}
    for g in ("force", "ctrl"):
        st = res["load"]["storage"][g]
        n = man["inputs"][g]["samples"]
        table = st["chunks_total"] + st["recordings_table"]
        res["per_group"][g] = {"samples": n, "table_bytes": table, "table_bytes_per_sample": round(table / n, 4),
                               "encoded_bytes_per_sample": round(st["encoded_bytes"] / n, 4)}
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    Path(out).write_text(json.dumps(res, indent=1, default=str) + "\n")
    print(json.dumps(res["per_group"]), json.dumps(res["versions"]), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
