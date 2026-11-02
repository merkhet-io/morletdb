"""B3 correctness (PROTOCOL.md "Correctness"): counts, bit-exact samples, exact timestamps, identity, reads
across batches and retry semantics, for one system on a list of acquisitions already loaded."""
import numpy as np

import common
import workload as wl


def check_reads(system, handle, m, ks):
    """Every channel of every acquisition read back whole and compared by SHA-256 with the manifest."""
    acqs = [m["acquisitions"][k] for k in ks]
    out = {"acquisitions": len(acqs), "channels_checked": 0, "mismatches": []}
    for a in acqs:
        for g, spec in wl.GROUPS.items():
            n = a["channels"][spec["channels"][0]]["samples"]
            if system == "questdb":
                import questdb_adapter as q
                ts, vals = q.read(handle, a, spec["channels"], 0, n, group=g)
                want_ts = a["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
                if ts.shape != want_ts.shape or not np.array_equal(ts, want_ts):
                    out["mismatches"].append(f"{a['acquisition']} {g}: timestamps")
            else:
                import morletdb_adapter as mo
                vals = mo.read(handle, a, spec["channels"], 0, n)
                for c in spec["channels"]:
                    row = handle.execute(f"SELECT sample_rate_hz, channel, unit, metadata->>'acquisition', "
                                         f"(checkpoint->>'end')::bigint FROM {mo.COLLECTION} WHERE recording_id = %s",
                                         (mo.rid(a, c),)).fetchone()
                    if row != (spec["rate"], c, wl.UNITS[c], a["acquisition"], n):
                        out["mismatches"].append(f"{a['acquisition']} {c}: identity {row}")
            for c in spec["channels"]:
                out["channels_checked"] += 1
                if common.sha(vals[c]) != a["channels"][c]["sha256"]:
                    out["mismatches"].append(f"{a['acquisition']} {c}: samples")
    return out


def check_counts(system, handle, m, ks):
    want = {g: sum(m["acquisitions"][k]["channels"][s["channels"][0]]["samples"] for k in ks)
            for g, s in wl.GROUPS.items()}
    if system == "questdb":
        import questdb_adapter as q
        got = {g: q.count(handle, g) for g in wl.GROUPS}
    else:
        import morletdb_adapter as mo
        nrec, total = mo.count(handle)
        want_total = sum(want[g] * len(s["channels"]) for g, s in wl.GROUPS.items())
        return {"recordings": nrec, "expected_recordings": len(ks) * len(wl.UNITS), "samples": int(total),
                "expected_samples": want_total, "ok": nrec == len(ks) * len(wl.UNITS) and total == want_total}
    return {"rows": got, "expected_rows": want, "ok": got == want}


def sample_reads(system, handle, m, ks, rng):
    """Random sub-intervals crossing ingestion boundaries (MorletDB 4,096-sample chunks; for QuestDB the
    client batches are whole acquisitions, so intervals also cross partition hours where they exist)."""
    bad = []
    for _ in range(20):
        a = m["acquisitions"][int(rng.choice(ks))]
        n = a["channels"]["vibration_1"]["samples"]
        length = int(rng.integers(1, 70000))
        off = int(rng.integers(0, n - length))
        x = wl.load(wl.ROOT / a["path"])["vibration_1"][off:off + length]
        if system == "questdb":
            import questdb_adapter as q
            _, v = q.read(handle, a, ["vibration_1"], off, length)
        else:
            import morletdb_adapter as mo
            v = mo.read(handle, a, ["vibration_1"], off, length)
        if common.sha(v["vibration_1"]) != common.sha(x):
            bad.append((a["acquisition"], off, length))
    return {"intervals": 20, "mismatches": bad}
