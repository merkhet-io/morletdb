#!/usr/bin/env python3
"""B7 harness (PROTOCOL.md): MorletDB and ModelarDB on float32-exact signals.

  python b7.py round R            # one round: every arm loaded fresh, checked, measured; results/round_R.json
  python b7.py stress             # the bit-exactness stress vector through both systems (results/stress.json)

Containers use B3's limits (tsdb_b3/common.py). The Python client runs on the host, single process.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workload as wl  # noqa: E402  (B7's, imported before B3's directory is on the path)
sys.path.insert(1, str(HERE.parent / "tsdb_b3"))
import common  # noqa: E402  (B3: containers with fixed limits, volume files, kernel memory)

sys.path.insert(0, str(HERE.parent.parent / "tools/ingest"))
MORLET_IMAGE = os.environ.get("B7_MORLET_IMAGE", "ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1")
MDB_IMAGE = os.environ.get("B7_MDB_IMAGE", "modelardb:1b90706")
GROUPS = [g for g in os.environ.get("B7_GROUPS", ",".join(wl.MAIN_GROUPS)).split(",") if g]
PW = "b7-local-only"


def manifest():
    return json.loads((wl.RESULTS / "manifest.json").read_text())


# ---------------------------------------------------------------- MorletDB --------------------------------------
class Morlet:
    name, port = "b7-morlet", 28871

    def __init__(self, arm):
        self.arm = arm                                   # m-g1 (release default) or m-g21 (Grizzbit 2.1, opt-in)
        self.volume = f"b7-morlet-{arm}"

    def start(self):
        common.fresh(self.name, MORLET_IMAGE, self.volume, "/var/lib/postgresql/data", [f"{self.port}:5432"],
                     {"POSTGRES_USER": "morlet", "POSTGRES_PASSWORD": PW, "POSTGRES_DB": "morlet"})
        self.conn = self._wait()
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        for g in GROUPS:
            self.conn.execute("SELECT morlet_create_stream_collection('public',%s)", (f"b7_{g}",))

    def restart(self):
        common.run(self.name, MORLET_IMAGE, self.volume, "/var/lib/postgresql/data", [f"{self.port}:5432"],
                   {"POSTGRES_USER": "morlet", "POSTGRES_PASSWORD": PW, "POSTGRES_DB": "morlet"})
        self.conn = self._wait()

    def _wait(self):
        import psycopg
        last = None
        for _ in range(240):
            try:
                c = psycopg.connect(f"host=127.0.0.1 port={self.port} dbname=morlet user=morlet password={PW}",
                                    autocommit=True)
                c.execute("SELECT 1")
                time.sleep(0.5)                          # past the image's first-start restart
                c = psycopg.connect(f"host=127.0.0.1 port={self.port} dbname=morlet user=morlet password={PW}",
                                    autocommit=True)
                c.execute("SELECT 1")
                return c
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.5)
        raise RuntimeError(f"MorletDB did not start: {last}")

    def rid(self, unit, ch):
        return f"{unit}.{ch}"

    def load_group(self, g, units):
        """All units of a group through the supported importer core (import_files), float32 values sent as their
        exact float64 upcast, extractor none, chunk 4,096, the importer's default transaction size."""
        from parquet_import import Config, import_files
        spec = wl.GROUPS[g]
        if self.arm == "m-g21":
            os.environ["PGOPTIONS"] = "-c morlet.chunk_format=grizzbit21"
        t_conv = 0.0

        def reader(path, config, _mapping):
            nonlocal t_conv
            uid, data, _start = units[path.name]
            for ch in spec["channels"]:
                a = time.perf_counter()
                x = data[ch].astype(np.float64)
                t_conv += time.perf_counter() - a
                for s in range(0, x.size, config.chunk_size):
                    yield (self.rid(uid, ch), (spec["rate"], ch, "", {"unit": uid, "group": g,
                                                                     "start_ns": units[path.name][2]}, 1.0, "none"),
                           s, x[s:s + config.chunk_size])
        # import_files wants real files: a tiny marker file per unit carries its name
        tmp = HERE / "results" / "tmp_markers"
        tmp.mkdir(parents=True, exist_ok=True)
        paths = []
        for uid in units:
            p = tmp / uid
            p.write_text(uid)
            paths.append(p)
        dsn = f"host=127.0.0.1 port={self.port} dbname=morlet user=morlet password={PW}"
        t = time.perf_counter()
        rep = import_files(paths, f"public.b7_{g}", Config(), dsn=dsn, reader=reader)
        dt = time.perf_counter() - t
        os.environ.pop("PGOPTIONS", None)
        if rep.get("status") != "complete":
            raise RuntimeError(f"MorletDB import of {g} failed: {rep.get('error')}")
        return {"seconds": dt, "conversion_s": t_conv}

    def settle(self):
        self.conn.execute("CHECKPOINT")
        for g in GROUPS:
            self.conn.execute(f"VACUUM (ANALYZE) b7_{g}_chunks")
            self.conn.execute(f"VACUUM (ANALYZE) b7_{g}")

    def storage(self):
        out = {}
        for g in GROUPS:
            heap, total, idx, enc = self.conn.execute(
                f"SELECT pg_relation_size('b7_{g}_chunks'), pg_total_relation_size('b7_{g}_chunks'), "
                f"pg_indexes_size('b7_{g}_chunks'), (SELECT sum(octet_length(encoded)) FROM b7_{g}_chunks)").fetchone()
            rec = self.conn.execute(f"SELECT pg_total_relation_size('b7_{g}')").fetchone()[0]
            out[g] = {"chunks_total": int(total), "chunks_indexes": int(idx), "recordings_table": int(rec),
                      "encoded_bytes": int(enc or 0)}
        out["database"] = int(self.conn.execute("SELECT pg_database_size('morlet')").fetchone()[0])
        out["pg_wal"] = int(self.conn.execute("SELECT coalesce(sum(size),0) FROM pg_ls_waldir()").fetchone()[0])
        return out

    def read(self, g, uid, ch, start, n):
        b = self.conn.execute("SELECT morlet_read_interval_raw(%s::regclass,%s,%s,%s)",
                              (f"public.b7_{g}", self.rid(uid, ch), start, start + n)).fetchone()[0]
        return np.frombuffer(bytes(b), dtype="<f8")

    def read_all(self, g, uid, ch, n):
        parts = [self.read(g, uid, ch, s, min(262144, n - s)) for s in range(0, n, 262144)]
        return np.concatenate(parts) if parts else np.zeros(0)

    def interval_stats(self, g, uid, ch, start, n):
        return self.conn.execute("SELECT sample_count, min, max, mean FROM morlet_interval_stats(%s::regclass,%s,%s,%s)",
                                 (f"public.b7_{g}", self.rid(uid, ch), start, start + n)).fetchone()

    def column_stats(self, g, ch):
        """min, max, sum, count over every recording of a channel (the whole-column aggregate)."""
        return self.conn.execute(
            f"SELECT min(s.min), max(s.max), sum(s.mean * s.sample_count), sum(s.sample_count) FROM b7_{g} r, "
            f"LATERAL morlet_interval_stats('public.b7_{g}'::regclass, r.recording_id, 0, (r.checkpoint->>'end')::bigint) s "
            f"WHERE r.channel = %s", (ch,)).fetchone()

    def stop(self):
        common.stop(self.name)

    def remove(self):
        common.remove(self.name, self.volume)


