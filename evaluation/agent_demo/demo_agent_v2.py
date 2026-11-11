#!/usr/bin/env python3
"""Scripted agent for "Why this part?", version 2 (QUESTIONS.md, "Version 2").

  DATABASE_URL=postgresql://agent_reader:agent-demo-only@127.0.0.1:54351/morlet \
      python -I evaluation/agent_demo/demo_agent_v2.py OUT_DIR [RUN ...]

Two phases, both through the MCP server's tools (read-only role, no language model):
1. calibrate: per operation with bad runs, align every good run to its start of machining, score the calibration
   and held-out good runs against the 5 comparison runs, and fix the check/inspect and shape thresholds from the
   calibration runs (OUT_DIR/calibration.json, transcript OUT_DIR/calibration_transcript.json.gz);
2. investigate every flagged run with aligned windows and the calibrated thresholds (OUT_DIR/answers/,
   OUT_DIR/transcripts/, OUT_DIR/answers.md, OUT_DIR/summary.json).
"""
import gzip
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from demo_agent import AXES, C, RATE, STRIDE, WINDOW, Agent, ev, f1, secs  # noqa: E402

SEED_POOL, SEED_SPLIT = 20261111, 20261112
POOL_GOOD, MIN_REFS = 5, 3
MIN_CALIBRATION_PER_OP = 40
BUCKET = 512


# ------------------------------------------------------------------ alignment (V2.1)
def onset_of(agent, rid, n):
    """(onset sample, status) by the fixed bucket-amplitude rule; status no_lead_in, clear or unclear."""
    mp = min(20000, 2 * math.ceil(n / BUCKET))
    r = agent.call("read_interval", collection=C, recording_id=rid, start_sample=0, end_sample=n, max_points=mp)
    if r is None or "min_max" not in r:
        return 0, "unclear"
    a = np.array([(hi - lo) / 2 for lo, hi in r["min_max"]])
    nb = len(a)
    starts = [j * (n // nb) + min(j, n % nb) for j in range(nb)]       # np.array_split boundaries
    m = float(np.median(a))
    j = next((j for j in range(nb) if float(np.median(a[j:j + 4])) >= 0.5 * m), None)
    if j is None:
        return 0, "unclear"
    if j == 0:
        return 0, "no_lead_in"
    if float(np.median(a[:j])) <= 0.25 * m:
        return starts[j], "clear"
    return 0, "unclear"


class Runs:
    """Window variances of runs, aligned (by onset) and raw (from sample 0), read through describe_interval."""

    def __init__(self, agent, lengths):
        self.agent, self.lengths = agent, lengths
        self.onsets, self.aligned, self.raw, self.desc = {}, {}, {}, {}

    def align(self, run):
        for a in AXES:
            if (run, a) not in self.onsets:
                self.onsets[(run, a)] = onset_of(self.agent, f"{run}-{a}", self.lengths[run])

    def windows(self, run, aligned=True):
        store = self.aligned if aligned else self.raw
        if run in store:
            return store[run]
        if aligned:
            self.align(run)
        out = {}
        n = self.lengths[run]
        for a in AXES:
            o = self.onsets[(run, a)][0] if aligned else 0
            for i in range((n - o) // WINDOW):
                s = o + i * WINDOW
                d = self.agent.call("describe_interval", collection=C, recording_id=f"{run}-{a}", start_sample=s,
                                    end_sample=s + WINDOW)
                if d:
                    out[(a, i)] = d["std"] ** 2
                    self.desc[(run, a, s)] = d
        store[run] = out
        return out

    def start(self, run, a, i, aligned=True):
        return (self.onsets[(run, a)][0] if aligned else 0) + i * WINDOW


def deviations(target, refs):
    """{(axis, i): |dB|} of target's window variances against the median over refs having that window."""
    out = {}
    for k, p in target.items():
        rs = [r[k] for r in refs if k in r]
        if len(rs) >= MIN_REFS and p > 0 and np.median(rs) > 0:
            out[k] = abs(10 * math.log10(p / float(np.median(rs))))
    return out


def top(dev):
    return max(dev, key=lambda k: (dev[k], k)) if dev else None


def shape_distance(agent, rid, s, comparison, exclude=None):
    """S: median over comparison runs of the smallest envelope distance from [s, s+WINDOW) to that run's recording
    of the same axis (search_similar restricted by run)."""
    ds = []
    for c in comparison:
        if c == exclude:
            continue
        r = agent.call("search_similar", collection=C, recording_id=rid, start_sample=s, end_sample=s + WINDOW,
                       metadata_filter=json.dumps({"run": c}), top_k=1, evidence=False)
        if r and r["hits"]:
            ds.append(r["hits"][0]["distance"])
    return float(np.median(ds)) if ds else None


def op_runs(agent, op):
    lst = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"operation": op, "axis": "x"}),
                     include_metadata=True, limit=500)
    return {r["metadata"]["run"]: {"label": r["metadata"]["label"], "samples": r["samples"],
                                   "period": r["metadata"]["period"]} for r in lst["recordings"]}


