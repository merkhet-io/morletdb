#!/usr/bin/env python3
"""B6 summary (PROTOCOL.md section 5): S1-S6 from the results, as figures and verdicts.

  python3 -I evaluation/storage_b6/summarize_b6.py [RESULTS_DIR] [--md] > ...

RESULTS_DIR holds b6_storage.json, b6_speed.json, b6_platforms.json, b3-single/ and b3-parallel/ (default: results/).
JSON by default; --md prints the verdict and the per-criterion tables as Markdown. Development series (elf-dev, and
B4's unfiltered Paderborn inputs, waveform-b4) are measured in the same run and listed apart, never counted in a
criterion. A results directory from the harness test (set "dev") is labelled DEVELOPMENT DRY RUN.

Criteria (frozen, section 5):
- S1: every chunk of every input exact (both programs agree), every B3 load correct, no read mismatch.
- S2: over the six waveforms, total G21 bytes <= 0.95 x total G1 bytes, and G21 < G1 on at least 4 of the 6.
- S3: on every one of the 30 inputs, every G21 chunk that differs from G1's is a version-5 chunk with strictly fewer
      bytes, every other chunk is byte-identical, and G21 <= G1.
- S4: on each of the six waveforms and the six B3 machine channels, single thread, G21 decode >= 50 M samples/s and
      G21 encode >= 10 M samples/s.
- S5: the per-input hashes of section 4B identical on every platform measured (macOS/arm64, Linux/arm64,
      Linux/x86_64 are required; the Raspberry Pi's count when present).
- S6: (a) every statement the release arm makes against QuestDB, Grizzbit 2.1 makes in the same direction; (b) no
      statement that Grizzbit 2.1 reads or computes interval statistics slower than the release (B3's statement
      rule: 10% in all three loads for ingestion, at p50 and p95 for reads and statistics).
A criterion whose inputs are incomplete does not hold.
"""
import json
import statistics
import sys
from pathlib import Path

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
R = Path(ARGS[0]) if ARGS else Path(__file__).resolve().parent / "results"
MD = "--md" in sys.argv
BENCH_SETS = ("waveform", "elf", "b3")
DEV_SETS = ("elf-dev", "waveform-b4")
REQUIRED_PLATFORMS = ("macos-arm64", "linux-arm64", "linux-amd64")
READS = ["R1", "R2", "R3", "R4_recent", "R4_older"]
ARMS = {"g21": "grizzbit21", "rel": "release", "q": "questdb"}
T = 1.10


def load_json(p):
    return json.loads(p.read_text()) if p.is_file() else None


# ---------------------------------------------------------------- storage (S1 part, S2, S3)
def storage():
    s = load_json(R / "b6_storage.json")
    if s is None:
        return {"missing": True}
    rows = s["inputs"]
    bench = [r for r in rows if r["set"] in BENCH_SETS]
    waves = [r for r in bench if r["set"] == "waveform"]
    g1, g21 = sum(r["bytes"]["G1"] for r in waves), sum(r["bytes"]["G21"] for r in waves)
    smaller = [r["input"] for r in waves if r["bytes"]["G21"] < r["bytes"]["G1"]]
    s2 = {"waveforms": len(waves), "G1_bytes": g1, "G21_bytes": g21,
          "G21_over_G1_total": round(g21 / g1, 4) if g1 else None, "smaller_on": smaller,
          "holds": len(waves) == 6 and g21 <= 0.95 * g1 and len(smaller) >= 4}
    s3_inputs = {r["input"]: {"violations": len(r["s3_violations"]), "G21_le_G1": r["bytes"]["G21"] <= r["bytes"]["G1"],
                              "changed": r["chunks_changed"], "of": r["chunks"]} for r in bench}
    s3 = {"inputs": len(bench), "per_input": s3_inputs,
          "failing": [k for k, v in s3_inputs.items() if v["violations"] or not v["G21_le_G1"]],
          "holds": len(bench) == 30 and all(v["violations"] == 0 and v["G21_le_G1"] for v in s3_inputs.values())}
    g1_ref = {r["input"]: {"B4": r.get("b4", {}).get("G1_matches_M_auto"), "B5": r.get("b5", {}).get("G1_matches")}
              for r in rows if "b4" in r or "b5" in r}
    table = []
    for r in rows:
        b4, b5 = r.get("b4", {}), r.get("b5", {})
        table.append({"input": r["input"], "set": r["set"], "development": r["set"] in DEV_SETS,
                      "samples": r["samples"], "chunks": r["chunks"], "G1": r["bits_per_sample"]["G1"],
                      "G21": r["bits_per_sample"]["G21"], "G21_over_G1": r["G21_over_G1"],
                      "changed": r["chunks_changed"], "kinds_G21": r["chunk_kinds"]["G21"],
                      "version5_paths": r["version5_paths"], "CH_best": b4.get("CH_best_bits"),
                      "CH_best_arm": b4.get("CH_best_arm"), "PCO": b4.get("PCO_bits"),
                      "G2_B5": b5.get("G2_bits")})
    return {"set": s["set"], "stopped": s.get("stopped"), "inputs_measured": len(rows),
            "S1_storage_exact": all(r["exact"] and r["bench_example_agrees"] for r in rows) and not s.get("stopped"),
            "G1_matches_references": g1_ref, "S2": s2, "S3": s3, "table": table}


