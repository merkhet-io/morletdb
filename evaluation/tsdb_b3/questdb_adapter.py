"""B3 QuestDB adapter (PROTOCOL.md, "QuestDB"): idiomatic wide tables, TIMESTAMP_NS, WAL, dedup, dataframe() ingestion."""
import time

import numpy as np
import pandas as pd
import questdb

import common
import workload as wl

IMAGE = "questdb/questdb:10.1.0"
NAME = "b3-questdb"
TABLES = {g: f"b3_{g}" for g in wl.GROUPS}


def env_for(durability, codec=None):
    env = {}
    if durability == "D1":
        env["QDB_CAIRO_COMMIT_MODE"] = "sync"
    if codec:
        env["QDB_CAIRO_PARTITION_ENCODER_PARQUET_COMPRESSION_CODEC"] = codec
        if codec == "ZSTD":
            env["QDB_CAIRO_PARTITION_ENCODER_PARQUET_COMPRESSION_LEVEL"] = "9"
    return env


def start(volume, durability="D2", codec=None):
    common.fresh(NAME, IMAGE, volume, "/var/lib/questdb", [9000, 8812], env_for(durability, codec))
    return _wait()


def connect():
    return questdb.connect("ws::addr=127.0.0.1:9000;")


def create(db):
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f"{c} DOUBLE" for c in spec["channels"])
        db.execute(f"CREATE TABLE IF NOT EXISTS {TABLES[g]} (ts TIMESTAMP_NS, acquisition SYMBOL, {cols}) "
                   f"timestamp(ts) PARTITION BY HOUR WAL DEDUP UPSERT KEYS(ts, acquisition)")


def frames(acq, data):
    """One pandas frame per group: exact nanosecond timestamps, the acquisition symbol, channels as columns."""
    out = {}
    for g, spec in wl.GROUPS.items():
        n = len(data[spec["channels"][0]])
        ts = acq["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
        df = pd.DataFrame({"ts": pd.to_datetime(ts, unit="ns", utc=True),
                           "acquisition": pd.Categorical([acq["acquisition"]] * n)})
        for c in spec["channels"]:
            df[c] = data[c]
        out[g] = df
    return out


def load_one(db, acq, data):
    """Ingest one acquisition; returns (conversion seconds, [per-call acknowledgement seconds])."""
    t = time.perf_counter()
    fr = frames(acq, data)
    conv = time.perf_counter() - t
    acks = []
    for g, df in fr.items():
        t = time.perf_counter()
        db.dataframe(df, table_name=TABLES[g], symbols=["acquisition"], at="ts")
        acks.append(time.perf_counter() - t)
    return conv, acks


def count(db, g):
    return int(db.query(f"select count() from {TABLES[g]}").to_pandas().iloc[0, 0])


def wal_pending(db):
    df = db.query("select name, writerTxn, sequencerTxn, suspended from wal_tables()").to_pandas()
    df = df[df["name"].isin(TABLES.values())]
    return int((df["sequencerTxn"] - df["writerTxn"]).sum()), bool(df["suspended"].any())


def read(db, acq, channels, offset, length, group="high"):
    """Exact samples [offset, offset+length) of the channels, as float64 arrays, and the timestamps."""
    period = wl.GROUPS[group]["period_ns"]
    lo = acq["start_ns"] + offset * period
    hi = acq["start_ns"] + (offset + length) * period
    q = (f"select ts, {', '.join(channels)} from {TABLES[group]} where acquisition = '{acq['acquisition']}' "
         f"and ts >= {lo}::timestamp_ns and ts < {hi}::timestamp_ns order by ts")
    t = db.query(q).to_arrow()
    ts = t.column("ts").cast("int64").to_numpy()
    return ts, {c: t.column(c).to_numpy() for c in channels}


def analytics(db, acq, offset, length):
    period = wl.GROUPS["high"]["period_ns"]
    lo = acq["start_ns"] + offset * period
    hi = acq["start_ns"] + (offset + length) * period
    q = (f"select min(vibration_1), max(vibration_1), avg(vibration_1), sqrt(avg(vibration_1 * vibration_1)), count() "
         f"from {TABLES['high']} where acquisition = '{acq['acquisition']}' and ts >= {lo}::timestamp_ns "
         f"and ts < {hi}::timestamp_ns")
    return [float(v) for v in db.query(q).to_pandas().iloc[0].tolist()]


def sentinel(db, k_after):
    """One row per table in a later hour, so every data partition can be converted (excluded from all measures)."""
    t0 = wl.start_ns(k_after) + 3600 * 1_000_000_000
    for g, spec in wl.GROUPS.items():
        df = pd.DataFrame({"ts": pd.to_datetime([t0], unit="ns", utc=True), "acquisition": pd.Categorical(["__sentinel__"])})
        for c in spec["channels"]:
            df[c] = [0.0]
        db.dataframe(df, table_name=TABLES[g], symbols=["acquisition"], at="ts")


def convert_parquet(db, before_ns):
    t = time.perf_counter()
    for g in TABLES.values():
        db.execute(f"ALTER TABLE {g} CONVERT PARTITION TO PARQUET WHERE ts < {before_ns}::timestamp_ns")
    return time.perf_counter() - t


def restart(volume, durability="D2", codec=None):
    """Same volume, new server settings (used to switch the Parquet codec before converting)."""
    common.run(NAME, IMAGE, volume, "/var/lib/questdb", [9000, 8812], env_for(durability, codec))
    return _wait()


def _wait():
    for _ in range(120):
        try:
            db = connect()
            db.query("select 1").to_pandas()
            return db
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("QuestDB did not start")


def classify(rel):
    """Storage class of a file path relative to a table directory."""
    top, name = rel.split("/")[0], rel.split("/")[-1]
    if top.startswith("wal") or top in ("seq", "txn_seq"):
        return "wal"
    if name.endswith(".parquet"):
        return "data"
    if name.split(".")[0] in ("ts",) and name.endswith(".d"):
        return "timestamps"
    if name == "acquisition.d":
        return "identity"       # per-row symbol key (int32): the acquisition of every row
    if name.split(".")[0] == "acquisition" or name.endswith((".k", ".v", ".o", ".c")):
        return "symbols"        # symbol dictionary and its index
    if name.endswith(".d"):
        return "data"
    return "metadata"


def storage(volume, exclude_partition=None):
    """Per table and class: apparent and allocated bytes; plus the volume totals. exclude_partition names the
    sentinel's partition (e.g. 2026-01-01T03), whose directories are reported apart and not counted."""
    files = common.volume_files(volume, IMAGE)
    tables, total = {}, [0, 0]
    for app, alloc, path in files:
        total[0] += app
        total[1] += alloc
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "db":
            continue
        table = next((t for t in TABLES.values() if parts[1] == t or parts[1].startswith(t + "~")), None)
        if table is None:
            continue
        cls = classify("/".join(parts[2:]))
        if exclude_partition and parts[2].split(".")[0] == exclude_partition:
            cls = "sentinel_partition_excluded"
        d = tables.setdefault(table, {}).setdefault(cls, [0, 0, 0])
        d[0] += app
        d[1] += alloc
        d[2] += 1
    return {"tables": {t: {c: {"apparent": v[0], "allocated": v[1], "files": v[2]} for c, v in cl.items()}
                       for t, cl in tables.items()},
            "volume": {"apparent": total[0], "allocated": total[1]}}