# ---------------------------------------------------------------- ModelarDB -------------------------------------
class Modelar:
    name, port = "b7-mdb", 29999

    def __init__(self, arm):
        self.arm = arm                                   # mdb-l (FIELD, lossless), mdb-r1 (1 %), mdb-r5 (5 %)
        self.volume = f"b7-mdb-{arm}"
        self.bound = {"mdb-l": "", "mdb-r1": "(1%)", "mdb-r5": "(5%)"}[arm]

    def _cmd(self):
        return ["target/release/modelardbd", "edge", "/data"]

    def start(self):
        common.fresh(self.name, MDB_IMAGE, self.volume, "/data", [f"{self.port}:9999"], {"MODELARDBD_HOST": "0.0.0.0"},
                     self._cmd())
        self.client = self._wait()
        for g in GROUPS:
            fields = ", ".join(f"{ch} FIELD{self.bound}" for ch in wl.GROUPS[g]["channels"])
            self.sql(f"CREATE TIME SERIES TABLE b7_{g}(ts TIMESTAMP, unit TAG, {fields})")

    def restart(self):
        common.run(self.name, MDB_IMAGE, self.volume, "/data", [f"{self.port}:9999"], {"MODELARDBD_HOST": "0.0.0.0"},
                   self._cmd())
        self.client = self._wait()

    def _wait(self):
        from pyarrow import flight
        last = None
        for _ in range(240):
            try:
                c = flight.FlightClient(f"grpc://127.0.0.1:{self.port}")
                list(c.list_actions())
                return c
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.5)
        raise RuntimeError(f"ModelarDB did not start: {last}")

    def sql(self, q):
        from pyarrow import flight
        return self.client.do_get(flight.Ticket(q)).read_all()

    def action(self, kind):
        from pyarrow import flight
        return list(self.client.do_action(flight.Action(kind, b"")))

    def load_group(self, g, units):
        """One do_put stream per unit: microsecond timestamps (whole numbers at every main-group rate), the unit as
        TAG, one float32 column per channel, in record batches of 65,536 rows."""
        import pyarrow as pa
        from pyarrow import flight
        spec = wl.GROUPS[g]
        period_us = spec["period_ns"] // 1000
        exact_us = spec["period_ns"] % 1000 == 0
        t_conv = 0.0
        t = time.perf_counter()
        for _name, (uid, data, start_ns) in units.items():
            a = time.perf_counter()
            n = len(data[spec["channels"][0]])
            if exact_us:
                ts = start_ns // 1000 + np.arange(n, dtype=np.int64) * period_us
            else:                                         # pvib: 15.625 us, rounded to whole microseconds
                ts = (start_ns + np.arange(n, dtype=np.int64) * spec["period_ns"] + 500) // 1000
            cols = [pa.array(ts, pa.timestamp("us")), pa.array([uid] * n, pa.string_view())] + \
                   [pa.array(data[ch], pa.float32()) for ch in spec["channels"]]
            table = pa.Table.from_arrays(cols, names=["ts", "unit"] + spec["channels"])
            t_conv += time.perf_counter() - a
            w, _ = self.client.do_put(flight.FlightDescriptor.for_path(f"b7_{g}"), table.schema)
            for b in table.to_batches(max_chunksize=65536):
                w.write_batch(b)
            w.close()
        dt = time.perf_counter() - t
        return {"seconds": dt, "conversion_s": t_conv}

    def settle(self):
        t = time.perf_counter()
        self.action("FlushMemory")   # edge without a remote store: FlushNode also transfers
        return time.perf_counter() - t

    def storage(self, files):
        out = {}
        for g in GROUPS:
            pre = f"tables/b7_{g}/"
            sel = [(a, b, p) for a, b, p in files if p.startswith(pre)]
            out[g] = {"parquet": sum(a for a, _, p in sel if p.endswith(".parquet")),
                      "delta_log": sum(a for a, _, p in sel if "_delta_log/" in p),
                      "all": sum(a for a, _, _ in sel)}
        out["wal"] = sum(a for a, _, p in files if p.startswith("wal/"))
        out["other"] = sum(a for a, _, p in files if not p.startswith("tables/") and not p.startswith("wal/"))
        return out

    def read(self, g, uid, ch, start_us, end_us):
        t = self.sql(f"SELECT ts, {ch} FROM b7_{g} WHERE unit = '{uid}' AND ts >= to_timestamp_micros({start_us}) "
                     f"AND ts < to_timestamp_micros({end_us}) ORDER BY ts")
        return t.column(0).cast("int64").to_numpy(), t.column(1).to_numpy()

    def interval_stats(self, g, uid, ch, start_us, end_us):
        t = self.sql(f"SELECT count({ch}), min({ch}), max({ch}), avg({ch}) FROM b7_{g} WHERE unit = '{uid}' "
                     f"AND ts >= to_timestamp_micros({start_us}) AND ts < to_timestamp_micros({end_us})")
        return tuple(t.column(i)[0].as_py() for i in range(4))

    def column_stats(self, g, ch):
        t = self.sql(f"SELECT min({ch}), max({ch}), sum({ch}), count({ch}) FROM b7_{g}")
        return tuple(t.column(i)[0].as_py() for i in range(4))

    def explain_column_stats(self, g, ch):
        t = self.sql(f"EXPLAIN SELECT min({ch}), max({ch}), sum({ch}), count({ch}) FROM b7_{g}")
        return "\n".join(str(x) for x in t.column(t.num_columns - 1).to_pylist())

    def stop(self):
        common.stop(self.name)

    def remove(self):
        common.remove(self.name, self.volume)