def split(runs):
    good = sorted(r for r, v in runs.items() if v["label"] == "good")
    rng = np.random.default_rng(SEED_POOL)
    comparison = [good[i] for i in sorted(rng.choice(len(good), min(POOL_GOOD, len(good)), replace=False))]
    rest = [g for g in good if g not in comparison]
    perm = np.random.default_rng(SEED_SPLIT).permutation(len(rest))
    k = len(rest) // 2
    return comparison, sorted(rest[i] for i in perm[:k]), sorted(rest[i] for i in perm[k:])


# ------------------------------------------------------------------ phase 1: calibration (V2.2, V2.4)
def calibrate(agent, out):
    t0 = time.perf_counter()
    lst = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"label": "bad", "axis": "x"}),
                     include_metadata=True, limit=500)
    ops = sorted({r["metadata"]["operation"] for r in lst["recordings"]})
    per_run, per_op = {}, {}
    for op in ops:
        runs = op_runs(agent, op)
        comparison, calib, held = split(runs)
        per_op[op] = {"comparison": comparison, "calibration": calib, "held_out": held}
        R = Runs(agent, {r: v["samples"] for r, v in runs.items()})
        comp_al = [R.windows(c) for c in comparison]
        comp_raw = [R.windows(c, aligned=False) for c in comparison]
        for role, group in (("calibration", calib), ("held_out", held)):
            for g in group:
                dev = deviations(R.windows(g), comp_al)
                k = top(dev)
                row = {"op": op, "role": role, "period": runs[g]["period"],
                       "onsets": {a: list(R.onsets[(g, a)]) for a in AXES}}
                if k:
                    a, i = k
                    s = R.start(g, a, i)
                    row.update(D=round(dev[k], 3), top_axis=a, top_window=i, top_start=s,
                               S=shape_distance(agent, f"{g}-{a}", s, comparison))
                if role == "held_out":
                    raw = deviations(R.windows(g, aligned=False), comp_raw)
                    row["D_raw"] = round(max(raw.values()), 3) if raw else None
                per_run[g] = row
        for c in comparison:
            per_run[c] = {"op": op, "role": "comparison", "period": runs[c]["period"],
                          "onsets": {a: list(R.onsets[(c, a)]) for a in AXES}}
        print(f"calibrate {op}: {len(comparison)} comparison, {len(calib)} calibration, {len(held)} held out, "
              f"{len(agent.calls)} calls so far", flush=True)
    cal = [r for r in per_run.values() if r["role"] == "calibration" and "D" in r]
    pooled = {"check": float(np.percentile([r["D"] for r in cal], 80)),
              "inspect": float(np.percentile([r["D"] for r in cal], 95)),
              "shape": float(np.percentile([r["S"] for r in cal if r["S"] is not None], 80)), "runs": len(cal)}
    thresholds = {}
    for op in ops:
        own = [r for r in cal if r["op"] == op]
        if len(own) >= MIN_CALIBRATION_PER_OP:
            thresholds[op] = {"check": float(np.percentile([r["D"] for r in own], 80)),
                              "inspect": float(np.percentile([r["D"] for r in own], 95)),
                              "shape": float(np.percentile([r["S"] for r in own if r["S"] is not None], 80)),
                              "runs": len(own), "source": "operation"}
        else:
            thresholds[op] = {**pooled, "source": f"pooled ({len(own)} calibration runs in {op} < "
                                                  f"{MIN_CALIBRATION_PER_OP})"}
    res = {"seeds": {"comparison": SEED_POOL, "split": SEED_SPLIT}, "pooled": pooled, "thresholds": thresholds,
           "operations": per_op, "runs": per_run, "tool_calls": len(agent.calls),
           "tool_errors": [{"tool": c["tool"], "error": c["error"]} for c in agent.calls if "error" in c],
           "wall_s": round(time.perf_counter() - t0, 1)}
    (out / "calibration.json").write_text(json.dumps(res, indent=1) + "\n")
    with gzip.open(out / "calibration_transcript.json.gz", "wt") as f:
        json.dump(agent.calls, f)
    return res


