#!/usr/bin/env python3
"""B3 runner (PROTOCOL.md). Subcommands, each writing results/<name>.json:

  subset                      correctness and retry semantics on 3 acquisitions, both systems
  load <system> <D1|D2> <rep> one measured fresh load (system: questdb, morletdb, morletdb-search),
                              then full correctness; rep 1 of D2 also measures storage, reads and analytics,
                              and for QuestDB the Parquet arms (LZ4 after rep 1, ZSTD 9 after rep 2)
"""
import json
import sys
import threading
import time

import numpy as np

import common
import correctness as cr
import workload as wl

SUBSET = [0, 61, 123]


def save(name, obj):
    common.RESULTS.mkdir(parents=True, exist_ok=True)
    (common.RESULTS / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str) + "\n")
    print(json.dumps(obj, indent=1, default=str)[:4000])


def preload(m, ks):
    t = time.perf_counter()
    data = {k: wl.load(wl.ROOT / m["acquisitions"][k]["path"]) for k in ks}
    return data, time.perf_counter() - t


def q_settle(db, expected, timeout=600):
    import questdb_adapter as q
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        pending, suspended = q.wal_pending(db)
        if suspended:
            raise RuntimeError("a QuestDB WAL table is suspended")
        if pending == 0 and all(q.count(db, g) == expected[g] for g in wl.GROUPS):
            return time.perf_counter() - t0
        time.sleep(0.05)
    raise RuntimeError("QuestDB did not settle")


def subset():
    import morletdb_adapter as mo
    import questdb_adapter as q
    m = common.manifest()
    data, _ = preload(m, SUBSET)
    rng = np.random.default_rng(1)
    expected = {g: sum(m["acquisitions"][k]["channels"][s["channels"][0]]["samples"] for k in SUBSET)
                for g, s in wl.GROUPS.items()}
    out = {}

    # QuestDB
    db = q.start("b3-q-subset")
    q.create(db)
    for k in SUBSET:
        q.load_one(db, m["acquisitions"][k], data[k])
    q_settle(db, expected)
    r = {"counts": cr.check_counts("questdb", db, m, SUBSET), "reads": cr.check_reads("questdb", db, m, SUBSET),
         "intervals": cr.sample_reads("questdb", db, m, SUBSET, rng)}
    a = m["acquisitions"][SUBSET[1]]
    q.load_one(db, a, data[SUBSET[1]])                       # identical retry
    q_settle(db, expected)
    r["identical_retry"] = {"counts_unchanged": cr.check_counts("questdb", db, m, SUBSET)["ok"],
                            "reads": cr.check_reads("questdb", db, m, [SUBSET[1]])["mismatches"]}
    altered = {c: v.copy() for c, v in data[SUBSET[1]].items()}
    altered["vibration_1"][1000] += 1.0
    q.load_one(db, a, altered)
    q_settle(db, expected)
    _, v = q.read(db, a, ["vibration_1"], 1000, 1)
    r["altered_retry"] = {"accepted": True, "counts_unchanged": cr.check_counts("questdb", db, m, SUBSET)["ok"],
                          "stored_value_is_altered": bool(v["vibration_1"][0] == altered["vibration_1"][1000])}
    out["questdb"] = r
    common.remove(q.NAME, "b3-q-subset")

    # MorletDB
    conn = mo.start("b3-m-subset")
    mo.create(conn)
    for k in SUBSET:
        mo.load_one(m["acquisitions"][k], data[k])
    r = {"counts": cr.check_counts("morletdb", conn, m, SUBSET), "reads": cr.check_reads("morletdb", conn, m, SUBSET),
         "intervals": cr.sample_reads("morletdb", conn, m, SUBSET, rng)}
    mo.load_one(a, data[SUBSET[1]])                          # identical retry
    r["identical_retry"] = {"counts_unchanged": cr.check_counts("morletdb", conn, m, SUBSET)["ok"],
                            "reads": cr.check_reads("morletdb", conn, m, [SUBSET[1]])["mismatches"]}
    try:
        mo.load_one(a, altered)
        r["altered_retry"] = {"accepted": True}
    except Exception as exc:
        msg = str(getattr(exc, "args", [exc])[0])
        r["altered_retry"] = {"accepted": False, "error": msg.get("error") if isinstance(msg, dict) else msg[:200]}
    r["altered_retry"]["reads_unchanged"] = cr.check_reads("morletdb", conn, m, [SUBSET[1]])["mismatches"] == []
    out["morletdb"] = r
    conn.close()
    common.remove(mo.NAME, "b3-m-subset")
    save("correctness_subset", out)



