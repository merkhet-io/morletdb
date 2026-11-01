#!/usr/bin/env python3
"""Generate the paper's main E1 numbers from stored results (no database, standard library only).

Inputs (all committed):
  evaluation/consolidated/results/e1_heldout.json, e1_dev.json   E1, build 68594d7, protocol 34904f6 (deviations 1-3)
  evaluation/consolidated/results/sizes_heldout.csv, sizes_dev.csv  per-structure sizes, read after E1 (sizes.sql)

Outputs (generated; do not edit by hand):
  research/paper/generated/numbers.tex     \\newcommand macros used in the manuscript
  research/paper/generated/tab_e1.tex      Table "E1, held-out data"
  research/paper/generated/tab_ablation.tex Table "recall by candidate generator"
  research/paper/generated/summary.json    the validated summary all of the above are written from
  research/paper/README.md                 the block between <!-- results:start --> and <!-- results:end -->

  python3 research/paper/gen_numbers.py           # regenerate
  python3 research/paper/gen_numbers.py --check   # exit 1 if any generated file is stale (used in CI)

Only E1 (current build) feeds these outputs. Historic numbers (BL1-C, R2-D, SS1, SS3, load runs) stay as written
text in the manuscript and are labelled there. Automatic numbers do not make a conclusion right: each statement
that uses them is checked by hand.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "evaluation/consolidated/results"
GEN = ROOT / "research/paper/generated"
README = ROOT / "research/paper/README.md"
REPORT_VERSION = ROOT / "research/paper/REPORT_VERSION"
ARMS_TIMED = ["EX-2", "EX-8", "PR", "Z-650", "Z-1000"]
ARMS_OFFLINE = ["PX-61", "PX-13", "PQ-13", "PX-7", "EZ"]
TOP = 10


class InputError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise InputError(msg)


def load_e1(name):
    path = RES / f"e1_{name}.json"
    need(path.exists(), f"missing input {path}")
    d = json.loads(path.read_text())
    for key in ("per_query", "summary", "environment", "gallery_windows"):
        need(key in d, f"{path.name}: field '{key}' missing")
    per = d["per_query"]
    need(len(per) == 100, f"{path.name}: expected 100 queries, found {len(per)}")
    for q, rec in per.items():
        ref = rec["top"].get("EX-2")
        need(ref is not None and len(ref) == TOP, f"{path.name}: query {q} has no exact reference of {TOP} rows")
        for arm in ARMS_TIMED + ARMS_OFFLINE:
            need(arm in rec["recall"], f"{path.name}: query {q} lacks recall for arm {arm}")
            got = {tuple(x) for x in rec["top"][arm]}
            recomputed = len(got & {tuple(x) for x in ref}) / TOP
            need(abs(recomputed - rec["recall"][arm]) < 1e-12,
                 f"{path.name}: query {q} arm {arm}: stored recall {rec['recall'][arm]} != recomputed {recomputed}")
    for arm in ARMS_TIMED:
        lat = d["summary"][arm].get("latency_ms", {})
        need(lat.get("n") == 200, f"{path.name}: arm {arm} should have 200 timings, found {lat.get('n')}")
    need(d["environment"]["build_commit"].startswith("68594d7"), f"{path.name}: unexpected build {d['environment']['build_commit']}")
    return d


def load_sizes(name):
    path = RES / f"sizes_{name}.csv"
    need(path.exists(), f"missing input {path}")
    sizes = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            sizes[row["role"]] = sizes.get(row["role"], 0) + int(row["bytes"])
    for role in ("raw", "envelopes", "probe", "probe_index"):
        need(role in sizes, f"{path.name}: no relation with role '{role}'")
    return sizes


def summarize(d, sizes):
    per = d["per_query"]
    keys = list(per)
    out = {"queries": len(keys), "denominator": TOP, "gallery_windows": d["gallery_windows"],
           "types": {t: sum(per[k]["type"] == t for k in keys) for t in ("random", "energetic")}, "arms": {}}
    need(out["types"] == {"random": 50, "energetic": 50}, f"query types {out['types']} != 50/50")
    for arm in ARMS_TIMED + ARMS_OFFLINE:
        r = [per[k]["recall"][arm] for k in keys]
        a = {"mean": sum(r) / len(r), "min": min(r),
             "hist": {f"{v:.1f}": sum(abs(x - v) < 1e-9 for x in r) for v in (1.0, 0.9, 0.8, 0.7)},
             "below_0.9": sum(x < 0.9 - 1e-9 for x in r),
             "by_type": {t: sum(per[k]["recall"][arm] for k in keys if per[k]["type"] == t) / out["types"][t]
                         for t in ("random", "energetic")}}
        need(abs(a["mean"] - d["summary"][arm]["recall"]["mean"]) < 1e-9, f"{arm}: mean disagrees with stored summary")
        if arm in ARMS_TIMED:
            lat = d["summary"][arm]["latency_ms"]
            a["p50"], a["p95"] = lat["p50"], lat["p95"]
        out["arms"][arm] = a
    missed, rank1 = 0, 0
    for k in keys:
        ref = [tuple(x) for x in per[k]["top"]["EX-2"]]
        got = {tuple(x) for x in per[k]["top"]["PR"]}
        miss = [i for i, it in enumerate(ref) if it not in got]
        missed += len(miss)
        rank1 += 0 in miss
    out["missed_PR"], out["missed_rank1_PR"], out["reference_items"] = missed, rank1, TOP * len(keys)
    strongest = min(("EX-2", "EX-8"), key=lambda x: out["arms"][x]["p95"])
    out["strongest_exact"] = strongest
    out["p95_ratio"] = out["arms"][strongest]["p95"] / out["arms"]["PR"]["p95"]
    out["sizes_mb"] = {k: v / 1e6 for k, v in sizes.items() if k != "other"}
    out["probe_over_raw"] = (sizes["probe"] + sizes["probe_index"]) / sizes["raw"]
    return out


def fmt(x, nd=3):
    return f"{x:.{nd}f}"


def provenance():
    """Report version and the measured software, as recorded by E1 in both databases."""
    envs = [load_e1(name)["environment"] for name in ("heldout", "dev")]
    keys = ("build_commit", "library_sha256", "server_version")
    need(all(e[k] == envs[0][k] for e in envs for k in keys) and envs[0]["extensions"] == envs[1]["extensions"],
         "E1 databases differ in build, library, server or extension versions")
    need(not envs[0]["build_dirty"], "E1 build was dirty")
    e = envs[0]
    return {"ReportVersion": REPORT_VERSION.read_text().strip(), "MeasuredCommit": e["build_commit"][:7],
            "MeasuredSQLVersion": e["extensions"]["pg_morlet"], "MeasuredPgvector": e["extensions"]["vector"],
            "MeasuredPostgres": e["server_version"]}


def macros(s):
    lines = ["% generated by research/paper/gen_numbers.py from research/paper/REPORT_VERSION and"
             " evaluation/consolidated/results; do not edit"]
    for k, v in provenance().items():
        lines.append(f"\\newcommand{{\\{k}}}{{{v}}}")
    for db, tag in (("heldout", "Ho"), ("dev", "Dev")):
        x = s[db]
        a = x["arms"]
        m = {
            f"E{tag}Queries": x["queries"], f"E{tag}Windows": f"{x['gallery_windows']:,}",
            f"E{tag}RecallPR": fmt(a["PR"]["mean"]), f"E{tag}MinPR": fmt(a["PR"]["min"], 1),
            f"E{tag}BelowNinePR": a["PR"]["below_0.9"],
            f"E{tag}RandomPR": fmt(a["PR"]["by_type"]["random"]), f"E{tag}EnergeticPR": fmt(a["PR"]["by_type"]["energetic"]),
            f"E{tag}PctPR": f"{100 * a['PR']['mean']:.1f}",
            f"E{tag}PfiftyPR": f"{a['PR']['p50']:.0f}", f"E{tag}PninetyfivePR": f"{a['PR']['p95']:.0f}",
            f"E{tag}PninetyfiveExact": f"{a[x['strongest_exact']]['p95']:.0f}",
            f"E{tag}Ratio": f"{x['p95_ratio']:.0f}",
            f"E{tag}RecallZsix": fmt(a["Z-650"]["mean"]), f"E{tag}RecallZthousand": fmt(a["Z-1000"]["mean"]),
            f"E{tag}PXsixtyone": fmt(a["PX-61"]["mean"]), f"E{tag}PXthirteen": fmt(a["PX-13"]["mean"]),
            f"E{tag}PQthirteen": fmt(a["PQ-13"]["mean"]), f"E{tag}PXseven": fmt(a["PX-7"]["mean"]),
            f"E{tag}EZ": fmt(a["EZ"]["mean"]),
            f"E{tag}Missed": x["missed_PR"], f"E{tag}MissedRankOne": x["missed_rank1_PR"],
            f"E{tag}RefItems": f"{x['reference_items']:,}",
            f"E{tag}ProbeOverRaw": f"{x['probe_over_raw']:.2f}",
            f"E{tag}RawMB": f"{x['sizes_mb']['raw']:.0f}",
        }
        for k, v in m.items():
            lines.append(f"\\newcommand{{\\{k}}}{{{v}}}")
    h = s["heldout"]["arms"]["PR"]["hist"]
    lines.append(f"\\newcommand{{\\EHoHist}}{{{h['1.0']}/{h['0.9']}/{h['0.8']}/{h['0.7']}}}")
    return "\n".join(lines) + "\n"


def tab_e1(s):
    a = s["heldout"]["arms"]
    rows = [("exact, 2 workers", "EX-2", "score all windows"), ("exact, 8 allowed (3 used)", "EX-8", "score all windows"),
            ("probes (product)", "PR", "13 scans, re-rank $\\approx$350"), ("zero-lag HNSW, 650", "Z-650", "1 scan, re-rank 650"),
            ("zero-lag HNSW, 1000", "Z-1000", "1 scan, re-rank 1000")]
    L = ["% generated by research/paper/gen_numbers.py; do not edit", "\\begin{tabular}{@{}lccl@{}}", "\\toprule",
         "Arm & Recall@10 & p50 / p95 ms & Work per query \\\\", "\\midrule"]
    for label, arm, work in rows:
        x = a[arm]
        rec = "1" if x["mean"] == 1 else f"{fmt(x['mean'])} / {fmt(x['min'], 1)} / {x['below_0.9']}"
        line = f"{label} & {rec} & {x['p50']:,.0f} / {x['p95']:,.0f} & {work} \\\\"
        if arm == "PR":
            line = f"\\textbf{{{label}}} & \\textbf{{{rec}}} & {x['p50']:,.0f} / \\textbf{{{x['p95']:,.0f}}} & {work} \\\\"
        lines_arm = line
        L.append(lines_arm)
    L += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(L) + "\n"


def tab_ablation(s):
    rows = [("all 61 lags, exact (PX-61)", "PX-61"), ("13-lag grid, exact (PX-13)", "PX-13"),
            ("13-lag grid, float16 probes, exact (PQ-13)", "PQ-13"), ("7-lag grid, exact (PX-7)", "PX-7"),
            ("\\textbf{13-lag grid, HNSW (product)}", "PR"), ("zero-lag, exact, same count (EZ)", "EZ")]
    L = ["% generated by research/paper/gen_numbers.py; do not edit", "\\begin{tabular}{@{}lcc@{}}", "\\toprule",
         "Candidates & dev & held-out \\\\", "\\midrule"]
    for label, arm in rows:
        dv, ho = fmt(s["dev"]["arms"][arm]["mean"]), fmt(s["heldout"]["arms"][arm]["mean"])
        if arm == "PR":
            dv, ho = f"\\textbf{{{dv}}}", f"\\textbf{{{ho}}}"
        L.append(f"{label} & {dv} & {ho} \\\\")
    L += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(L) + "\n"


def readme_block(s):
    h, d = s["heldout"], s["dev"]
    a = h["arms"]
    L = ["*(generated by `python3 research/paper/gen_numbers.py` from `evaluation/consolidated/results/`; do not edit)*", "",
         f"E1: all arms on one build (`main` 68594d7), {h['queries']} queries per database (50 random, 50 high-energy windows), "
         f"recall@10 = share of the {TOP} exact intervals a result contains, averaged over queries. Held-out data are stations and a "
         "year kept out of development; E1 re-measured them after their first use (BL1-C), so it is not a new independent confirmation.",
         "", "| Held-out ({:,} windows) | recall@10 | min | queries < 0.9 | p50 / p95 ms |".format(h["gallery_windows"]),
         "|---|---|---|---|---|"]
    for label, arm in (("exact, fastest arm", h["strongest_exact"]), ("shifted probes (product)", "PR"),
                       ("zero-lag HNSW, 650 candidates", "Z-650"), ("zero-lag HNSW, 1000 candidates", "Z-1000")):
        x = a[arm]
        L.append(f"| {label} | {fmt(x['mean'])} | {fmt(x['min'], 1)} | {x['below_0.9']} | {x['p50']:.0f} / {x['p95']:.0f} |")
    L += ["",
          f"- Ratio of p95 latencies, fastest in-database exact arm to probes: {h['p95_ratio']:.0f} (held-out), {d['p95_ratio']:.0f} (dev); "
          "not a per-query speed-up.",
          f"- Held-out recall by query group: random {fmt(a['PR']['by_type']['random'])}, high-energy {fmt(a['PR']['by_type']['energetic'])}; "
          f"{a['PR']['hist']['1.0']} / {a['PR']['hist']['0.9']} / {a['PR']['hist']['0.8']} / {a['PR']['hist']['0.7']} queries at 1.0 / 0.9 / 0.8 / 0.7; "
          f"{h['missed_PR']} of {h['reference_items']:,} reference intervals missed, {h['missed_rank1_PR']} of them the exact best match.",
          f"- Candidate generators (held-out): all 61 lags exact {fmt(a['PX-61']['mean'])}; 13-lag grid exact {fmt(a['PX-13']['mean'])}; "
          f"with float16 probes {fmt(a['PQ-13']['mean'])}; with HNSW {fmt(a['PR']['mean'])}; zero-lag exact, same count {fmt(a['EZ']['mean'])}.",
          f"- Storage (held-out): raw chunks {h['sizes_mb']['raw']:.0f} MB; envelopes {h['sizes_mb']['envelopes']:.0f} MB; probe table "
          f"{h['sizes_mb']['probe']:.0f} MB; HNSW index {h['sizes_mb']['probe_index']:.0f} MB; probe structures / raw "
          f"{h['probe_over_raw']:.2f} (dev {d['probe_over_raw']:.2f})."]
    return "\n".join(L)


def render():
    s = {name: summarize(load_e1(name), load_sizes(name)) for name in ("heldout", "dev")}
    files = {GEN / "summary.json": json.dumps(s, indent=1, sort_keys=True) + "\n", GEN / "numbers.tex": macros(s),
             GEN / "tab_e1.tex": tab_e1(s), GEN / "tab_ablation.tex": tab_ablation(s)}
    readme = README.read_text()
    a, z = "<!-- results:start -->", "<!-- results:end -->"
    if a not in readme or z not in readme:
        raise InputError(f"{README}: markers {a} / {z} missing")
    i, j = readme.index(a) + len(a), readme.index(z)
    files[README] = readme[:i] + "\n" + readme_block(s) + "\n" + readme[j:]
    return files


def main():
    check = "--check" in sys.argv[1:]
    try:
        files = render()
    except InputError as e:
        sys.exit(f"gen_numbers.py: {e}")
    stale = [p for p, text in files.items() if not p.exists() or p.read_text() != text]
    if check:
        if stale:
            sys.exit("stale generated files (run python3 research/paper/gen_numbers.py): " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
        print("generated files are up to date")
        return
    GEN.mkdir(exist_ok=True)
    for p in stale:
        p.write_text(files[p])
    print(f"wrote {len(stale)} file(s)" + ("" if stale else " (all up to date)"))


if __name__ == "__main__":
    main()