# ---------------------------------------------------------------- one arm ---------------------------------------
def group_units(g, man):
    """{marker name: (unit id, {channel: float32}, start_ns)} for a group, checked against the manifest."""
    out = {}
    entries = {e["unit"]: e for e in man["groups"][g]["units"]}
    limit = int(os.environ.get("B7_MAX_UNITS", "0")) if wl.SET == "dev" else 0   # harness work only
    for k, (uid, path) in enumerate(wl.units(g)):
        if limit and k >= limit:
            break
        data = wl.load(g, path)
        e = entries[uid]
        for ch, a in data.items():
            if wl.sha32(a) != e["channels"][ch]["sha256_f32"]:
                raise SystemExit(f"{uid} {ch}: differs from the manifest")
        out[uid] = (uid, data, wl.start_ns(k))
    return out


def load_arm(system, data):
    """Fresh container, every group loaded (timed), settled, stopped, measured on disk, restarted."""
    system.start()
    common.kernel_memory(system.name)                    # (B3) touch: memory.peak covers start and ingestion
    res = {"groups": {}}
    t_all = time.perf_counter()
    for g in GROUPS:
        res["groups"][g] = system.load_group(g, data[g])
    res["load_seconds"] = time.perf_counter() - t_all
    res["settle_seconds"] = system.settle() if isinstance(system, Modelar) else (system.settle() or 0.0)
    res["memory"] = common.kernel_memory(system.name)
    if isinstance(system, Morlet):
        res["storage"] = system.storage()
    system.stop()
    files = common.volume_files(system.volume, MORLET_IMAGE if isinstance(system, Morlet) else MDB_IMAGE)
    res["volume_bytes"] = sum(a for a, _, _ in files)
    if isinstance(system, Modelar):
        res["storage"] = system.storage(files)
    system.restart()
    return res


