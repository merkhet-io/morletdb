"""B3 MorletDB adapter (PROTOCOL.md, "MorletDB"): one recording per (acquisition, channel), bulk path = the
importer core `import_files` with an in-memory reader, chunk 4,096, 16 chunks per transaction."""
import secrets
import sys
import time

import psycopg
from psycopg.types.json import Jsonb  # noqa: F401  (import_files uses it)

import common
import workload as wl

sys.path.insert(0, str(wl.ROOT / "tools/ingest"))
from parquet_import import Config, import_files  # noqa: E402
from morlet_import import build  # noqa: E402

IMAGE = "ghcr.io/merkhet-io/morletdb:0.3.3-alpha.1"
NAME = "b3-morletdb"
PORT = 54340
COLLECTION = "b3"
MAX_READ = 32768
_PASSWORD = secrets.token_hex(16)   # generated per process, never written anywhere
DSN = f"host=127.0.0.1 port={PORT} user=morlet dbname=morlet password={_PASSWORD}"
GROUP_OF = {c: g for g, s in wl.GROUPS.items() for c in s["channels"]}


def start(volume, durability="D2"):
    cmd = ["postgres"] + (["-c", "synchronous_commit=off"] if durability == "D2" else [])
    common.fresh(NAME, IMAGE, volume, "/var/lib/postgresql/data", [f"{PORT}:5432"],
                 {"POSTGRES_USER": "morlet", "POSTGRES_PASSWORD": _PASSWORD, "POSTGRES_DB": "morlet"}, cmd)
    for _ in range(240):
        try:
            conn = psycopg.connect(DSN, connect_timeout=2)
            conn.execute("SELECT 1")
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("MorletDB did not start")
    # the entrypoint restarts the server once after init: wait until it is the final one
    time.sleep(3)
    conn = connect()
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    sc = conn.execute("SHOW synchronous_commit").fetchone()[0]
    want = "off" if durability == "D2" else "on"
    if sc != want:
        raise RuntimeError(f"synchronous_commit is {sc}, expected {want}")
    return conn


def connect():
    for _ in range(60):
        try:
            return psycopg.connect(DSN, autocommit=True)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("cannot connect")


def create(conn, search=False):
    conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLLECTION,))
    if search:
        conn.execute("SELECT morlet_enable_envelopes(%s::regclass)", (COLLECTION,))


def rid(acq, channel):
    return f"{acq['acquisition']}.{channel}"


def extractor(channel, search):
    if not search:
        return "none"
    rate = wl.GROUPS[GROUP_OF[channel]]["rate"]
    return "morlet-energy-v1" if rate >= 128 else ("morlet-energy-lowrate-v1" if rate >= 1 else "none")


def reader_for(acq, data, search, timer):
    """import_files reader over one acquisition already in memory; times the list conversion separately."""
    def read(_path, config, _mapping):
        for c in wl.UNITS:
            g = GROUP_OF[c]
            spec = (wl.GROUPS[g]["rate"], c, wl.UNITS[c],
                    {"acquisition": acq["acquisition"], "group": g, "start_ns": acq["start_ns"], "b3_k": acq["k"]},
                    wl.QUANTUM.get(c, 1.0) if VARIANT["quantum"] else 1.0, extractor(c, search))
            x = data[c]
            for s in range(0, x.size, config.chunk_size):
                t = time.perf_counter()
                vals = x[s:s + config.chunk_size].tolist()
                timer[0] += time.perf_counter() - t
                yield rid(acq, c), spec, s, vals
    return read


VARIANT = {"rpc_chunks": 1, "quantum": True}   # attribution runs only (RESULTS.md, "Attribution"); frozen arms use the defaults


def load_one(acq, data, search=False):
    """Ingest one acquisition with the importer core; returns (conversion s, acknowledgement s, report)."""
    timer = [0.0]
    path = wl.ROOT / acq["path"]
    rep = import_files([path], COLLECTION, Config(rpc_chunks=VARIANT["rpc_chunks"]), dsn=DSN,
                       reader=reader_for(acq, data, search, timer))
    # elapsed_s excludes the importer's source-file hashing (preflight); conversion is inside it
    return timer[0], rep["elapsed_s"], rep


def build_envelopes(conn_dsn_conn, ids, window=4096):
    return build(conn_dsn_conn, COLLECTION, ids, window, False)


def count(conn):
    return conn.execute(f"SELECT count(*), coalesce(sum((checkpoint->>'end')::bigint), 0) FROM {COLLECTION}").fetchone()


def read(conn, acq, channels, offset, length):
    """Exact samples [offset, offset+length) per channel as float64 arrays; one round trip per channel,
    pieces of at most 32,768 samples (the documented per-call limit), assembled in order."""
    import numpy as np
    out = {}
    for c in channels:
        rows = conn.execute(
            "SELECT a, samples FROM generate_series(%s::bigint, %s::bigint - 1, %s) a, "
            "LATERAL morlet_read_interval(%s::regclass, %s, a, least(a + %s, %s::bigint)) ORDER BY a",
            (offset, offset + length, MAX_READ, COLLECTION, rid(acq, c), MAX_READ, offset + length),
            binary=True).fetchall()
        out[c] = np.concatenate([np.asarray(r[1], dtype=np.float64) for r in rows])
    return out


def analytics(conn, acq, offset, length):
    q = ("SELECT min(x), max(x), avg(x), sqrt(avg(x*x)), count(*) FROM generate_series(%s::bigint, %s::bigint - 1, %s) a, "
         "LATERAL morlet_read_interval(%s::regclass, %s, a, least(a + %s, %s::bigint)) r, unnest(r.samples) x")
    row = conn.execute(q, (offset, offset + length, MAX_READ, COLLECTION, rid(acq, "vibration_1"), MAX_READ,
                           offset + length)).fetchone()
    return [float(v) for v in row]


def settle(conn):
    conn.execute("CHECKPOINT")
    conn.execute(f"VACUUM (ANALYZE) {COLLECTION}")
    for (rel,) in conn.execute("SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname LIKE %s",
                                (COLLECTION + "%",)).fetchall():
        conn.execute(f"VACUUM (ANALYZE) {rel}")
    conn.execute("CHECKPOINT")


def storage(conn):
    """Bytes per relation class of the collection's relations, and pg_wal."""
    rows = conn.execute(
        "SELECT c.relname, c.relkind, pg_relation_size(c.oid), "
        "coalesce(pg_total_relation_size(c.reltoastrelid), 0), pg_indexes_size(c.oid) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname LIKE %s", (COLLECTION + "%",)).fetchall()
    rels = {r[0]: {"heap": r[2], "toast": r[3], "indexes": r[4]} for r in rows}
    groups = {g: {"encoded_bytes": int(b), "samples": int(n)} for g, b, n in conn.execute(
        f"SELECT r.metadata->>'group', sum(pg_column_size(c.encoded)), sum(c.sample_count) FROM {COLLECTION}_chunks c "
        f"JOIN {COLLECTION} r USING (recording_id) GROUP BY 1").fetchall()}
    wal = conn.execute("SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()").fetchone()[0]
    db = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    return {"relations": rels, "groups": groups, "pg_wal": int(wal), "database": int(db)}
