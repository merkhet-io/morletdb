#!/usr/bin/env python3
"""Scripted agent for "Why this part?": investigates every flagged M01 run with the MCP server's tools.

  DATABASE_URL=postgresql://agent_reader:agent-demo-only@127.0.0.1:54351/morlet \
      python evaluation/agent_demo/demo_agent.py OUT_DIR [RUN ...]

Calls the tools of tools/mcp/morlet_mcp.py in-process, through the MCP server's own call_tool (the same
argument validation and JSON results a client gets), as the read-only role in DATABASE_URL. No language model:
the order of calls and every rule are fixed in QUESTIONS.md, so a rerun gives the same answers.

Writes, per run, OUT_DIR/answers/<run>.md (the plain-English answer) and OUT_DIR/transcripts/<run>.json.gz
(every tool call with its arguments, result or error and time), and OUT_DIR/answers.md, OUT_DIR/summary.json
with all runs. metrics.py computes the metrics from them.
"""
import asyncio
import gzip
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "mcp"))
import morlet_mcp  # noqa: E402

C = "public.bosch_m01"
WINDOW = 4096
STRIDE = 2048
RATE = 2000.0
AXES = ("x", "y", "z")
POOL_GOOD = 5
SEED = 20261111
MIN_REFS = 3
LEVEL_DB = 3.0
CLEAR_DB = 6.0


def ev(rid, a, b):
    return f"{C} / {rid} / {a}..{b}"


def f1(v):
    return "n/a" if v is None else f"{v:.1f}"


def secs(a):
    return f"{a / RATE:.1f} s"


class Agent:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.calls = []

    def call(self, tool, **args):
        """One tool call through the MCP server; returns the parsed result, or None on a tool error."""
        t = time.perf_counter()
        entry = {"step": len(self.calls) + 1, "tool": tool, "arguments": args}
        try:
            r = self.loop.run_until_complete(morlet_mcp.mcp.call_tool(tool, args))
            text = r.content[0].text if r.content else "{}"
            if getattr(r, "is_error", False):
                entry["error"] = text
                out = None
            else:
                out = json.loads(text)
                entry["result"] = out
        except Exception as exc:                 # the SDK raises ToolError for tool errors
            entry["error"] = f"{type(exc).__name__}: {exc}"
            out = None
        entry["ms"] = round(1000 * (time.perf_counter() - t), 1)
        self.calls.append(entry)
        return out


