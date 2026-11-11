#!/usr/bin/env python3
"""B6 B3 loads (PROTOCOL.md section 4C): B3's own programs, unchanged, with two additions for B6.

  cd evaluation/tsdb_b3 && ../../.venv-b3/bin/python ../storage_b6/b6_b3.py load <system> <D1|D2> <rep>        (track S)
  cd evaluation/tsdb_b3 && ../../.venv-b3/bin/python ../storage_b6/b6_b3.py parallel <system> <D> <writers> <rep>  (P)

1. The Grizzbit 2.1 arm's server configuration: with B6_CHUNK_FORMAT=grizzbit21, the MorletDB server is started with
   `-c morlet.chunk_format=grizzbit21` (section 4C: "image morletdb:b6 ... with morlet.chunk_format = grizzbit21");
   after the extension is created the setting is read back (SHOW) and the load refuses to continue unless it is the
   one asked for. Every result file records it as `b6_chunk_format` (track S: also the server's value).
2. Track S, the MorletDB storage measurement (D2 r1, as B5's b3_load.py): the chunks per group (high, mid, process) by
   format version and kind, with their chunk bytes and samples, and for version-5 chunks their correction mode, read
   from the chunks themselves. B3's own storage figures stay as they are.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tsdb_b3"))
import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402

FORMAT = os.environ.get("B6_CHUNK_FORMAT")   # grizzbit21 for the Grizzbit 2.1 arm; unset: server defaults
KINDS = {(2, 0): "v2 raw", (2, 1): "v2 grid", (2, 2): "v2 float", (3, 1): "v3 grid", (5, 1): "v5 grid"}
MODES = {0: "none", 1: "per sample", 2: "table", 3: "exceptions only"}

_fresh = common.fresh


def fresh(name, image, volume, mount, ports, env=None, cmd=None):
    if FORMAT and name == mo.NAME:
        cmd = list(cmd or ["postgres"]) + ["-c", f"morlet.chunk_format={FORMAT}"]
    return _fresh(name, image, volume, mount, ports, env, cmd)


common.fresh = fresh
_start = mo.start


def start(volume, durability="D2"):
    conn = _start(volume, durability)
    if FORMAT:
        conn.execute("SELECT morlet_version()")      # loads the library, which registers the setting
        got = conn.execute("SELECT current_setting('morlet.chunk_format', true)").fetchone()[0]
        if got != FORMAT:
            raise RuntimeError(f"morlet.chunk_format is {got!r}, expected {FORMAT!r}")
    return conn


mo.start = start
_storage = mo.storage


def storage(conn):
    out = _storage(conn)
    # flags byte of a version-5 chunk: after version, kind and the varint sample count (1-3 bytes)
    flags_at = "2 + CASE WHEN c.sample_count < 128 THEN 1 WHEN c.sample_count < 16384 THEN 2 ELSE 3 END"
    rows = conn.execute(
        f"SELECT r.metadata->>'group', get_byte(c.encoded, 0), get_byte(c.encoded, 1), "
        f"CASE WHEN get_byte(c.encoded, 0) = 5 THEN get_byte(c.encoded, {flags_at}) >> 1 END, count(*), "
        f"sum(octet_length(c.encoded)), sum(c.sample_count) FROM {mo.COLLECTION}_chunks c "
        f"JOIN {mo.COLLECTION} r USING (recording_id) GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4").fetchall()
    kinds = {}
    for g, v, k, mode, n, b, s in rows:
        key = KINDS.get((v, k), f"v{v} kind {k}") + ("" if mode is None else f" ({MODES.get(mode, mode)})")
        kinds.setdefault(g, {})[key] = {"chunks": int(n), "chunk_bytes": int(b), "samples": int(s)}
    for g, d in kinds.items():
        d["total"] = {key: sum(x[key] for x in d.values()) for key in ("chunks", "chunk_bytes", "samples")}
    out["b6_chunk_kinds"] = kinds
    out["b6_chunk_format"] = {"asked": FORMAT, "server": conn.execute(
        "SELECT current_setting('morlet.chunk_format', true)").fetchone()[0]}
    return out


mo.storage = storage


def mark(result_dir, name):
    """Records the chunk format asked for in a result file B3's programs wrote (track P has no storage hook)."""
    import json
    p = Path(result_dir) / f"{name}.json"
    if p.is_file():
        d = json.loads(p.read_text())
        d["b6_chunk_format"] = {"asked": FORMAT}
        p.write_text(json.dumps(d, indent=1, default=str) + "\n")


if __name__ == "__main__":
    what, args = sys.argv[1], sys.argv[2:]
    if what == "load":
        import run
        run.load(*args)
    elif what == "parallel":
        import parallel
        system, d, w, r = args
        parallel.main(system, d, w, r)
        mark(common.RESULTS, f"parallel_{system}_{d}_w{w}_r{r}")
    else:
        raise SystemExit("usage: b6_b3.py load <system> <D> <rep> | parallel <system> <D> <writers> <rep>")