def check_all(system, data):
    """Every stored sample read back and compared with the float32 input bit for bit (MorletDB: the float64 read-back
    downcast must be exact and equal; ModelarDB: float32 bits; timestamps start + n x period). Lossy arms: max error."""
    out = {}
    for g in GROUPS:
        spec = wl.GROUPS[g]
        bad, n_all, maxerr, maxrel, outside = 0, 0, 0.0, 0.0, 0
        for uid, x, start_ns in data[g].values():
            n = len(x[spec["channels"][0]])
            for ch in spec["channels"]:
                ref = x[ch]
                if isinstance(system, Morlet):
                    got = system.read_all(g, uid, ch, n)
                    ok = got.size == n and np.array_equal(got.astype(np.float32).view(np.uint32), ref.view(np.uint32)) \
                        and np.array_equal(got, ref.astype(np.float64))
                else:
                    s_us = start_ns // 1000
                    ts, got = system.read(g, uid, ch, s_us, s_us + 10**12)
                    ok = got.size == n
                    if ok:
                        exp_ts = (start_ns + np.arange(n, dtype=np.int64) * spec["period_ns"] + 500) // 1000
                        ok_ts = np.array_equal(ts, exp_ts)
                        if system.arm == "mdb-l":
                            ok = ok_ts and np.array_equal(got.astype(np.float32).view(np.uint32), ref.view(np.uint32))
                        else:
                            d = np.abs(got.astype(np.float64) - ref.astype(np.float64))
                            maxerr = max(maxerr, float(d.max()))
                            rel = d / np.maximum(np.abs(ref.astype(np.float64)), 1e-30)
                            maxrel = max(maxrel, float(np.max(np.where(ref == 0, (d > 0) * np.inf, rel))))
                            pct = {"mdb-r1": 0.01, "mdb-r5": 0.05}[system.arm]   # FIELD(1%), FIELD(5%): relative bound
                            outside += int(np.count_nonzero(~(d <= pct * np.abs(ref.astype(np.float64)))))
                            ok = ok_ts
                bad += 0 if ok else 1
                n_all += n
        out[g] = {"series_not_exact": bad, "samples": n_all, **({"max_abs_error": maxerr, "max_rel_error": maxrel,
                                                                  "values_outside_bound": outside}
                                                                 if isinstance(system, Modelar) and system.arm != "mdb-l" else {})}
    return out