# ---------------------------------------------------------------- speed (S4)
def speed():
    s = load_json(R / "b6_speed.json")
    if s is None:
        return {"missing": True}
    rows = {r["input"]: r for r in s["inputs"]}
    crit = {r["input"]: {"decode": r["G21"]["decode_msamples_s"], "encode": r["G21"]["encode_msamples_s"]}
            for r in s["inputs"] if r["set"] in ("waveform", "b3")}
    failing = {k: v for k, v in crit.items() if v["decode"] < 50 or v["encode"] < 10}
    return {"set": s["set"], "dryrun": s.get("dryrun"), "stopped": s.get("stopped"), "not_run": s.get("not_run", []),
            "S4": {"inputs": len(crit), "per_input": crit, "failing": failing,
                   "holds": len(crit) == 12 and not failing and not s.get("not_run")},
            "table": [{"input": r["input"], "set": r["set"], "G1_enc": r["G1"]["encode_msamples_s"],
                       "G1_dec": r["G1"]["decode_msamples_s"], "G21_enc": r["G21"]["encode_msamples_s"],
                       "G21_dec": r["G21"]["decode_msamples_s"], "enc_ratio": r["G21_over_G1_encode"],
                       "dec_ratio": r["G21_over_G1_decode"],
                       "load1": [c["load1"] for c in r["conditions"]]} for r in rows.values()]}


# ---------------------------------------------------------------- platforms (S5)
def platforms():
    p = load_json(R / "b6_platforms.json")
    if p is None:
        return {"missing": True}
    measured = [x for x in p["platforms"] if not p["complete"].get(x)]
    missing = [x for x in REQUIRED_PLATFORMS if x not in measured]
    return {"platforms": p["platforms"], "platform_status": p.get("platform_status", {}),
            "incomplete": {k: v for k, v in p["complete"].items() if v}, "differing_inputs": p["differing_inputs"],
            "S5": {"required_missing": missing, "holds": not missing and not p["differing_inputs"]},
            "edge_speeds": p.get("edge_speeds", {})}


# ---------------------------------------------------------------- B3 (S1 part, S6), as B5's summary
def files(track, arm):
    d = R / ("b3-single" if track == "S" else "b3-parallel") / ARMS[arm]
    return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json")) if ".failed-" not in p.name}


def name(track, arm, d, r):
    sysname = "questdb" if arm == "q" else ("morletdb-i2" if track == "S" else "morletdb-i1")
    return f"load_{sysname}_{d}_r{r}" if track == "S" else f"parallel_{sysname}_{d}_w8_r{r}"


def rates(track, arm, d):
    fs = files(track, arm)
    return [(f["ingest"]["samples_per_s"] if (f := fs.get(name(track, arm, d, r))) and f.get("correct") else None)
            for r in (1, 2, 3)]


def ingest_statement(a, b):
    if None in a or None in b:
        return "incomplete", None, None
    med = statistics.median(a) / statistics.median(b)
    if min(a) / max(b) >= T:
        return "faster", round(med, 3), round(min(a) / max(b), 3)
    if min(b) / max(a) >= T:
        return "slower", round(med, 3), round(max(a) / min(b), 3)
    return "none", round(med, 3), None


