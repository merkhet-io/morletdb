#!/usr/bin/env python3
"""B5 summary (PROTOCOL.md section 5): S1-S6 from the results, as figures and verdicts.

  python3 evaluation/storage_b5/summarize_b5.py [RESULTS_DIR] > evaluation/storage_b5/results/b5_summary.json

Statement rule (B3's): ingestion, a statement when the difference is at least 10% in all three loads: the all-loads
bound (slowest load of the faster arm against the fastest load of the other) is at least 1.10; the figure reported is
the ratio of medians. Reads R1-R4 and interval statistics: at least 10% at both p50 and p95. R5 (4 threads):
queries per second, a single measure, as B3.16 reported it (p50 and p95 are listed too). Ratios are written as
"other / this arm" for latencies and "this arm / other" for rates, so > 1 means this arm is better.
"""
import json
import statistics
import sys
from pathlib import Path

R = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "results"
LARGE = ["SSD-bench", "Bird-migration", "Basel-temp", "Basel-wind", "Food-price"]
WAVES = ["cx", "ims", "pad-c", "pad-p", "sleep", "ecg"]
READS = ["R1", "R2", "R3", "R4_recent", "R4_older"]
ARMS = {"g2": "grizzbit2", "rel": "release", "q": "questdb"}
T = 1.10


def load_json(p):
    return json.loads(p.read_text()) if p.is_file() else None


# ---------------------------------------------------------------- storage and codec speed
def storage():
    s = load_json(R / "b5_storage.json")
    if s is None:
        return {"missing": True}
    rows = {r["input"]: r for r in s["inputs"]}
    bench = [r for r in s["inputs"] if r["set"] in ("waveform", "elf")]
    out = {"inputs_measured": len(bench), "stopped": s.get("stopped"),
           "g1_matches_b4": {r["input"]: r["b4"]["G1_matches_M_auto"] for r in s["inputs"] if "b4" in r},
           "inputs_match_b4": {r["input"]: r["b4"]["sha256_matches"] and r["b4"]["samples_match"]
                               for r in s["inputs"] if "b4" in r}}
    s1 = all(r["exact"] and r["bench_example_agrees"] for r in s["inputs"])
    s2 = {n: rows[n]["ratios"]["G2_over_CH_best"] for n in LARGE if n in rows}
    others = [r["input"] for r in bench if r["input"] not in LARGE and r["input"] != "Bitcoin-price"]
    s3 = {n: rows[n]["ratios"]["G2_over_CH_best"] for n in others}
    btc = rows.get("Bitcoin-price")
    s4 = {r["input"]: {"changed": r["chunks_changed"], "of": r["chunks"], "violations": len(r["s4_violations"]),
                       "changed_by_G1_kind": r["changed_by_G1_kind"]} for r in s["inputs"]}
    out["S1_storage_exact"] = s1
    out["S2"] = {"ratios": s2, "holds": len(s2) == 5 and all(v <= 1.25 for v in s2.values())}
    out["S3"] = {"ratios": s3, "bitcoin_price": None if btc is None else {
        "G1_bits": btc["bits_per_sample"]["G1"], "G2_bits": btc["bits_per_sample"]["G2"],
        "G2_over_CH_best": btc["ratios"]["G2_over_CH_best"], "G2_le_G1": btc["bytes"]["G2"] <= btc["bytes"]["G1"]},
        "holds": len(s3) == 18 and all(v <= T for v in s3.values()) and btc is not None
        and btc["bytes"]["G2"] <= btc["bytes"]["G1"],
        "failing": {k: v for k, v in s3.items() if v > T}}
    out["S4"] = {"per_input": s4, "holds": all(v["violations"] == 0 for k, v in s4.items()
                                               if rows[k]["set"] in ("waveform", "elf"))}
    out["table"] = [{"input": r["input"], "set": r["set"], "samples": r["samples"], "chunks": r["chunks"],
                     "G1": r["bits_per_sample"]["G1"], "G2": r["bits_per_sample"]["G2"],
                     "CH_best": r.get("b4", {}).get("CH_best_bits"), "CH_best_arm": r.get("b4", {}).get("CH_best_arm"),
                     "PCO": r.get("b4", {}).get("PCO_bits"), **r.get("ratios", {}),
                     "kinds_G1": r["chunk_kinds"]["G1"], "kinds_G2": r["chunk_kinds"]["G2"],
                     "changed": r["chunks_changed"]} for r in s["inputs"]]
    return out