def query_plan(data, seed):
    """Interval reads and interval aggregates: per group, 40 seeded (unit, channel, offset) of 1 s and 40 of a whole
    unit's channel."""
    rnd = random.Random(seed)
    plan = []
    for g in GROUPS:
        spec = wl.GROUPS[g]
        units = list(data[g].values())
        for kind in ("1s", "unit"):
            for _ in range(40):
                uid, x, start_ns = rnd.choice(units)
                ch = rnd.choice(spec["channels"])
                n = len(x[ch])
                ln = min(n, int(spec["rate"])) if kind == "1s" else n
                off = rnd.randrange(0, n - ln + 1)
                plan.append((g, kind, uid, ch, off, ln, start_ns))
    return plan


def run_queries(morlet, mdb, data, seed):
    """Alternating per query (MorletDB first on even queries): latency, and equality of the returned samples."""
    plan = query_plan(data, seed)
    rows = []
    for i, (g, kind, uid, ch, off, ln, start_ns) in enumerate(plan):
        spec = wl.GROUPS[g]
        s_us = (start_ns + off * spec["period_ns"]) // 1000
        e_us = (start_ns + (off + ln) * spec["period_ns"]) // 1000
        ref = data[g][uid][1][ch][off:off + ln]
        res = {}
        for sysname in (("morlet", "mdb") if i % 2 == 0 else ("mdb", "morlet")):
            a = time.perf_counter()
            if sysname == "morlet":
                got = morlet.read(g, uid, ch, off, ln)
                t1 = time.perf_counter()
                st = morlet.interval_stats(g, uid, ch, off, ln)
                t2 = time.perf_counter()
                exact = np.array_equal(got, ref.astype(np.float64))
            else:
                _ts, got = mdb.read(g, uid, ch, s_us, e_us)
                t1 = time.perf_counter()
                st = mdb.interval_stats(g, uid, ch, s_us, e_us)
                t2 = time.perf_counter()
                exact = got.size == ref.size and np.array_equal(got.astype(np.float32).view(np.uint32), ref.view(np.uint32))
            res[sysname] = {"read_ms": (t1 - a) * 1000, "stats_ms": (t2 - t1) * 1000, "exact": bool(exact),
                            "count": int(st[0]) if st[0] is not None else None}
        rows.append({"group": g, "kind": kind, "n": ln, **res})
    return rows


def column_aggregates(morlet, mdb):
    out = {}
    for g in GROUPS:
        for ch in wl.GROUPS[g]["channels"]:
            r = {}
            for sysname, s in (("morlet", morlet), ("mdb", mdb)):
                a = time.perf_counter()
                v = s.column_stats(g, ch)
                r[sysname] = {"ms": (time.perf_counter() - a) * 1000, "min": v[0], "max": v[1],
                              "sum": float(v[2]) if v[2] is not None else None, "count": int(v[3])}
            r["mdb_plan"] = mdb.explain_column_stats(g, ch)
            out[f"{g}.{ch}"] = r
    return out