def lat_statement(a, b):
    r50, r95 = b[0] / a[0], b[1] / a[1]
    st = "faster" if r50 >= T and r95 >= T else "slower" if 1 / r50 >= T and 1 / r95 >= T else "none"
    return st, round(r50, 3), round(r95, 3)


def reads_of(arm):
    f = files("S", arm).get(name("S", arm, "D2", 1))
    if not f:
        return None
    return {"reads": f.get("reads_native") if arm == "q" else f.get("reads"), "analytics": f.get("analytics"), "load": f}


def measures(a, b, tracks=(("S", ("D2", "D1")), ("P", ("D2", "D1")))):
    out = {}
    for track, ds in tracks:
        for d in ds:
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
    out["R5"] = {"statement": "faster" if qa / qb >= T else "slower" if qb / qa >= T else "none",
                 "qps_ratio": round(qa / qb, 3), "qps": {a: qa, b: qb}}
    sa, sb = xa["analytics"], xb["analytics"]
    st, r50, r95 = lat_statement((sa["p50_ms"], sa["p95_ms"]), (sb["p50_ms"], sb["p95_ms"]))
    out["statistics"] = {"statement": st, "p50_ratio": r50, "p95_ratio": r95}
    return out


def b3(smoke=False):
    reps = (1,) if smoke else (1, 2, 3)
    ds = ("D2",) if smoke else ("D2", "D1")
    loads, bad = {}, []
    for track in ("S", "P"):
        for arm in ARMS:
            for d in ds:
                for r in reps:
                    f = files(track, arm).get(name(track, arm, d, r))
                    key = f"{track} {ARMS[arm]} {d} r{r}"
                    loads[key] = None if f is None else bool(f.get("correct"))
                    if not f or not f.get("correct"):
                        bad.append(key)
    mism = {}
    for arm in ARMS:
        x = reads_of(arm)
        if x and x["reads"]:
            mism[arm] = sum(v.get("mismatches", 0) for v in x["reads"].values() if isinstance(v, dict))
    fmt = {}
    for track in ("S", "P"):
        for k, f in files(track, "g21").items():
            fmt[f"{track} {k}"] = (f.get("storage") or {}).get("b6_chunk_format") or f.get("b6_chunk_format")
    out = {"loads_correct": loads, "void_or_missing": bad, "read_mismatches_D2r1": mism,
           "grizzbit21_chunk_format": fmt,
           "failed_attempts_kept": sorted(str(p.relative_to(R)) for p in R.glob("b3-*/*/*.failed-*.json"))}
    if smoke:
        return out
    rel_q, g_q, g_rel = measures("rel", "q"), measures("g21", "q"), measures("g21", "rel")
    a_fail = []
    for k, v in rel_q.items():
        if not isinstance(v, dict):
            continue
        s_rel, s_g = v["statement"], g_q[k]["statement"]
        if s_rel in ("faster", "slower") and s_g != s_rel:
            a_fail.append(f"{k}: release {s_rel}, Grizzbit 2.1 {s_g}")
        if s_rel == "none" and s_g == "slower":
            a_fail.append(f"{k}: Grizzbit 2.1 has QuestDB faster where the release has no statement")
        if "incomplete" in (s_rel, s_g):
            a_fail.append(f"{k}: incomplete ({s_rel}, {s_g})")
    if "reads" in rel_q or "reads" in g_q:
        a_fail.append("reads missing")
    b_fail = [f"{k}: Grizzbit 2.1 slower than the release" for k, v in g_rel.items()
              if isinstance(v, dict) and not k.startswith("ingest") and v["statement"] == "slower"]
    if "reads" in g_rel:
        b_fail.append("reads missing")
    cost = {k: v for k, v in g_rel.items() if k.startswith("ingest") and isinstance(v, dict)}
    out.update({"release_vs_questdb": rel_q, "grizzbit21_vs_questdb": g_q, "grizzbit21_vs_release": g_rel,
                "S6a": {"holds": not a_fail, "failures": a_fail}, "S6b": {"holds": not b_fail, "failures": b_fail},
                "ingestion_grizzbit21_vs_release": cost,
                "ingestion_cost_in_all_loads": [k for k, v in cost.items() if v["statement"] == "slower"]})
    return out