# ------------------------------------------------------------------ phase 2: investigation
def investigate(agent, run, cal):
    t_start = time.perf_counter()
    agent.calls = []
    facts = {"run": run}
    st = []

    def say(q, text, *evidence):
        st.append({"q": q, "text": text, "evidence": [e for e in evidence if e]})

    def done():
        return facts, st, agent.calls, time.perf_counter() - t_start

    # 1. latest, list_recordings, coverage
    agent.call("latest", collection=C, metadata_filter=json.dumps({"run": run}), samples=1)
    lst = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"run": run}), include_metadata=True)
    recs = {r["metadata"]["axis"]: r for r in (lst or {}).get("recordings", [])}
    if set(recs) != set(AXES):
        facts["error"] = "run not found with three axes"
        return done()
    meta = recs["x"]["metadata"]
    op, n, period = meta["operation"], recs["x"]["samples"], meta["period"]
    facts.update(operation=op, period=period, samples=n, file=meta["file"])
    say(1, f"Run {run} is machine {meta['machine']}, operation {op}, period {period.replace('_', ' ')}: three axes "
           f"at {RATE:g} Hz, {n} samples ({secs(n)}) each; dataset label {meta['label']}.",
        *[ev(recs[a]["recording_id"], 0, recs[a]["samples"]) for a in AXES])
    cov = agent.call("coverage", collection=C, group_by="label", metadata_filter=json.dumps({"operation": op, "axis": "x"}))
    groups = {g["group"]: g["recordings"] for g in (cov or {}).get("groups", [])}
    if cov:
        say(1, f"Operation {op} on M01 has {groups.get('good', 0)} good and {groups.get('bad', 0)} bad runs stored.")
    runs = op_runs(agent, op)
    th = cal["thresholds"][op]
    comparison = cal["operations"][op]["comparison"]

    # 2. decision_state per axis
    seg = {}
    for a in AXES:
        ds = agent.call("decision_state", collection=C, recording_id=recs[a]["recording_id"], sample=0,
                        exclude_labels="bad", neighbors=5, examples=3, example_labels="bad")
        if ds and ds.get("summary"):
            seg[a] = {"score": ds["summary"]["score"], "top_value": ds["summary"]["top_value"],
                      "top_rel": ds["summary"]["top_relative_deviation"], "history": ds["history"]["size"]}
    facts["segment"] = seg
    if seg:
        a = max(seg, key=lambda k: seg[k]["score"])
        rel = "n/a" if seg[a]["top_rel"] is None else f"{seg[a]['top_rel']:+.0%}"
        say(2, f"Whole run against the {seg[a]['history']} good {op} runs of the same axis: largest summary deviation "
               f"on axis {a}, {seg[a]['top_value']} {rel} from the history median (score {seg[a]['score']:.2f}). "
               f"This includes the lead-in.", ev(recs[a]["recording_id"], 0, n))

    # 3. aligned windows over the pool: flagged run + the 5 comparison runs
    R = Runs(agent, {r: v["samples"] for r, v in runs.items()})
    pool = [run] + comparison
    win = {r: R.windows(r) for r in pool}
    facts["onsets"] = {a: list(R.onsets[(run, a)]) for a in AXES}
    dev = {(run,) + k: v for k, v in deviations(win[run], [win[c] for c in comparison]).items()}
    for c in comparison:
        dev.update({(c,) + k: v for k, v in deviations(win[c], [win[o] for o in comparison if o != c]).items()})
    mine = {k[1:]: v for k, v in dev.items() if k[0] == run}
    if not mine:
        facts["error"] = "no aligned window of the flagged run could be scored"
        return done()
    pool_top = max(dev, key=lambda k: (dev[k], k))
    facts["pool_top"] = {"run": pool_top[0], "axis": pool_top[1], "window": pool_top[2], "db": round(dev[pool_top], 2)}
    facts["pool_top_in_flagged"] = pool_top[0] == run
    facts["flagged_share_of_scored_windows"] = len(mine) / len(dev)
    ta, ti = top(mine)
    D = mine[(ta, ti)]
    o = R.onsets[(run, ta)]
    ws, we = R.start(run, ta, ti), R.start(run, ta, ti) + WINDOW
    rid = f"{run}-{ta}"
    refp = [win[c][(ta, ti)] for c in comparison if (ta, ti) in win[c]]
    signed = 10 * math.log10(win[run][(ta, ti)] / float(np.median(refp)))
    facts["top"] = {"axis": ta, "window": ti, "start": ws, "end": we, "db": round(signed, 2), "abs_db": round(D, 2),
                    "onset": o[0], "onset_status": o[1]}
    status = {"clear": f"machining starts at sample {o[0]} ({secs(o[0])})",
              "no_lead_in": "machining starts at the first sample (no lead-in)",
              "unclear": "no clear start of machining was found, so raw positions are used"}
    say(3, f"Aligned to the start of machining (axis {ta}: {status[o[1]]}), the most deviating window is aligned "
           f"window {ti}, samples {ws}..{we} ({secs(ws)} to {secs(we)} into the recording): its vibration power is "
           f"{signed:+.1f} dB against the median of the same aligned window in {len(refp)} good {op} runs.",
        ev(rid, ws, we))
    say(3, f"Calibrated on held-apart good runs ({th['source']}), check is {th['check']:.1f} dB and inspect "
           f"{th['inspect']:.1f} dB; this run's {D:.1f} dB is "
           + ("above inspect." if D >= th["inspect"] else "above check." if D >= th["check"] else "below check."),
        ev(rid, ws, we))
    if facts["pool_top_in_flagged"]:
        say(3, f"Across this run and the {len(comparison)} comparison runs it is the largest deviation of all "
               f"({len(dev)} aligned windows scored).", ev(rid, ws, we))
    else:
        r2, a2, i2 = pool_top
        s2 = R.start(r2, a2, i2)
        say(3, f"It is not the largest in the pool: good run {r2} deviates more ({dev[pool_top]:.1f} dB on axis {a2}).",
            ev(f"{r2}-{a2}", s2, s2 + WINDOW))

    # 4. level or shape (V2.4), band
    S = shape_distance(agent, rid, ws, comparison)
    level, shape = D >= th["check"], S is not None and S >= th["shape"]
    kind = "level and shape" if level and shape else "level only" if level else "shape only" if shape else "neither"
    facts.update(kind=kind, S=S)
    say(4, f"Shape: the closest envelope shape to this window in each comparison run is at distance "
           f"{f1(S) if S is None else f'{S:.3f}'} (median; shape threshold {th['shape']:.3f}). Level: {D:.1f} dB "
           f"against check {th['check']:.1f} dB. Classification: {kind}.", ev(rid, ws, we))
    cands = sorted((win[c][(ta, ti)], c) for c in comparison if (ta, ti) in win[c])
    ref = cands[(len(cands) - 1) // 2][1]
    rs = R.start(ref, ta, ti)
    ref_rid = f"{ref}-{ta}"
    facts["reference_run"] = ref
    cmp_ = agent.call("compare_intervals", collection=C, recording_id=rid, start_sample=ws, end_sample=we,
                      reference_start_sample=rs, reference_end_sample=rs + WINDOW, reference_recording_id=ref_rid)
    if cmp_:
        ratio, bands = cmp_["power_ratio_db"], cmp_["bands"]
        if ratio is not None and ratio >= 0:
            band = max(bands, key=lambda k: bands[k]["excess_fraction"])
            txt = f"mostly in {band} ({bands[band]['excess_fraction']:.0%} of the excess power)"
        else:
            band = min(bands, key=lambda k: bands[k]["ratio_db"] if bands[k]["ratio_db"] is not None else 0)
            txt = f"most reduced in {band} ({bands[band]['ratio_db']:+.1f} dB)"
        facts["band"] = band
        say(4, f"Against the same aligned window of good run {ref}: {ratio:+.1f} dB, {txt}.",
            ev(rid, ws, we), ev(ref_rid, rs, rs + WINDOW))
    kf, kr = R.desc[(run, ta, ws)], R.desc[(ref, ta, rs)]
    say(4, f"Described, not classified: kurtosis {f1(kf['kurtosis'])} against {f1(kr['kurtosis'])}, crest factor "
           f"{f1(kf['crest_factor'])} against {f1(kr['crest_factor'])}.", ev(rid, ws, we), ev(ref_rid, rs, rs + WINDOW))
    es = agent.call("envelope_spectrum", collection=C, recording_id=rid, start_sample=ws, end_sample=we)
    if es and es["peaks"]:
        say(4, f"Envelope spectrum: strongest line at {es['peaks'][0]['frequency_hz']:g} Hz, "
               f"{es['peaks'][0]['db'] - es['noise_peak_db']:.1f} dB above the noise peaks.", ev(rid, ws, we))

    # 5. since when
    series = sorted((i, v) for (a, i), v in mine.items() if a == ta)
    first = next((i for i, v in series if v >= th["check"]), None)
    facts["first_window_check"] = first
    if first is not None:
        s1 = R.start(run, ta, first)
        say(5, f"On axis {ta} the deviation first reaches check in aligned window {first} ({secs(s1)} into the "
               f"recording); {sum(1 for _, v in series if v >= th['check'])} of {len(series)} aligned windows reach it.",
            ev(rid, s1, s1 + WINDOW))
    else:
        say(5, f"On axis {ta} no aligned window reaches check.", ev(rid, o[0], n))
    on_f = agent.call("find_onsets", collection=C, recording_id=rid, start_sample=0, end_sample=n,
                      sta_seconds=0.25, lta_seconds=5.0, threshold=4.0, max_onsets=50)
    if on_f is not None:
        say(5, f"STA/LTA onsets on axis {ta}: {len(on_f['onsets'])}"
               + (f", the first at {secs(on_f['onsets'][0]['trigger_sample'])}" if on_f["onsets"] else "") + ".",
            ev(rid, 0, n))
    tr = agent.call("trend", collection=C, metadata_filter=json.dumps({"operation": op, "axis": ta}), max_points=300)
    if tr:
        rows = [r.split(",") for r in tr["table_csv"].splitlines()]
        i_r, i_rms = rows[0].index("recording_id"), rows[0].index("rms")
        vals = sorted(((float(r[i_rms]), r[i_r]) for r in rows[1:]), reverse=True)
        rank = next((k + 1 for k, (_, r) in enumerate(vals) if r == rid), None)
        facts["trend_rank"] = rank
        say(5, f"trend over the {tr['points']} {op} recordings of axis {ta}: rms rank {rank} of {len(vals)}, measured "
               f"on each recording's first 4,096 samples (#385), i.e. mostly the lead-in, not aligned.", ev(rid, 0, WINDOW))

    # 6. seen before (V2.3)
    def windows_of(m):
        return max(0, (m - WINDOW) // STRIDE + 1)
    others = {r: v for r, v in runs.items() if r != run}

    def chance(same):
        sel = [v for v in others.values() if (v["period"] == period) == same]
        tot = sum(windows_of(v["samples"]) for v in sel)
        return sum(windows_of(v["samples"]) for v in sel if v["label"] == "bad") / tot if tot else None
    facts["chance"] = {"all": (sum(windows_of(v["samples"]) for v in others.values() if v["label"] == "bad")
                               / sum(windows_of(v["samples"]) for v in others.values())),
                       "same_period": chance(True), "other_periods": chance(False)}

    def score(res, key):
        if res is None:
            return None
        meta_of = res["recordings"]
        rest = [h for h in res["hits"] if meta_of[h["recording_id"]]["run"] != run]
        out = {"hits": len(res["hits"]), "own_run_hits_dropped": len(res["hits"]) - len(rest)}
        for name, sel in (("all", rest), ("same_period", [h for h in rest if meta_of[h["recording_id"]]["period"] == period]),
                          ("other_periods", [h for h in rest if meta_of[h["recording_id"]]["period"] != period])):
            lab = [meta_of[h["recording_id"]]["label"] == "bad" for h in sel]
            out[name] = {"n": len(sel), "p@5": sum(lab[:5]) / 5 if len(lab) >= 5 else None,
                         "p@10": sum(lab[:10]) / 10 if len(lab) >= 10 else None}
        top10 = rest[:10]
        bad_runs = {meta_of[h["recording_id"]]["run"]: meta_of[h["recording_id"]]["period"] for h in top10
                    if meta_of[h["recording_id"]]["label"] == "bad"}
        out["bad_runs_top10"] = len(bad_runs)
        out["bad_runs_top10_other_periods"] = sum(p != period for p in bad_runs.values())
        out["top"] = [{"recording_id": h["recording_id"], "start_sample": h["start_sample"], "end_sample": h["end_sample"],
                       "distance": h["distance"], "label": meta_of[h["recording_id"]]["label"],
                       "period": meta_of[h["recording_id"]]["period"]} for h in top10]
        facts[key] = out
        return out
    ss = score(agent.call("search_similar", collection=C, recording_id=rid, start_sample=ws, end_sample=we,
                          metadata_filter=json.dumps({"operation": op}), top_k=50), "search_similar")
    sf = score(agent.call("search_from_time", collection=C, utc_time=R.desc[(run, ta, ws)]["start_utc"],
                          metadata_filter=json.dumps({"run": run, "axis": ta}),
                          search_filter=json.dumps({"operation": op}), top_k=50), "search_from_time")
    if ss:
        flagged = {}
        for h in ss["top"]:
            hr = h["recording_id"].rsplit("-", 1)[0]
            if hr not in flagged:
                d = agent.call("decisions", collection=C, recording_prefix=hr, limit=5)
                flagged[hr] = bool(d and d["decisions"])
        ch = facts["chance"]
        pl = lambda k, word: f"{k} {word}" + ("" if k == 1 else "s")  # noqa: E731

        def part(name, label):
            x = ss[name]
            if ch[name] is None:
                return f"{label}: no other runs there"
            if x["p@5"] is None:
                return f"{label}: only {pl(x['n'], 'hit')}, too few to score (chance {ch[name]:.0%})"
            return f"{label}: {x['p@5']:.0%} of the nearest 5 hits are bad (chance {ch[name]:.0%})"
        say(6, f"Similar to {pl(ss['bad_runs_top10'], 'other bad run')} among the nearest 10 windows, "
               f"{ss['bad_runs_top10_other_periods']} of them from other periods. "
               + part("same_period", f"Same period ({period.replace('_', ' ')})") + "; "
               + part("other_periods", "other periods") + f". {pl(ss['own_run_hits_dropped'], 'hit')} from this run "
               f"itself dropped (#384); {sum(flagged.values())} of {len(flagged)} hit runs carry a logged flag.",
            *[ev(h["recording_id"], h["start_sample"], h["end_sample"]) for h in ss["top"][:5]])
    if sf:
        say(6, f"search_from_time on the same moment: {sf['bad_runs_top10']} bad runs among the nearest 10, "
               f"{sf['bad_runs_top10_other_periods']} from other periods.",
            *[ev(h["recording_id"], h["start_sample"], h["end_sample"]) for h in sf["top"][:5]])

    # 7. evidence
    raw = agent.call("read_interval", collection=C, recording_id=rid, start_sample=ws, end_sample=we, max_points=WINDOW)
    if raw and "samples" in raw:
        say(7, f"The {len(raw['samples'])} original samples of that window read back from the database; they come "
               f"from {meta['file']} (SHA-256 {meta['sha256'][:12]}...).", ev(rid, ws, we))

    # recommendation (V2.5)
    where = f"{secs(ws)}..{secs(we)} on axis {ta} (aligned window {ti})"
    if D >= th["inspect"]:
        rec = f"Inspect this part at {where}: the deviation is above what 95% of good runs show."
    elif D >= th["check"]:
        rec = f"Check this part at {where}: the deviation is above what 80% of good runs show."
    else:
        rec = "No clear deviation: this run is within what 80% of good runs show."
    if ss:
        k = ss["bad_runs_top10"]
        rec += (f" Similar to {k} other bad run{'' if k == 1 else 's'}, {ss['bad_runs_top10_other_periods']} of them "
                f"from other periods.")
    facts["recommendation"] = rec
    facts["fires"] = "inspect" if D >= th["inspect"] else "check" if D >= th["check"] else "none"
    say(8, "Recommendation: " + rec, ev(rid, ws, we))
    return done()


def render(facts, st, wall):
    out = [f"### {facts['run']}", ""]
    if "error" in facts:
        out.append(f"Investigation stopped: {facts['error']}.")
    heads = {1: "What it is", 2: "Whole run", 3: "What is different, and where", 4: "Level or shape",
             5: "Since when", 6: "Seen before, and what happened then", 7: "Evidence", 8: "Recommendation"}
    for q in sorted({s["q"] for s in st}):
        out.append(f"**{heads[q]}.**")
        for s in (x for x in st if x["q"] == q):
            out.append(f"- {s['text']}" + "".join(f"\n  - `{e}`" for e in s["evidence"]))
        out.append("")
    out.append(f"_{len(st)} statements, {sum(1 for s in st if s['evidence'])} with evidence; {wall:.1f} s._")
    return "\n".join(out) + "\n"


def main():
    out = Path(sys.argv[1])
    (out / "answers").mkdir(parents=True, exist_ok=True)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    agent = Agent()
    cal_path = out / "calibration.json"
    cal = json.loads(cal_path.read_text()) if cal_path.exists() else calibrate(agent, out)
    print(f"thresholds (pooled): {cal['pooled']}", flush=True)
    runs = sys.argv[2:]
    if not runs:
        lst = agent.call("list_recordings", collection=C, metadata_filter=json.dumps({"label": "bad", "axis": "x"}),
                         include_metadata=True, limit=500)
        runs = sorted(r["metadata"]["run"] for r in lst["recordings"])
    summary, md = [], ["# Why this part? Answers for the flagged M01 runs (version 2)", "",
                       "Generated by `demo_agent_v2.py`; see `../README.md`. Development data, not study results.", ""]
    for run in runs:
        facts, st, calls, wall = investigate(agent, run, cal)
        facts.update(wall_s=round(wall, 2), tool_calls=len(calls), statements=len(st),
                     statements_with_evidence=sum(1 for s in st if s["evidence"]),
                     tool_errors=[{"tool": c["tool"], "error": c["error"]} for c in calls if "error" in c])
        text = render(facts, st, wall)
        (out / "answers" / f"{run}.md").write_text(text)
        with gzip.open(out / "transcripts" / f"{run}.json.gz", "wt") as f:
            json.dump({"run": run, "facts": facts, "statements": st, "calls": calls}, f)
        summary.append(facts)
        md.append(text)
        print(f"{run}: {len(calls)} calls, {len(facts['tool_errors'])} errors, {wall:.1f} s, D {facts.get('top', {}).get('abs_db')}, "
              f"fires {facts.get('fires')}, kind {facts.get('kind')}, top in flagged {facts.get('pool_top_in_flagged')}",
              flush=True)
    (out / "answers.md").write_text("\n".join(md))
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