def round_(r):
    man = manifest()
    data = {g: group_units(g, man) for g in GROUPS}
    order = ["m-g1", "mdb-l", "mdb-l", "m-g1"] if r % 2 else ["mdb-l", "m-g1", "m-g1", "mdb-l"]
    res = {"round": r, "groups": GROUPS, "images": {"morlet": MORLET_IMAGE, "modelardb": MDB_IMAGE}, "arms": {}}
    # loads (ABBA within the round: each main arm loaded twice, alternating; the second load is kept for checks)
    for i, arm in enumerate(order):
        s = Morlet(arm) if arm.startswith("m-") else Modelar(arm)
        res["arms"].setdefault(arm, []).append(load_arm(s, data))
        print(json.dumps({"round": r, "arm": arm, "load_s": res["arms"][arm][-1]["load_seconds"]}), flush=True)
        if i < 2:
            s.remove()
    morlet, mdb = Morlet("m-g1"), Modelar("mdb-l")
    morlet.conn = morlet._wait()
    mdb.client = mdb._wait()
    res["check"] = {"morlet": check_all(morlet, data), "mdb": check_all(mdb, data)}
    res["column_aggregates"] = column_aggregates(morlet, mdb)
    res["queries"] = run_queries(morlet, mdb, data, seed=20261112 + r)
    morlet.remove()
    mdb.remove()
    # storage-only arms (one load each per round): Grizzbit 2.1, ModelarDB 1 % and 5 %
    for arm in ("m-g21", "mdb-r1", "mdb-r5"):
        s = Morlet(arm) if arm.startswith("m-") else Modelar(arm)
        res["arms"][arm] = [load_arm(s, data)]
        if isinstance(s, Modelar):
            res.setdefault("lossy_check", {})[arm] = check_all(s, data)
        else:
            res.setdefault("g21_check", check_all(s, data))
        s.remove()
        print(json.dumps({"round": r, "arm": arm, "load_s": res["arms"][arm][-1]["load_seconds"]}), flush=True)
    out = wl.RESULTS / f"round_{r}.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    print("wrote", out)


def stress():
    """-0.0 inside zero runs, NaN with distinct payloads, +-inf, subnormals, linear ramps, a constant run: through
    ModelarDB lossless and MorletDB, compared bit for bit (float32)."""
    v = []
    v += [0.0] * 50 + [-0.0] + [0.0] * 50
    v += list(np.frombuffer(np.array([0x7fc00001, 0x7fc00002, 0xffc00003, 0x7f800001], dtype=np.uint32).tobytes(),
                            dtype=np.float32))
    v += [np.inf, -np.inf, 1e-45, -1e-45, 3e-39]
    v += list(np.float32(0.1) * np.arange(200, dtype=np.float32))
    v += [np.float32(2.5)] * 100
    v += list(np.float32(1.0) + np.float32(1e-7) * np.arange(100, dtype=np.float32))
    x = np.array(v, dtype=np.float32)
    global GROUPS
    GROUPS = ["force"]
    data = {"force": {"stress": ("stress", {"force_sensor_x": x, "force_sensor_y": x[::-1].copy(),
                                            "force_sensor_z": np.zeros_like(x)}, wl.start_ns(0))}}
    out = {"n": int(x.size)}
    for s in (Morlet("m-g1"), Modelar("mdb-l")):
        s.start()
        try:
            ref = x
            if isinstance(s, Morlet):   # MorletDB refuses NaN and +-inf (documented): its arm gets the finite values
                ref = x[np.isfinite(x)]
                out["morlet_excluded_nonfinite"] = int(x.size - ref.size)
                d = {"force": {"stress": ("stress", {"force_sensor_x": ref, "force_sensor_y": ref[::-1].copy(),
                                                      "force_sensor_z": np.zeros_like(ref)}, wl.start_ns(0))}}
                s.load_group("force", d["force"])
            else:
                s.load_group("force", data["force"])
            s.settle()
            if isinstance(s, Morlet):
                got = s.read_all("force", "stress", "force_sensor_x", ref.size)
                g32 = got.astype(np.float32)
            else:
                _ts, g32 = s.read("force", "stress", "force_sensor_x", wl.start_ns(0) // 1000, wl.start_ns(0) // 1000 + 10**9)
                g32 = g32.astype(np.float32)
            diff = [int(i) for i in np.nonzero(g32.view(np.uint32) != ref.view(np.uint32))[0]] if g32.size == ref.size else None
            out[s.arm] = {"returned": int(g32.size), "bit_differences": diff,
                          "examples": [(int(i), hex(int(ref.view(np.uint32)[i])), hex(int(g32.view(np.uint32)[i])))
                                       for i in (diff or [])[:10]]}
        except Exception as e:  # noqa: BLE001
            out[s.arm] = {"error": f"{type(e).__name__}: {e}"[:500]}
        s.remove()
    (wl.RESULTS / "stress.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if sys.argv[1] == "round":
        round_(int(sys.argv[2]))
    elif sys.argv[1] == "stress":
        stress()