def main():
    st, sp, pl = storage(), speed(), platforms()
    start = R / "b3-single/b6_b3.start"
    b = b3(smoke=start.is_file() and "mode smoke" in start.read_text())   # the dry run's smoke check
    dry = st.get("set") == "dev" or sp.get("dryrun")
    s1 = st.get("S1_storage_exact") is True and not b["void_or_missing"] and \
        all(v == 0 for v in b["read_mismatches_D2r1"].values()) and len(b["read_mismatches_D2r1"]) == 3
    verdict = {"S1": s1, "S2": st.get("S2", {}).get("holds"), "S3": st.get("S3", {}).get("holds"),
               "S4": sp.get("S4", {}).get("holds"), "S5": pl.get("S5", {}).get("holds"),
               "S6": (b.get("S6a", {}).get("holds") and b.get("S6b", {}).get("holds")) if "S6a" in b else None}
    verdict["passes"] = all(v is True for v in verdict.values())
    out = {"label": "DEVELOPMENT DRY RUN: not results" if dry else "B6 results", "verdict": verdict,
           "storage": st, "speed": sp, "platforms": pl, "b3": b}
    print(markdown(out) if MD else json.dumps(out, indent=1))


def fmt(v, nd=3):
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def markdown(o):
    v, st, sp, pl, b = o["verdict"], o["storage"], o["speed"], o["platforms"], o["b3"]
    lines = [f"# B6 summary ({o['label']})", "", "| Criterion | Holds |", "|---|---|"]
    lines += [f"| {k} | {fmt(v[k])} |" for k in ("S1", "S2", "S3", "S4", "S5", "S6", "passes")]
    if not st.get("missing"):
        s2 = st["S2"]
        lines += ["", f"**S2:** six waveforms G21/G1 total {fmt(s2['G21_over_G1_total'], 4)}; smaller on "
                      f"{len(s2['smaller_on'])} of {s2['waveforms']}.", "",
                  "## Storage (bits/sample; development series marked)", "",
                  "| Input | Set | G1 | G21 | G21/G1 | Changed | v5: table / exc. / per sample / none / LPC | CH best | PCO | G2 (B5) |",
                  "|---|---|---:|---:|---:|---:|---|---:|---:|---:|"]
        for r in st["table"]:
            p = r["version5_paths"]
            lines.append(f"| {r['input']}{' (development)' if r['development'] else ''} | {r['set']} | "
                         f"{fmt(r['G1'])} | {fmt(r['G21'])} | {fmt(r['G21_over_G1'], 4)} | {r['changed']}/{r['chunks']} | "
                         f"{p['table']} / {p['exceptions only']} / {p['per sample']} / {p['none']} / {p['LPC']} | "
                         f"{fmt(r['CH_best'])} | {fmt(r['PCO'])} | {fmt(r['G2_B5'])} |")
    if not sp.get("missing"):
        lines += ["", "## Codec speed (M samples/s, single thread, best of 3)", "",
                  "| Input | Set | G1 enc | G1 dec | G21 enc | G21 dec | enc ratio | dec ratio | load (passes) |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for r in sp["table"]:
            lines.append(f"| {r['input']} | {r['set']} | {fmt(r['G1_enc'], 1)} | {fmt(r['G1_dec'], 1)} | "
                         f"{fmt(r['G21_enc'], 1)} | {fmt(r['G21_dec'], 1)} | {fmt(r['enc_ratio'])} | "
                         f"{fmt(r['dec_ratio'])} | {', '.join(fmt(x, 2) for x in r['load1'])} |")
    if not pl.get("missing"):
        lines += ["", f"## Platforms: {', '.join(pl['platforms'])}; differing inputs: "
                      f"{', '.join(pl['differing_inputs']) or 'none'}; required missing: "
                      f"{', '.join(pl['S5']['required_missing']) or 'none'}", ""]
        lines += [f"- {k}: {s}" for k, s in pl["platform_status"].items()]
    lines += ["", "## B3", "", f"Loads void or missing: {', '.join(b['void_or_missing']) or 'none'}; read mismatches "
                                f"(D2 r1): {b['read_mismatches_D2r1']}; Grizzbit 2.1 chunk format: "
                                f"{sorted({json.dumps(x) for x in b['grizzbit21_chunk_format'].values()})}"]
    for key in ("S6a", "S6b"):
        if key in b:
            lines.append(f"- {key}: {b[key]['holds']} {'; '.join(b[key]['failures'])}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