def investigate(agent, run):
    t_start = time.perf_counter()
    agent.calls = []
    facts = {"run": run}
    st = []                                      # statements: {"q", "text", "evidence": [...]}

    def say(q, text, *evidence):
        st.append({"q": q, "text": text, "evidence": [e for e in evidence if e]})

    # ---- 1. latest, list_recordings, coverage
    agent.call("latest", collection=C, metadata_filter=json.dumps({"run": run}), samples=1)
    lst = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"run": run}), include_metadata=True)
    recs = {r["metadata"]["axis"]: r for r in (lst or {}).get("recordings", [])}
    if set(recs) != set(AXES):
        facts["error"] = "run not found with three axes"
        return facts, st, agent.calls, time.perf_counter() - t_start
    meta = recs["x"]["metadata"]
    op, n = meta["operation"], recs["x"]["samples"]
    facts.update(operation=op, period=meta["period"], samples=n, file=meta["file"])
    say(1, f"Run {run} is machine {meta['machine']}, operation {op}, period {meta['period'].replace('_', ' ')}: "
           f"three axes (x, y, z) at {RATE:g} Hz, {n} samples ({secs(n)}) each; dataset label {meta['label']}.",
        *[ev(recs[a]["recording_id"], 0, recs[a]["samples"]) for a in AXES])
    cov = agent.call("coverage", collection=C, group_by="label", metadata_filter=json.dumps({"operation": op, "axis": "x"}))
    groups = {g["group"]: g["recordings"] for g in (cov or {}).get("groups", [])}
    facts["op_runs"] = groups
    if cov:
        say(1, f"Operation {op} on M01 has {groups.get('good', 0)} good and {groups.get('bad', 0)} bad runs "
               f"stored (one recording per axis).")
    ops = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"operation": op, "axis": "x"}),
                     include_metadata=True, limit=500)
    op_runs = {r["metadata"]["run"]: (r["metadata"]["label"], r["samples"]) for r in (ops or {}).get("recordings", [])}

    # ---- 2. decision_state per axis
    seg = {}
    for a in AXES:
        ds = agent.call("decision_state", collection=C, recording_id=recs[a]["recording_id"], sample=0,
                        exclude_labels="bad", neighbors=5, examples=3, example_labels="bad")
        if ds and ds.get("summary"):
            top = max((v for v in ds["values"] if v["relative_deviation"] is not None),
                      key=lambda v: abs(v["relative_deviation"]), default=None)
            seg[a] = {"score": ds["summary"]["score"], "top_value": ds["summary"]["top_value"],
                      "top_rel": ds["summary"]["top_relative_deviation"], "history": ds["history"]["size"],
                      "largest": top, "neighbours": ds["neighbors"], "examples": ds.get("examples", [])}
    facts["segment"] = seg
    if seg:
        a = max(seg, key=lambda k: seg[k]["score"])
        s = seg[a]
        rel = "n/a" if s["top_rel"] is None else f"{s['top_rel']:+.0%}"
        say(2, f"Whole run against the {s['history']} good {op} runs of the same axis: the largest summary "
               f"deviation is on axis {a}, {s['top_value']} {rel} from the history median "
               f"(score {s['score']:.2f}; per axis: " + ", ".join(f"{k} {seg[k]['score']:.2f}" for k in AXES if k in seg)
               + ").", ev(recs[a]["recording_id"], 0, n))
        if s["largest"] and s["largest"]["name"] != s["top_value"]:
            say(2, f"Of all values on axis {a}, {s['largest']['name']} deviates most: "
                   f"{s['largest']['relative_deviation']:+.0%} (history quantile {s['largest']['history_quantile']}).",
                ev(recs[a]["recording_id"], 0, n))
        facts["segment_axis"] = a

    # ---- 3a. window deviations over a pool: the flagged run and 5 seeded good runs of the operation
    good = sorted(r for r, (lab, _) in op_runs.items() if lab == "good")
    rng = np.random.default_rng(SEED)
    pool_good = [good[i] for i in sorted(rng.choice(len(good), min(POOL_GOOD, len(good)), replace=False))]
    pool = [run] + pool_good
    lengths = {r: op_runs[r][1] for r in pool}
    desc = {}                                    # (run, axis, i) -> describe_interval result
    for r in pool:
        for a in AXES:
            for i in range(lengths[r] // WINDOW):
                d = agent.call("describe_interval", collection=C, recording_id=f"{r}-{a}", start_sample=i * WINDOW,
                               end_sample=(i + 1) * WINDOW)
                if d:
                    desc[(r, a, i)] = d
    power = {k: v["std"] ** 2 for k, v in desc.items()}
    dev = {}
    for (r, a, i), p in power.items():
        refs = [power[(s, a, i)] for s in pool_good if s != r and (s, a, i) in power]
        if len(refs) >= MIN_REFS and p > 0 and np.median(refs) > 0:
            dev[(r, a, i)] = abs(10 * math.log10(p / float(np.median(refs))))
    if not dev:
        facts["error"] = "no window could be scored"
        return facts, st, agent.calls, time.perf_counter() - t_start
    pool_top = max(dev, key=lambda k: (dev[k], k))
    mine = {k: v for k, v in dev.items() if k[0] == run}
    facts["pool"] = pool
    facts["pool_top"] = {"run": pool_top[0], "axis": pool_top[1], "window": pool_top[2], "db": round(dev[pool_top], 2)}
    facts["pool_top_in_flagged"] = pool_top[0] == run
    facts["flagged_share_of_scored_windows"] = len(mine) / len(dev)
    if not mine:
        facts["error"] = "no window of the flagged run could be scored"
        return facts, st, agent.calls, time.perf_counter() - t_start
    _, ta, ti = max(mine, key=lambda k: (mine[k], k))
    ws, we = ti * WINDOW, (ti + 1) * WINDOW
    rid = f"{run}-{ta}"
    p_flag = power[(run, ta, ti)]
    signed = 10 * math.log10(p_flag / float(np.median([power[(s, ta, ti)] for s in pool_good if (s, ta, ti) in power])))
    facts["top"] = {"axis": ta, "window": ti, "start": ws, "end": we, "db": round(signed, 2), "abs_db": round(mine[(run, ta, ti)], 2)}
    say(2, f"The most deviating window of this run is on axis {ta}, samples {ws}..{we} ({secs(ws)} to {secs(we)} into "
           f"the run): its vibration power is {signed:+.1f} dB against the median of the same window in "
           f"{len([s for s in pool_good if (s, ta, ti) in power])} good {op} runs.", ev(rid, ws, we))
    if facts["pool_top_in_flagged"]:
        say(2, f"Across the flagged run and {len(pool_good)} good {op} runs, this is the largest window deviation of "
               f"all ({len(dev)} windows scored), so it singles this run out.", ev(rid, ws, we))
    else:
        r2, a2, i2 = pool_top
        say(2, f"It is not the largest in the pool: good run {r2} deviates more ({dev[pool_top]:.1f} dB, axis {a2}, "
               f"samples {i2 * WINDOW}..{(i2 + 1) * WINDOW}), so window power alone does not single this run out.",
            ev(f"{r2}-{a2}", i2 * WINDOW, (i2 + 1) * WINDOW))

    # ---- 3b. level or shape, band: against the good pool run with the median power in that window
    cands = sorted((power[(s, ta, ti)], s) for s in pool_good if (s, ta, ti) in power)
    ref = cands[(len(cands) - 1) // 2][1]
    ref_rid = f"{ref}-{ta}"
    facts["reference_run"] = ref
    cmp_ = agent.call("compare_intervals", collection=C, recording_id=rid, start_sample=ws, end_sample=we,
                      reference_start_sample=ws, reference_end_sample=we, reference_recording_id=ref_rid)
    kinds = []
    if cmp_:
        ratio = cmp_["power_ratio_db"]
        bands = cmp_["bands"]
        if ratio is not None and ratio >= 0:
            band = max(bands, key=lambda k: bands[k]["excess_fraction"])
            band_txt = f"mostly in {band} ({bands[band]['excess_fraction']:.0%} of the excess power)"
        else:
            band = min(bands, key=lambda k: bands[k]["ratio_db"] if bands[k]["ratio_db"] is not None else 0)
            band_txt = f"most reduced in {band} ({bands[band]['ratio_db']:+.1f} dB)"
        facts["band"] = band
        facts["power_ratio_db"] = ratio
        if ratio is not None and abs(ratio) >= LEVEL_DB:
            kinds.append("level")
        say(3, f"Against the same window of good run {ref} (median power there): {ratio:+.1f} dB overall, {band_txt}.",
            ev(rid, ws, we), ev(ref_rid, ws, we))
    kf, kr = desc[(run, ta, ti)], desc[(ref, ta, ti)]
    k_ratio = (kf["kurtosis"] or 0) / (kr["kurtosis"] or 1)
    c_ratio = (kf["crest_factor"] or 0) / (kr["crest_factor"] or 1)
    if k_ratio >= 2 or k_ratio <= 0.5 or c_ratio >= 1.5 or c_ratio <= 1 / 1.5:
        kinds.append("shape")
    say(3, f"Impulsiveness: kurtosis {f1(kf['kurtosis'])} against {f1(kr['kurtosis'])}, crest factor "
           f"{f1(kf['crest_factor'])} against {f1(kr['crest_factor'])}.", ev(rid, ws, we), ev(ref_rid, ws, we))
    es = agent.call("envelope_spectrum", collection=C, recording_id=rid, start_sample=ws, end_sample=we)
    if es and es["peaks"]:
        f0, db0 = es["peaks"][0]["frequency_hz"], es["peaks"][0]["db"]
        above = db0 - es["noise_peak_db"]
        band_arg = "-".join(f"{v:g}" for v in es["demodulation_band_hz"])
        er = agent.call("envelope_spectrum", collection=C, recording_id=ref_rid, start_sample=ws, end_sample=we,
                        band=band_arg, check_frequencies=f"{f0}", harmonics=1)
        ref_above = er["checks"][0]["harmonics"][0]["db_above_noise_peaks"] if er and er["checks"] else None
        facts["envelope"] = {"frequency_hz": f0, "db_above_noise": round(above, 1), "reference_db_above_noise": ref_above}
        new_line = above >= 10 and ref_above is not None and ref_above < 6
        if new_line:
            kinds.append("shape")
        say(3, f"Envelope spectrum ({band_arg} Hz band): strongest line at {f0:g} Hz, {above:.1f} dB above the noise "
               f"peaks; the reference has {ref_above if ref_above is not None else 'no value'} dB there"
               + (" - a periodic modulation the good run does not have." if new_line else "."),
            ev(rid, ws, we), ev(ref_rid, ws, we))
    kinds = sorted(set(kinds))
    facts["kind"] = " and ".join(kinds) if kinds else "neither above the thresholds"
    say(3, f"Classification: {facts['kind']} (rules in QUESTIONS.md: level if |ratio| >= 3 dB; shape if kurtosis x2, "
           f"crest x1.5 or a new envelope line).", ev(rid, ws, we))

    # ---- 4. find_onsets: first deviating window, new onsets
    series = sorted((i, v) for (r, a, i), v in dev.items() if r == run and a == ta)
    first = next((i for i, v in series if v >= LEVEL_DB), None)
    facts["first_window_3db"] = first
    if first is not None:
        say(4, f"On axis {ta} the deviation first reaches 3 dB in window {first} (samples {first * WINDOW}.."
               f"{(first + 1) * WINDOW}, {secs(first * WINDOW)} into the run); " +
               f"{sum(1 for _, v in series if v >= LEVEL_DB)} of {len(series)} scored windows reach it.",
            ev(rid, first * WINDOW, (first + 1) * WINDOW))
    else:
        say(4, f"On axis {ta} no window reaches 3 dB against the good runs.", ev(rid, 0, n))
    on_f = agent.call("find_onsets", collection=C, recording_id=rid, start_sample=0, end_sample=n,
                      sta_seconds=0.25, lta_seconds=5.0, threshold=4.0, max_onsets=50)
    on_r = agent.call("find_onsets", collection=C, recording_id=ref_rid, start_sample=0, end_sample=lengths[ref],
                      sta_seconds=0.25, lta_seconds=5.0, threshold=4.0, max_onsets=50)
    if on_f is not None and on_r is not None:
        ref_on = [o["trigger_sample"] for o in on_r["onsets"]]
        new = [o for o in on_f["onsets"] if not any(abs(o["trigger_sample"] - s) <= RATE for s in ref_on)]
        facts["onsets"] = {"flagged": len(on_f["onsets"]), "reference": len(ref_on), "new": [o["trigger_sample"] for o in new]}
        if new:
            say(4, f"Onsets (STA/LTA): {len(on_f['onsets'])} in this run, {len(ref_on)} in good run {ref}; "
                   f"{len(new)} have no counterpart within 1 s in the good run, the first at sample "
                   f"{new[0]['trigger_sample']} ({secs(new[0]['trigger_sample'])}).",
                ev(rid, new[0]["trigger_sample"], new[0]["end_sample"]))
        else:
            say(4, f"Onsets (STA/LTA): {len(on_f['onsets'])} in this run, {len(ref_on)} in good run {ref}; none is new.",
                ev(rid, 0, n), ev(ref_rid, 0, lengths[ref]))

    # ---- 5. trend over the operation's recordings of that axis
    tr = agent.call("trend", collection=C, metadata_filter=json.dumps({"operation": op, "axis": ta}), max_points=300)
    if tr:
        rows = [r.split(",") for r in tr["table_csv"].splitlines()]
        head, body = rows[0], rows[1:]
        i_r, i_rms = head.index("recording_id"), head.index("rms")
        vals = sorted(((float(r[i_rms]), r[i_r]) for r in body), reverse=True)
        rank = next((k + 1 for k, (_, r) in enumerate(vals) if r == rid), None)
        cp = (tr["change_points"].get("rms") or {}).get("first_change_utc")
        facts["trend"] = {"points": tr["points"], "rms_rank": rank, "rms_change_utc": cp}
        say(5, f"Trend over the {tr['points']} {op} recordings of axis {ta}, in nominal time order: this run's rms ranks "
               f"{rank} of {len(vals)}" + (f"; rms change point at {cp}" if cp else "; no rms change point")
               + ". Caveat (#385): trend measures only each recording's first 4,096 samples (the lead-in), "
               + ("which here is the deviating window." if ti == 0 else "not the deviating window."),
            ev(rid, 0, WINDOW))

    # ---- 6. search: seen before? and what happened then (labels, logged decisions)
    def score_hits(res, key):
        if res is None:
            return None
        hits = res["hits"]
        own = [h for h in hits if res["recordings"][h["recording_id"]]["run"] == run]
        rest = [h for h in hits if res["recordings"][h["recording_id"]]["run"] != run]
        lab = [res["recordings"][h["recording_id"]]["label"] == "bad" for h in rest]
        out = {"hits": len(hits), "own_run_hits_dropped": len(own), "scored": len(rest),
               "p@5": sum(lab[:5]) / 5 if len(lab) >= 5 else None, "p@10": sum(lab[:10]) / 10 if len(lab) >= 10 else None,
               "top": [{"recording_id": h["recording_id"], "start_sample": h["start_sample"],
                        "end_sample": h["end_sample"], "distance": h["distance"],
                        "label": res["recordings"][h["recording_id"]]["label"]} for h in rest[:10]]}
        facts[key] = out
        return out

    other = {r: v for r, v in op_runs.items() if r != run}
    win = lambda m: max(0, (m - WINDOW) // STRIDE + 1)  # noqa: E731  stored windows per recording
    facts["chance"] = sum(win(m) for lab, m in other.values() if lab == "bad") / max(1, sum(win(m) for _, m in other.values()))
    ss = score_hits(agent.call("search_similar", collection=C, recording_id=rid, start_sample=ws, end_sample=we,
                               metadata_filter=json.dumps({"operation": op}), top_k=50), "search_similar")
    utc = desc[(run, ta, ti)]["start_utc"]
    sf = score_hits(agent.call("search_from_time", collection=C, utc_time=utc,
                               metadata_filter=json.dumps({"run": run, "axis": ta}),
                               search_filter=json.dumps({"operation": op}), top_k=50), "search_from_time")
    flagged_hits = {}
    if ss:
        for h in ss["top"]:
            hr = h["recording_id"].rsplit("-", 1)[0]
            if hr not in flagged_hits:
                d = agent.call("decisions", collection=C, recording_prefix=hr, limit=5)
                flagged_hits[hr] = bool(d and d["decisions"])
        facts["hit_runs_flagged"] = flagged_hits
        bad5 = sum(h["label"] == "bad" for h in ss["top"][:5])
        hs = ss["top"][:5]
        say(6, f"Similar windows elsewhere in {op} (search_similar, own run excluded; {ss['own_run_hits_dropped']} of "
               f"{ss['hits']} hits were from this run itself, #384): {bad5} of the nearest 5 and "
               f"{sum(h['label'] == 'bad' for h in ss['top'][:10])} of the nearest 10 are from bad runs; "
               f"by chance {facts['chance']:.0%} of the windows are. "
               f"{sum(flagged_hits.values())} of {len(flagged_hits)} hit runs carry a logged flag (decisions).",
            *[ev(h["recording_id"], h["start_sample"], h["end_sample"]) for h in hs])
    if sf:
        say(6, f"search_from_time on the same moment (framing by its own rule): "
               f"{sum(h['label'] == 'bad' for h in sf['top'][:5])} of the nearest 5 hits from bad runs.",
            *[ev(h["recording_id"], h["start_sample"], h["end_sample"]) for h in sf["top"][:5]])

    # ---- 7. evidence: the raw samples
    raw = agent.call("read_interval", collection=C, recording_id=rid, start_sample=ws, end_sample=we, max_points=WINDOW)
    if raw and "samples" in raw:
        facts["evidence_read"] = {"recording_id": rid, "start": ws, "end": we, "values": len(raw["samples"]),
                                  "file": meta["file"], "axis": AXES.index(ta)}
        say(7, f"The {len(raw['samples'])} original samples of that window read back from the database "
               f"(rms {raw['stats']['rms']:.1f} counts); they come from {meta['file']} (SHA-256 {meta['sha256'][:12]}...).",
            ev(rid, ws, we))

    # ---- recommendation (fixed rules)
    score = max((s["score"] for s in seg.values()), default=0)
    absdb = facts["top"]["abs_db"]
    where = f"{secs(ws)}..{secs(we)} on axis {ta}"
    if absdb >= CLEAR_DB or score >= 1:
        rec = f"Inspect this part at {where}; the deviation is clear."
    elif absdb >= LEVEL_DB:
        rec = f"Check this part at {where}; the deviation is moderate."
    else:
        rec = "No clear deviation found by these tools; check the process record."
    if ss and ss["p@5"] is not None and ss["p@5"] >= 0.6:
        rec += " Similar windows were mostly in bad runs before."
    facts["recommendation"] = rec
    say(8, "Recommendation: " + rec, ev(rid, ws, we))
    return facts, st, agent.calls, time.perf_counter() - t_start


def render(facts, st, wall):
    run = facts["run"]
    out = [f"### {run}", ""]
    if "error" in facts:
        out.append(f"Investigation stopped: {facts['error']}.")
    heads = {1: "What it is", 2: "What is different, and where", 3: "Level or shape", 4: "Since when",
             5: "History", 6: "Seen before, and what happened then", 7: "Evidence", 8: "Recommendation"}
    for q in sorted({s["q"] for s in st}):
        out.append(f"**{heads[q]}.**")
        for s in (x for x in st if x["q"] == q):
            links = "".join(f"\n  - `{e}`" for e in s["evidence"])
            out.append(f"- {s['text']}{links}")
        out.append("")
    out.append(f"_{len(st)} statements, {sum(1 for s in st if s['evidence'])} with evidence; {wall:.1f} s._")
    return "\n".join(out) + "\n"


def main():
    out = Path(sys.argv[1])
    (out / "answers").mkdir(parents=True, exist_ok=True)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    agent = Agent()
    runs = sys.argv[2:]
    if not runs:
        lst = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"label": "bad", "axis": "x"}),
                         include_metadata=True, limit=500)
        runs = sorted(r["metadata"]["run"] for r in lst["recordings"])
    summary, md = [], ["# Why this part? Answers for the flagged M01 runs", "",
                       "Generated by `demo_agent.py`; see `../README.md`. Development data, not study results.", ""]
    for run in runs:
        facts, st, calls, wall = investigate(agent, run)
        facts["wall_s"] = round(wall, 2)
        facts["tool_calls"] = len(calls)
        facts["tool_errors"] = [{"tool": c["tool"], "error": c["error"]} for c in calls if "error" in c]
        facts["statements"] = len(st)
        facts["statements_with_evidence"] = sum(1 for s in st if s["evidence"])
        text = render(facts, st, wall)
        (out / "answers" / f"{run}.md").write_text(text)
        with gzip.open(out / "transcripts" / f"{run}.json.gz", "wt") as f:
            json.dump({"run": run, "facts": facts, "statements": st, "calls": calls}, f)
        summary.append(facts)
        md.append(text)
        print(f"{run}: {len(calls)} calls, {len(facts['tool_errors'])} errors, {wall:.1f} s, "
              f"top in flagged {facts.get('pool_top_in_flagged')}, p@5 {facts.get('search_similar', {}).get('p@5')}",
              flush=True)
    (out / "answers.md").write_text("\n".join(md))
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