def speed():
    s = load_json(R / "b5_speed.json")
    if s is None:
        return {"missing": True}
    rows = {r["input"]: r for r in s["inputs"]}
    dec = {w: rows[w]["G2"]["decode_msamples_s"] for w in WAVES if w in rows}
    return {"stopped": s.get("stopped"), "S5": {"G2_decode_waveforms": dec,
                                                "holds": len(dec) == 6 and all(v >= 50 for v in dec.values())},
            "table": [{"input": r["input"], "set": r["set"], "G1_enc": r["G1"]["encode_msamples_s"],
                       "G1_dec": r["G1"]["decode_msamples_s"], "G2_enc": r["G2"]["encode_msamples_s"],
                       "G2_dec": r["G2"]["decode_msamples_s"], "enc_ratio": r["G2_over_G1_encode"],
                       "dec_ratio": r["G2_over_G1_decode"], "load1": [r["load1_before"], r["load1_after"]]}
                      for r in s["inputs"]]}


# ---------------------------------------------------------------- B3
def files(track, arm):
    d = R / ("b3-single" if track == "S" else "b3-parallel") / ARMS[arm]
    out = {}
    for p in sorted(d.glob("*.json")):
        if ".failed-" in p.name:
            continue
        out[p.stem] = json.loads(p.read_text())
    return out


def name(track, arm, d, r):
    sysname = "questdb" if arm == "q" else ("morletdb-i2" if track == "S" else "morletdb-i1")
    return f"load_{sysname}_{d}_r{r}" if track == "S" else f"parallel_{sysname}_{d}_w8_r{r}"


def rates(track, arm, d):
    fs = files(track, arm)
    out = []
    for r in (1, 2, 3):
        f = fs.get(name(track, arm, d, r))
        out.append(f["ingest"]["samples_per_s"] if f and f.get("correct") else None)
    return out


def ingest_statement(a, b):
    """a, b: three rates each (higher is better). Returns (statement, ratio of medians, bound)."""
    if None in a or None in b:
        return "incomplete", None, None
    med = statistics.median(a) / statistics.median(b)
    if min(a) / max(b) >= T:
        return "faster", round(med, 3), round(min(a) / max(b), 3)
    if min(b) / max(a) >= T:
        return "slower", round(med, 3), round(max(a) / min(b), 3)
    return "none", round(med, 3), None


def lat_statement(a, b):
    """a, b: (p50, p95) in ms (lower is better). Ratios b/a: > 1 means a is faster."""
    r50, r95 = b[0] / a[0], b[1] / a[1]
    st = "faster" if r50 >= T and r95 >= T else "slower" if 1 / r50 >= T and 1 / r95 >= T else "none"
    return st, round(r50, 3), round(r95, 3)


def reads_of(arm):
    f = files("S", arm).get(name("S", arm, "D2", 1))
    if not f:
        return None
    rd = f.get("reads_native") if arm == "q" else f.get("reads")
    return {"reads": rd, "analytics": f.get("analytics"), "load": f}


def measures(a, b):
    """Every measure of tracks S and P: arm a against arm b."""
    out = {}
    for track in ("S", "P"):
        for d in ("D2", "D1"):
            ra, rb = rates(track, a, d), rates(track, b, d)
            st, med, bound = ingest_statement(ra, rb)
            out[f"ingest_{track}_{d}"] = {"statement": st, "ratio_of_medians": med, "bound": bound,
                                          "rates": {a: ra, b: rb}}
    xa, xb = reads_of(a), reads_of(b)
    if not xa or not xb or not xa["reads"] or not xb["reads"]:
        out["reads"] = "missing"
        return out
    for rq in READS:
        pa, pb = xa["reads"][rq], xb["reads"][rq]
        st, r50, r95 = lat_statement((pa["p50_ms"], pa["p95_ms"]), (pb["p50_ms"], pb["p95_ms"]))
        out[rq] = {"statement": st, "p50_ratio": r50, "p95_ratio": r95,
                   "ms": {a: [pa["p50_ms"], pa["p95_ms"]], b: [pb["p50_ms"], pb["p95_ms"]]}}
    qa, qb = xa["reads"]["R5"]["queries_per_s"], xb["reads"]["R5"]["queries_per_s"]
    r = qa / qb
    out["R5"] = {"statement": "faster" if r >= T else "slower" if 1 / r >= T else "none", "qps_ratio": round(r, 3),
                 "qps": {a: qa, b: qb}, "ms": {a: [xa["reads"]["R5"]["p50_ms"], xa["reads"]["R5"]["p95_ms"]],
                                               b: [xb["reads"]["R5"]["p50_ms"], xb["reads"]["R5"]["p95_ms"]]}}
    sa, sb = xa["analytics"], xb["analytics"]
    st, r50, r95 = lat_statement((sa["p50_ms"], sa["p95_ms"]), (sb["p50_ms"], sb["p95_ms"]))
    out["statistics"] = {"statement": st, "p50_ratio": r50, "p95_ratio": r95,
                         "ms": {a: [sa["p50_ms"], sa["p95_ms"]], b: [sb["p50_ms"], sb["p95_ms"]]}}
    return out


