#!/usr/bin/env python3
"""B3.2 maximum-throughput track: N writer processes, each with its own connection, for MorletDB and QuestDB
alike. Acquisitions are dealt round-robin to the writers; every writer parses its files before a common start
barrier (untimed); wall time runs from the barrier to the last acknowledgement, then until everything is
queryable. Full correctness (counts and every channel by SHA-256) after the load.

B3.20 adds CedarDB: each writer opens its own connection (B3.18's adapter: binary COPY per acquisition and group,
`SET async_commit = on` for D2), building its rows after the barrier as in B3.18, so conversion is counted. A CedarDB
load that fails the read-back keeps its container's volume (CEDARDB_RANGE_SCAN_FAULT.md).

  ../../.venv-b3/bin/python parallel.py <questdb|morletdb-032|morletdb-i1|cedardb> <D1|D2> <writers> <rep>
"""
import multiprocessing as mp
import sys
import time

import common
import run
import workload as wl


def writer(system, durability, ks, barrier, out_q):
    m = common.manifest()
    data = {k: wl.load(wl.ROOT / m["acquisitions"][k]["path"]) for k in ks}
    if system == "questdb":
        import questdb_adapter as q
        db = q.connect()
        load = lambda a: q.load_one(db, a, data[a["k"]])
    elif system == "cedardb":
        import cedar_adapter as c
        conn = c.connect(durability)
        load = lambda a: c.load_one(conn, a, data[a["k"]])
    else:
        import morletdb_adapter as mo
        mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                           "one_statement": True})
        if system == "morletdb-i1":   # B3.17: B3.10's client path (256 chunks per transaction, one connection per
            mo.VARIANT.update({"commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0})   # writer, batched)
        load = lambda a: mo.load_one(a, data[a["k"]])
    acks = []
    barrier.wait()
    for k in ks:
        t = time.perf_counter()
        load(m["acquisitions"][k])
        acks.append(time.perf_counter() - t)
    out_q.put((time.perf_counter(), acks))


def main(system, durability, writers, rep):
    writers, rep = int(writers), int(rep)
    m = common.manifest()
    n = len(m["acquisitions"])
    volume = f"b3-par-{system}-{durability.lower()}-w{writers}-r{rep}"
    if system == "questdb":
        import questdb_adapter as q
        handle = q.start(volume, durability)
        q.create(handle)
        container = q.NAME
    elif system == "cedardb":
        import cedar_adapter as c
        import run_cedar as rc
        handle = c.start(volume, durability)
        c.create(handle)
        container = c.NAME
    else:
        import morletdb_adapter as mo
        handle = mo.start(volume, durability)
        mo.create(handle)
        container = mo.NAME
    ctx = mp.get_context("spawn")
    barrier, out_q = ctx.Barrier(writers + 1), ctx.Queue()
    procs = [ctx.Process(target=writer, args=(system, durability, list(range(w, n, writers)), barrier, out_q))
             for w in range(writers)]
    for p in procs:
        p.start()
    with common.Stats(container) as st:
        barrier.wait(timeout=900)   # B3.20: a writer that fails before the start no longer hangs the run
        t0 = time.perf_counter()
        ends, acks = [], []
        for _ in procs:
            end, a = out_q.get(timeout=3600)   # B3.20: nor one that fails during the load
            ends.append(end)
            acks += a
        t_last = max(ends)
        if system == "questdb":
            run.q_settle(handle, run.expected_rows(m), timeout=3600)
        elif system == "cedardb":
            rc.settle(handle, rc.expected(m, list(range(n))))
        else:
            want = run.total_samples(m)
            while mo.count(handle)[1] != want:
                time.sleep(0.05)
        queryable = time.perf_counter() - t_last
    for p in procs:
        p.join()
    wall = t_last - t0
    res = {"system": system, "durability": durability, "writers": writers, "rep": rep,
           "samples": run.total_samples(m),
           "ingest": {"wall_s": round(wall, 3), "samples_per_s": round(run.total_samples(m) / wall),
                      "ack_per_acquisition": common.pcts(acks), "queryable_after_last_ack_s": round(queryable, 3),
                      "server": st.summary()}}
    if system == "cedardb":
        res["image"] = c.IMAGE
        res["image_id"] = common.sh(["docker", "image", "inspect", c.IMAGE, "--format", "{{.Id}}"]).strip()
        res["settings"] = c.server_settings(handle)
        ks = list(range(n))
        got, want = c.count(handle), rc.expected(m, ks)
        t = time.perf_counter()
        res["correctness"] = {"counts": {"got": got, "want": want, "ok": got == want}, "reads": rc.check_reads(handle, m, ks)}
        res["correctness"]["reads"]["seconds"] = round(time.perf_counter() - t, 1)
        handle.close()
    elif system != "questdb":
        res["image"] = mo.IMAGE
        res["image_id"] = common.sh(["docker", "image", "inspect", mo.IMAGE, "--format", "{{.Id}}"]).strip()
    if system != "cedardb":
        res["correctness"] = run.full_counts_and_reads("questdb" if system == "questdb" else "morletdb", handle, m)
    res["correct"] = res["correctness"]["counts"]["ok"] and not res["correctness"]["reads"]["mismatches"]
    if system == "cedardb" and not res["correct"]:
        common.stop(container)
        res["volume_kept"] = volume   # evidence, as for B3.19 round 3
    else:
        common.remove(container, volume)
    run.save(f"parallel_{system}_{durability}_w{writers}_r{rep}", res)


if __name__ == "__main__":
    main(*sys.argv[1:])
