#!/usr/bin/env python3
"""B5 track S load (PROTOCOL.md section 4B): B3's `run.py load <system> <D> <rep>`, unchanged, with one addition to
the MorletDB storage measurement (D2 r1): the chunks per group (high, mid, process) by format version and kind, with
their chunk bytes (octet_length of the stored chunk) and samples, read from the chunks themselves (the version and
kind bytes; section 3). B3's own storage figures stay as they are.

  cd evaluation/tsdb_b3 && ../../.venv-b3/bin/python ../storage_b5/b3_load.py load <system> <D1|D2> <rep>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tsdb_b3"))
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

KINDS = {(2, 0): "v2 raw", (2, 1): "v2 grid", (2, 2): "v2 float", (3, 1): "v3 grid", (4, 3): "v4 pco"}
_storage = mo.storage


def storage(conn):
    out = _storage(conn)
    rows = conn.execute(
        f"SELECT r.metadata->>'group', get_byte(c.encoded, 0), get_byte(c.encoded, 1), count(*), "
        f"sum(octet_length(c.encoded)), sum(c.sample_count) FROM {mo.COLLECTION}_chunks c "
        f"JOIN {mo.COLLECTION} r USING (recording_id) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3").fetchall()
    kinds = {}
    for g, v, k, n, b, s in rows:
        kinds.setdefault(g, {})[KINDS.get((v, k), f"v{v} kind {k}")] = {"chunks": int(n), "chunk_bytes": int(b),
                                                                       "samples": int(s)}
    for g, d in kinds.items():
        d["total"] = {key: sum(x[key] for x in d.values()) for key in ("chunks", "chunk_bytes", "samples")}
    out["b5_chunk_kinds"] = kinds
    return out


mo.storage = storage

if __name__ == "__main__":
    assert sys.argv[1] == "load", "usage: b3_load.py load <system> <D1|D2> <rep>"
    run.load(*sys.argv[2:])