def b3():
    loads, bad = {}, []
    for track in ("S", "P"):
        for arm in ARMS:
            for d in ("D2", "D1"):
                for r in (1, 2, 3):
                    f = files(track, arm).get(name(track, arm, d, r))
                    key = f"{track} {ARMS[arm]} {d} r{r}"
                    loads[key] = None if f is None else bool(f.get("correct"))
                    if not f or not f.get("correct"):
                        bad.append(key)
    failed_attempts = sorted(str(p.relative_to(R)) for p in R.glob("b3-*/*/*.failed-*.json"))
    read_mismatch = {}
    for arm in ARMS:
        x = reads_of(arm)
        if x and x["reads"]:
            read_mismatch[arm] = sum(v.get("mismatches", 0) for v in x["reads"].values() if isinstance(v, dict))
    rel_q, g2_q, g2_rel = measures("rel", "q"), measures("g2", "q"), measures("g2", "rel")
    a_fail = []
    for k, v in rel_q.items():
        if not isinstance(v, dict):
            continue
        s_rel, s_g2 = v["statement"], g2_q[k]["statement"]
        if s_rel in ("faster", "slower") and s_g2 != s_rel:
            a_fail.append(f"{k}: release {s_rel}, Grizzbit 2 {s_g2}")
        if s_rel == "none" and s_g2 == "slower":
            a_fail.append(f"{k}: Grizzbit 2 has QuestDB faster where the release has no statement")
        if "incomplete" in (s_rel, s_g2):
            a_fail.append(f"{k}: incomplete ({s_rel}, {s_g2})")
    b_fail = [f"{k}: Grizzbit 2 slower than the release" for k, v in g2_rel.items()
              if isinstance(v, dict) and not k.startswith("ingest") and v["statement"] == "slower"]
    cost = {k: v for k, v in g2_rel.items() if k.startswith("ingest") and isinstance(v, dict)}
    server = {}
    for track in ("S", "P"):
        for arm in ARMS:
            for d in ("D2", "D1"):
                cells = []
                for r in (1, 2, 3):
                    f = files(track, arm).get(name(track, arm, d, r))
                    if f:
                        sv = f["ingest"].get("server", {})
                        cells.append({"rep": r, "samples_per_s": f["ingest"]["samples_per_s"],
                                      "cpu_mean": sv.get("cpu_percent_mean"), "mem_peak_mib": sv.get("memory_peak_mib"),
                                      "kernel_peak_mib": (sv.get("kernel_memory") or {}).get("peak_mib")})
                server[f"{track} {ARMS[arm]} {d}"] = cells
    storage_b3 = {}
    for arm in ("g2", "rel", "q"):
        x = reads_of(arm)
        if x:
            f = x["load"]
            storage_b3[ARMS[arm]] = {k: f.get(k) for k in ("storage", "storage_native", "native_lib", "image_id")
                                     if k in f}
    return {"loads_correct": loads, "void_or_missing": bad, "failed_attempts_kept": failed_attempts,
            "read_mismatches_D2r1": read_mismatch,
            "release_vs_questdb": rel_q, "grizzbit2_vs_questdb": g2_q, "grizzbit2_vs_release": g2_rel,
            "S6a": {"holds": not a_fail, "failures": a_fail}, "S6b": {"holds": not b_fail, "failures": b_fail},
            "ingestion_grizzbit2_vs_release": cost, "server_per_cell": server, "storage_D2r1": storage_b3}


def main():
    st, sp, b = storage(), speed(), b3()
    s1 = st.get("S1_storage_exact") is True and not b["void_or_missing"] and \
        all(v == 0 for v in b["read_mismatches_D2r1"].values()) and len(b["read_mismatches_D2r1"]) == 3
    verdict = {"S1": s1, "S2": st.get("S2", {}).get("holds"), "S3": st.get("S3", {}).get("holds"),
               "S4": st.get("S4", {}).get("holds"), "S5": sp.get("S5", {}).get("holds"),
               "S6": b["S6a"]["holds"] and b["S6b"]["holds"]}
    verdict["passes"] = all(v is True for v in verdict.values())
    print(json.dumps({"verdict": verdict, "storage": st, "speed": sp, "b3": b}, indent=1))


if __name__ == "__main__":
    main()