def full_counts_and_reads(system, handle, m, sha_reads=True):
    ks = list(range(len(m["acquisitions"])))
    out = {"counts": cr.check_counts(system, handle, m, ks)}
    if sha_reads:
        t = time.perf_counter()
        out["reads"] = cr.check_reads(system, handle, m, ks)
        out["reads"]["seconds"] = round(time.perf_counter() - t, 1)
    return out


def ingest(system, handle, m, data, search=False):
    """Load all acquisitions in order from memory; per-acquisition acknowledgement times."""
    import morletdb_adapter as mo
    import questdb_adapter as q
    conv, acks, calls, failures, preflight = 0.0, [], [], [], 0.0
    t0 = time.perf_counter()
    for a in m["acquisitions"]:
        t = time.perf_counter()
        try:
            if system == "questdb":
                c, per_call = q.load_one(handle, a, data[a["k"]])
                calls += per_call
            else:
                c, _, rep = mo.load_one(a, data[a["k"]], search=search)
                preflight += rep["preflight_s"]    # the importer hashing its source file: not ingestion work
                t += rep["preflight_s"]
        except Exception as exc:
            failures.append(f"{a['acquisition']}: {str(exc)[:200]}")
            raise
        conv += c
        acks.append(time.perf_counter() - t)
    t_last = time.perf_counter()
    return {"wall_s": t_last - t0 - preflight, "conversion_s": conv, "acks": acks, "calls": calls,
            "failures": failures, "excluded_preflight_s": preflight}, t_last


def total_samples(m):
    return sum(c["samples"] for a in m["acquisitions"] for c in a["channels"].values())


def expected_rows(m):
    return {g: sum(a["channels"][s["channels"][0]]["samples"] for a in m["acquisitions"]) for g, s in wl.GROUPS.items()}


def load(system, durability, rep):
    import morletdb_adapter as mo
    import questdb_adapter as q
    rep = int(rep)
    m = common.manifest()
    data, parse_s = preload(m, range(len(m["acquisitions"])))
    name = f"load_{system}_{durability}_r{rep}"
    volume = f"b3-{system}-{durability.lower()}-r{rep}"
    res = {"system": system, "durability": durability, "rep": rep, "parse_s": parse_s,
           "samples": total_samples(m), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if system != "questdb":
        res["image"] = mo.IMAGE
        res["image_id"] = common.sh(["docker", "image", "inspect", mo.IMAGE, "--format", "{{.Id}}"]).strip()
    search = system == "morletdb-search"
    if system in ("morletdb-batched16", "morletdb-noquantum"):     # attribution, outside the frozen arms
        mo.VARIANT.update({"rpc_chunks": 16} if system.endswith("batched16") else {"quantum": False})
    if system == "morletdb-031":   # B3.1: SQL 0.3.3 raw reads and native interval statistics on top of the client path
        mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True})
    if system == "morletdb-031c16":   # the same with 16,384-sample chunks (a documented importer option)
        mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "chunk_size": 16384})
    if system in ("morletdb-b30", "morletdb-batched16", "morletdb-noquantum"):
        # the B3 stage-1 client path: Python lists in text arrays, reads in 32,768-sample pieces as Python floats
        mo.VARIANT.update({"binary": False, "binary_read": False, "short_read": False})
    if system == "questdb":
        handle = q.start(volume, durability)
        q.create(handle)
        container = q.NAME
    else:
        handle = mo.start(volume, durability)
        mo.create(handle, search=search)
        container = mo.NAME
    with common.Stats(container) as st:
        r, t_last = ingest(system, handle, m, data, search)
        if system == "questdb":
            q_settle(handle, expected_rows(m), timeout=3600)
        else:
            want = sum(a["channels"][c]["samples"] for a in m["acquisitions"] for c in a["channels"])
            while mo.count(handle)[1] != want:
                time.sleep(0.05)
        r["queryable_after_last_ack_s"] = time.perf_counter() - t_last
        if search:
            ids = [mo.rid(a, c) for a in m["acquisitions"] for c in wl.UNITS]
            t = time.perf_counter()
            bconn = __import__("psycopg").connect(mo.DSN)
            built = mo.build_envelopes(bconn, ids, 4096)
            bconn.close()
            r["envelope_build_s"] = time.perf_counter() - t
            r["envelopes_built"] = sum(built.values())
    res["ingest"] = {"wall_s": round(r["wall_s"], 3), "samples_per_s": round(res["samples"] / r["wall_s"]),
                     "conversion_s": round(r["conversion_s"], 3), "ack_per_acquisition": common.pcts(r["acks"]),
                     "queryable_after_last_ack_s": round(r["queryable_after_last_ack_s"], 3),
                     "failures": r["failures"], "excluded_preflight_s": round(r["excluded_preflight_s"], 3),
                     "server": st.summary()}
    if r["calls"]:
        res["ingest"]["ack_per_call"] = common.pcts(r["calls"])
    for key in ("envelope_build_s", "envelopes_built"):
        if key in r:
            res["ingest"][key] = r[key]
    res["correctness"] = full_counts_and_reads("questdb" if system == "questdb" else "morletdb", handle, m)
    ok = res["correctness"]["counts"]["ok"] and not res["correctness"]["reads"]["mismatches"]
    res["correct"] = ok
    save(name, res)
    if not ok:
        raise SystemExit("correctness failed")
    measure = durability == "D2" and (rep == 1 or rep == 2 and system in ("morletdb", "questdb", "morletdb-batched16"))
    if system == "questdb" and measure:
        db = handle
        common.stop(q.NAME)
        res["storage_native"] = q.storage(volume)
        db = q.restart(volume, durability)
        if rep == 1:
            res["reads_native"] = reads("questdb", db, m, data)
            res["analytics"] = analytics("questdb", db, m, data)
        codec = "LZ4_RAW" if rep == 1 else "ZSTD"
        db = q.restart(volume, durability, None if codec == "LZ4_RAW" else codec)
        q.sentinel(db, len(m["acquisitions"]))
        exp = {g: v + 1 for g, v in expected_rows(m).items()}
        q_settle(db, exp)
        hour = 3600 * 10**9
        sentinel_hour = (wl.start_ns(len(m["acquisitions"])) + hour) // hour * hour
        conv_s = q.convert_parquet(db, sentinel_hour)
        t = time.perf_counter()
        while True:
            np_ = db.query("select count() from table_partitions('b3_high') where not isParquet").to_pandas().iloc[0, 0]
            if q.wal_pending(db)[0] == 0 and int(np_) <= 1:
                break
            time.sleep(0.2)
        res[f"parquet_{codec}"] = {"convert_statement_s": round(conv_s, 3),
                                   "until_settled_s": round(conv_s + time.perf_counter() - t, 3)}
        res[f"parquet_{codec}"]["partitions"] = db.query(
            "select name, isParquet, numRows, diskSize, parquetFileSize from table_partitions('b3_high')").to_pandas().to_dict("records")
        res[f"parquet_{codec}"]["correctness"] = {
            "reads": cr.check_reads("questdb", db, m, list(range(len(m["acquisitions"]))))}
        common.stop(q.NAME)
        res[f"storage_parquet_{codec}"] = q.storage(volume, exclude_partition=time.strftime("%Y-%m-%dT%H", time.gmtime(sentinel_hour // 10**9)))
        if rep == 2:
            db = q.restart(volume, durability, codec)
            res["reads_parquet_ZSTD"] = reads("questdb", db, m, data)
            res["analytics_parquet_ZSTD"] = analytics("questdb", db, m, data)
        common.remove(q.NAME)
    elif system != "questdb" and measure:
        mo.settle(handle)
        res["storage"] = mo.storage(handle)
        if rep == 1 and system in ("morletdb", "morletdb-b30", "morletdb-031", "morletdb-031c16"):
            res["reads"] = reads("morletdb", handle, m, data)
            res["analytics"] = analytics("morletdb", handle, m, data)
            if system == "morletdb":
                res["exchange_file"] = packed_exchange()
        handle.close()
        common.stop(mo.NAME)
        files = common.volume_files(volume, mo.IMAGE)
        res["storage"]["volume"] = {"apparent": sum(f[0] for f in files), "allocated": sum(f[1] for f in files),
                                    "pg_wal_allocated": sum(f[1] for f in files if f[2].startswith("pg_wal/"))}
        common.remove(mo.NAME)
    else:
        common.remove(container)
    common.remove(container, volume)
    save(name, res)


def packed_exchange():
    """Packed baseline 1: the collection as a MorletDB exchange file (written to the scratch area, then verified)."""
    import morletdb_adapter as mo
    sys.path.insert(0, str(wl.ROOT / "tools/ingest"))
    import morlet_export as mx
    import tempfile
    from pathlib import Path
    out = Path(tempfile.mkdtemp(prefix="b3-")) / "b3.morlet.parquet"
    t = time.perf_counter()
    mx.export(mo.DSN, mo.COLLECTION, out)
    res = {"bytes": out.stat().st_size, "export_s": round(time.perf_counter() - t, 1)}
    res["verify"] = mx.verify(mo.DSN, mo.COLLECTION, out)
    res["verify"]["file"] = out.name
    out.unlink()
    return res


def _reader(system):
    import morletdb_adapter as mo
    import questdb_adapter as q
    if system == "questdb":
        h = q.connect()
        return lambda a, chans, off, n: q.read(h, a, chans, off, n)[1]
    h = mo.connect()
    return lambda a, chans, off, n: mo.read(h, a, chans, off, n)


def _same(x, y):
    return x.shape == y.shape and np.array_equal(x.view(np.int64), y.view(np.int64))


def reads(system, _handle, m, data, reps=50):
    plan = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    out = {}
    for rq, spec in plan.items():
        n, chans = spec["length"], spec["channels"]
        bad = 0
        if spec.get("threads"):
            lat, lock = [], threading.Lock()
            pairs = spec["pairs"]

            def worker(sub):
                rd, mine = _reader(system), []
                for k, off in sub:                     # warm-up pass
                    rd(acq[k], chans, off, n)
                for _ in range(reps):
                    for k, off in sub:
                        t = time.perf_counter()
                        v = rd(acq[k], chans, off, n)
                        mine.append(time.perf_counter() - t)
                    if not _same(v[chans[0]], data[k][chans[0]][off:off + n]):
                        raise RuntimeError("read mismatch")
                with lock:
                    lat.extend(mine)
            th = [threading.Thread(target=worker, args=(pairs[i::spec["threads"]],)) for i in range(spec["threads"])]
            t0 = time.perf_counter()
            for t in th:
                t.start()
            for t in th:
                t.join()
            wall = time.perf_counter() - t0
            out[rq] = {**common.pcts(lat), "threads": spec["threads"], "queries": len(lat),
                       "queries_per_s": round(len(lat) / wall, 1)}
            continue
        rd, lat = _reader(system), []
        for k, off in spec["pairs"]:
            v = rd(acq[k], chans, off, n)              # warm-up, checked
            bad += sum(not _same(v[c], data[k][c][off:off + n]) for c in chans)
            for _ in range(reps):
                t = time.perf_counter()
                v = rd(acq[k], chans, off, n)
                lat.append(time.perf_counter() - t)
            bad += sum(not _same(v[c], data[k][c][off:off + n]) for c in chans)
        out[rq] = {**common.pcts(lat), "samples_per_query": n * len(chans), "mismatches": bad}
        print(system, rq, out[rq], flush=True)
    return out


def analytics(system, _handle, m, data, reps=20):
    import morletdb_adapter as mo
    import questdb_adapter as q
    plan = common.read_plan(m)["R2"]
    acq = {a["k"]: a for a in m["acquisitions"]}
    h = q.connect() if system == "questdb" else mo.connect()
    f = q.analytics if system == "questdb" else mo.analytics
    lat, worst = [], 0.0
    for k, off in plan["pairs"]:
        x = data[k]["vibration_1"][off:off + plan["length"]]
        want = [x.min(), x.max(), x.mean(), np.sqrt(np.mean(x * x))]
        got = f(h, acq[k], off, plan["length"])
        if int(got[4]) != x.size:
            raise RuntimeError("analytics count mismatch")
        worst = max(worst, max(abs(g - w) / max(abs(w), 1e-300) for g, w in zip(got[:4], want)))
        for _ in range(reps):
            t = time.perf_counter()
            f(h, acq[k], off, plan["length"])
            lat.append(time.perf_counter() - t)
    return {**common.pcts(lat), "max_relative_difference": worst, "within_1e-12": worst <= 1e-12}


if __name__ == "__main__":
    {"subset": subset, "load": load}[sys.argv[1]](*sys.argv[2:])
