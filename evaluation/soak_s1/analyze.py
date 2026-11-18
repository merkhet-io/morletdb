#!/usr/bin/env python3
"""Soak S1 analysis (PROTOCOL.md, "Hypotheses"): one verdict per hypothesis, with its numbers, for the test week.

    python3 analyze.py RESULTS_DIR --start 2026-11-10T16:30:00Z [--spool SPOOL_DIR] [--verify-fdsn R.json ...]
                       [--morlet-verify V.jsonl] [--manual-actions 0] [--json OUT]

Inputs (all JSON lines unless noted), from the soak file system:
- RESULTS_DIR/probe.jsonl        the probe (probe.py): latencies, stored samples, coverage
- RESULTS_DIR/host.jsonl         the host probe (host_probe.sh): containers, restarts, OOM, steward status, collector,
                                 and (shakedown change 7) each container's memory.stat anon, shmem and file
- RESULTS_DIR/disturbances.jsonl D1 and D2 as carried out (disturb.sh): time, and when storing resumed
- SPOOL_DIR/gaps.jsonl           the collector's segment ends, reconnects and replays (default RESULTS_DIR/../spool)
- --verify-fdsn                  the reports of verify_fdsn.py --out (H1), one or more
- --morlet-verify                the output of tools/verify/morlet_verify.py --json at the end (H1)
- --manual-actions               the owner's count of manual actions in the week besides D1 and D2 (H2)

The week is [start, start + --days). H4 compares the first and the last --window-hours of it (24 by the protocol).
An input that is missing makes its check PENDING, never PASS. Exit status: 0 all pass, 1 any fail, 2 pending.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

NOMINAL = 3600.0          # samples/s: 12 stations x 3 channels x 100 Hz
HOUR = 3600.0
SOAK = ("collector", "db", "probe", "steward")   # the long-running soak containers (setup and init exit by design)


def ts(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def iso(t: float | None) -> str:
    return "-" if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def lines(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                out.append({"unparsable": ln[:200]})
    return out


def p95(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, math.ceil(0.95 * len(xs)) - 1)]


def mib(s: str) -> float | None:
    """'649.2MiB / 1GiB' -> 649.2 (the used part)."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?i?B)", s or "")
    if not m:
        return None
    f = {"B": 1 / 2**20, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024, "TiB": 2**20}
    return float(m.group(1)) * f.get(m.group(2), 1)


def container(name: str) -> str | None:
    """'soak-s1-db-1' or '/soak-s1-db-1' -> 'db'."""
    m = re.match(r"/?soak-s1-(.+)-\d+$", name)
    return m.group(1) if m else None


def ratio(a, b):
    return None if a is None or b in (None, 0) else a / b


def fmt(x, nd=2):
    return "-" if x is None else (f"{x:,.{nd}f}" if isinstance(x, float) else f"{x:,}")


class Verdicts:
    def __init__(self):
        self.rows = []

    def add(self, hyp, check, ok, numbers):
        """ok: True, False or None (pending)."""
        self.rows.append({"hypothesis": hyp, "check": check,
                          "verdict": "PASS" if ok is True else "FAIL" if ok is False else "PENDING",
                          "numbers": numbers})

    def hypothesis(self, h):
        v = [r["verdict"] for r in self.rows if r["hypothesis"] == h]
        return "FAIL" if "FAIL" in v else "PENDING" if "PENDING" in v else "PASS"


def analyze(a) -> dict:
    res = Path(a.results)
    spool = Path(a.spool) if a.spool else res.parent / "spool"
    t0 = ts(a.start)
    t1 = t0 + a.days * 86400
    w = a.window_hours * HOUR
    inweek = lambda r: t0 <= r.get("at", -1) < t1
    probe = [r for r in lines(res / "probe.jsonl") if inweek(r)]
    host = [r for r in lines(res / "host.jsonl") if inweek(r)]
    gaps = [r for r in lines(spool / "gaps.jsonl") if inweek(r)]
    dist = {d.get("id"): d for d in lines(res / "disturbances.jsonl") if inweek(d)}
    harness = None
    if "D1r" in dist:   # amendment 1: D1 by docker kill was a manual stop; D1r is the crash H3 evaluates
        harness = dist.pop("D1")
        dist["D1"] = dist.pop("D1r")
    last = max([r["at"] for r in probe + host] or [t0])
    complete = last >= t1 - 2 * 900       # the week has run to its end (within two probe rounds)
    V = Verdicts()
    S = {"d1_harness": None if harness is None else {"docker_kill": harness, "manual_start_at": iso(1794508020.6),
                                                     "note": "amendment 1: docker kill is a manual stop; D1r repeats D1"},
         "week": {"start": iso(t0), "end": iso(t1), "last_record": iso(last), "complete": complete,
                  "probe_rounds": len(probe), "host_records": len(host), "gap_lines": len(gaps)}}

    # ---- H1, nothing lost or changed ---------------------------------------------------------------------------
    if a.verify_fdsn:
        tot = {"compared": 0, "different": 0, "not_in_archive": 0, "errors": 0, "recordings": 0}
        for f in a.verify_fdsn:
            rep = json.loads(Path(f).read_text())
            t = rep.get("total", rep) if isinstance(rep, dict) else {}
            for k in tot:
                tot[k] += int(t.get(k, 0))
        V.add("H1", "verify_fdsn: 0 samples differ from the archive", tot["different"] == 0 and tot["compared"] > 0,
              tot)
    else:
        V.add("H1", "verify_fdsn: 0 samples differ from the archive", None, "report not given")
    if a.morlet_verify:
        summ, bad = None, 0
        for r in lines(Path(a.morlet_verify)):
            if "summary" in r:
                summ = r["summary"]
            elif r.get("problems"):
                bad += 1
        ok = None if summ is None else (summ.get("recordings_with_problems", bad) == 0 and bad == 0)
        V.add("H1", "morlet_verify at the end: 0 problems", ok, summ or "no summary line")
    else:
        V.add("H1", "morlet_verify at the end: 0 problems", None, "output not given")
    holes = [g for g in gaps if g.get("cause") == "spool hole on replay"]
    V.add("H1", "no 'spool hole on replay' in the collector's gaps.jsonl", len(holes) == 0 if gaps or complete else None,
          {"holes": len(holes)})

    # ---- H2, unattended ------------------------------------------------------------------------------------------
    V.add("H2", "no manual action besides D1 and D2", None if a.manual_actions is None else a.manual_actions == 0,
          "owner's statement not given" if a.manual_actions is None else {"manual_actions": a.manual_actions})
    starts, ooms, restarts = {}, [], {}
    prev = {}
    for r in host:
        for name, st in (r.get("state") or {}).items():
            c = container(name)
            if c not in SOAK:
                continue
            if st.get("oom"):
                ooms.append((iso(r["at"]), c))
            restarts[c] = st.get("restarts", 0)
            s = st.get("started")
            if c in prev and s != prev[c]:
                starts.setdefault(c, []).append(r["at"])
            prev[c] = s
    allowed = {"D1": "collector", "D2": "db"}
    if harness:         # amendment 1: the manual start after the docker kill is explained by it
        dist["D1_docker_kill"] = harness
        allowed["D1_docker_kill"] = "collector"
    unexplained = []
    for c, times in starts.items():
        for t in times:   # a start seen in the first host record after a disturbance (at most 30 min later) is it
            d = next((k for k, v in allowed.items() if v == c and k in dist and 0 <= t - dist[k]["at"] <= 1800), None)
            if d:
                allowed.pop(d)
            else:
                unexplained.append((iso(t), c))
    V.add("H2", "no container restart besides D1 and D2, no OOM kill", not unexplained and not ooms,
          {"unexplained_starts": unexplained, "oom": ooms, "docker_restart_counts": restarts})
    levels = []
    for r in host:
        txt = r.get("steward")
        lvl = (txt or "").strip().splitlines()[0] if txt else None
        levels.append((r["at"], lvl, txt or ""))
    worst, run_start = 0.0, None
    for i, (t, lvl, _) in enumerate(levels):
        if lvl == "ACTION REQUIRED":
            run_start = t if run_start is None else run_start
            end = levels[i + 1][0] if i + 1 < len(levels) else t
            worst = max(worst, end - run_start)
        else:
            run_start = None
    hold = [iso(t) for t, _, txt in levels if re.search(r"ingest", txt, re.I) and re.search(r"\bhold", txt, re.I)]
    unknown = sum(1 for _, lvl, _ in levels if lvl not in ("NORMAL", "ATTENTION", "ACTION REQUIRED"))
    counts = {k: sum(1 for _, lvl, _ in levels if lvl == k) for k in ("NORMAL", "ATTENTION", "ACTION REQUIRED")}
    V.add("H2", "steward never ACTION REQUIRED > 1 h, never sets the ingest hold",
          (worst <= HOUR and not hold) if levels else None,
          {"status_records": counts, "unreadable": unknown, "longest_action_required_h": round(worst / HOUR, 2),
           "hold_seen": hold})

    # ---- H3, recovery --------------------------------------------------------------------------------------------
    for k, what in (("D1", "collector kill"), ("D2", "database restart")):
        d = dist.get(k)
        if not d:
            V.add("H3", f"{k} ({what}): storing resumes within 5 min", None if not complete else False,
                  "not carried out yet" if not complete else "missing from disturbances.jsonl")
            continue
        rs = d.get("resume_s")
        V.add("H3", f"{k} ({what}): storing resumes within 5 min", None if rs is None else rs <= 300,
              {"at": iso(d["at"]), "resume_s": rs, "stored_before": d.get("stored_before"),
               "stored_after": d.get("stored_after")})
    d1 = dist.get("D1")
    if d1:
        rep = [g for g in gaps if g.get("cause") == "collector restart: spool replayed" and g["at"] >= d1["at"]]
        hole = [g for g in holes if g["at"] >= d1["at"]]
        V.add("H3", "D1: the spool is replayed with no hole", bool(rep) and not hole,
              {"replay_lines": len(rep), "holes": len(hole)})
    else:
        V.add("H3", "D1: the spool is replayed with no hole", None if not complete else False, "D1 not carried out")

    # ---- H4, steady ----------------------------------------------------------------------------------------------
    lw0 = t1 - w if complete else last - w      # an incomplete run: its last window so far (reported, not final)
    first = [r for r in probe if r["at"] < t0 + w]
    lastw = [r for r in probe if r["at"] >= lw0]
    S["h4_windows"] = {"first": [iso(t0), iso(t0 + w)], "last": [iso(lw0), iso(t1 if complete else last)],
                       "overlap": lw0 < t0 + w}

    def series(rs, key):
        out = []
        for r in rs:
            for c in r.get("checks", []):
                x = c.get(key)
                if not x or x.get("error"):
                    continue
                if key == "search":
                    if x.get("history"):
                        out.append(x["s"] / x["history"])
                else:
                    out.append(x["s"])
        return out

    def abs_search(rs):
        return [c["search"]["s"] for r in rs for c in r.get("checks", []) if c.get("search") and not c["search"].get("error")]

    for key, label, lim in (("latest", "p95 of latest", 2.0), ("stats", "p95 of interval statistics", 2.0),
                            ("search", "p95 of search latency per searched sample", 2.0)):
        f, l = p95(series(first, key)), p95(series(lastw, key))
        r_ = ratio(l, f)
        nums = {"first": f, "last": l, "ratio": None if r_ is None else round(r_, 3),
                "n": [len(series(first, key)), len(series(lastw, key))]}
        if key == "search":
            nums["absolute_p95_s"] = [p95(abs_search(first)), p95(abs_search(lastw))]
        V.add("H4", f"{label}: last window at most {lim}x the first", None if r_ is None or not complete else r_ <= lim, nums)
    hf = [r for r in host if r["at"] < t0 + w]
    hl = [r for r in host if r["at"] >= lw0]

    def peak(rs, c):
        xs = [mib(v.get("mem")) for r in rs for n, v in (r.get("containers") or {}).items() if container(n) == c]
        xs = [x for x in xs if x is not None]
        return max(xs) if xs else None

    def peak_proc(rs, c):
        """Shakedown change 7: the container's process memory, anon + shmem from cgroup memory.stat, in MiB."""
        xs = [(v["anon"] + v["shmem"]) / 2**20 for r in rs for n, v in (r.get("memstat") or {}).items() if container(n) == c]
        return max(xs) if xs else None

    def peak_file(rs, c):
        xs = [v["file"] / 2**20 for r in rs for n, v in (r.get("memstat") or {}).items() if container(n) == c]
        return round(max(xs), 1) if xs else None

    f, l = peak_proc(hf, "db"), peak_proc(hl, "db")
    r_ = ratio(l, f)
    V.add("H4", "db peak process memory (anon + shmem): last window at most 1.25x the first",
          None if r_ is None or not complete else r_ <= 1.25,
          {"first_mib": None if f is None else round(f, 1), "last_mib": None if l is None else round(l, 1),
           "ratio": None if r_ is None else round(r_, 3),
           "page_cache_peak_mib_reported": [peak_file(hf, "db"), peak_file(hl, "db")],
           "docker_figure_peak_mib_reported": [peak(hf, "db"), peak(hl, "db")]})
    f, l = peak(hf, "collector"), peak(hl, "collector")
    r_ = ratio(l, f)
    V.add("H4", "collector peak memory: last window at most 1.25x the first", None if r_ is None or not complete else r_ <= 1.25,
          {"first_mib": f, "last_mib": l, "ratio": None if r_ is None else round(r_, 3)})

    # ---- H5, keeps up --------------------------------------------------------------------------------------------
    backlog = [((r.get("collector") or {}).get("backlog"), r["at"]) for r in host]
    backlog = [(b, t) for b, t in backlog if b is not None]
    mx = max(backlog, default=(None, None))
    V.add("H5", "collector backlog never above 10,000 packets", None if not backlog else mx[0] <= 10000,
          {"max_packets": mx[0], "at": iso(mx[1]), "snapshots": len(backlog)})
    pr = [r for r in probe if "stored_samples" in r]
    endp = pr[-1] if pr else None
    if endp and "coverage" in endp:
        cov = endp["coverage"]
        stored = endp["stored_samples"]
        unsearchable = stored - cov.get("searchable", 0)
        older = max(1, stored - int(NOMINAL * HOUR))     # stored samples older than one hour, at the nominal rate
        frac = min(1.0, cov.get("searchable", 0) / older)
        V.add("H5", "at the end, >= 99% of samples older than 1 h searchable", frac >= 0.99 if complete else None,
              {"searchable_fraction_of_older": round(frac, 5), "unsearchable_h_of_feed": round(unsearchable / NOMINAL / HOUR, 3),
               "at": iso(endp["at"])})
    else:
        V.add("H5", "at the end, >= 99% of samples older than 1 h searchable", None, "no coverage in probe.jsonl")
    if len(pr) >= 2:
        span = pr[-1]["at"] - pr[0]["at"]
        rate = (pr[-1]["stored_samples"] - pr[0]["stored_samples"]) / span if span > 0 else None
        share = None if rate is None else rate / NOMINAL
        causes = {}
        for g in gaps:
            causes[g.get("cause", "?")] = causes.get(g.get("cause", "?"), 0) + 1
        explained = {k: v for k, v in causes.items() if k not in ("new UTC day",)}
        ok = None if share is None else (True if share >= 0.97 else (None if explained else False))
        V.add("H5", "stored >= 97% of the nominal 3,600 samples/s (shortfall explained)", ok if complete else None,
              {"stored_per_s": None if rate is None else round(rate, 1), "share_of_nominal": None if share is None else round(share, 4),
               "gap_causes": causes,
               "note": "below 97%: PENDING until each shortfall is matched to a logged gap or D1/D2" if ok is None and share else ""})
    else:
        V.add("H5", "stored >= 97% of the nominal 3,600 samples/s (shortfall explained)", None, "fewer than 2 probe rounds")

    # ---- series summaries and the stop rule ----------------------------------------------------------------------
    unhealthy = []
    for r in host:
        for n, s in (r.get("other_services") or {}).items():
            if "unhealthy" in s or not s.startswith("Up"):
                unhealthy.append((r["at"], n))
    worst_unhealthy = 0.0
    by = {}
    for t, n in unhealthy:
        by.setdefault(n, []).append(t)
    for n, ts_ in by.items():   # consecutive 15-minute snapshots
        run = 900.0
        for x, y in zip(ts_, ts_[1:]):
            run = run + (y - x) if y - x <= 1000 else 900.0
            worst_unhealthy = max(worst_unhealthy, run)
        worst_unhealthy = max(worst_unhealthy, 900.0 if ts_ else 0)
    S["stop_rule_household"] = {"unhealthy_snapshots": len(unhealthy), "longest_unhealthy_min": round(worst_unhealthy / 60),
                                "services": sorted(by)}
    S["probe_errors"] = {"failed_rounds": sum(1 for r in probe if "error" in r),
                         "check_errors": sum(1 for r in probe for c in r.get("checks", []) for k in ("latest", "stats", "search")
                                             if (c.get(k) or {}).get("error"))}
    if pr:
        unb = [(r["at"], (r["stored_samples"] - r.get("coverage", {}).get("searchable", r["stored_samples"])) / NOMINAL / HOUR)
               for r in pr if "coverage" in r]
        if unb:
            half = [h for t, h in unb if t >= unb[len(unb) // 2][0]]
            S["unsearchable_backlog_h"] = {"max": round(max(h for _, h in unb), 3), "last": round(unb[-1][1], 3),
                                           "max_second_half": round(max(half), 3)}
        S["database"] = {"stored_samples": pr[-1]["stored_samples"], "recordings": pr[-1].get("recordings"),
                         "db_gib": round(pr[-1].get("db_bytes", 0) / 2**30, 3)}
    fs = [r["soak_fs"] for r in host if r.get("soak_fs")]
    if fs:
        S["soak_fs_used_gib"] = {"first": round(fs[0]["used"] / 2**30, 2), "last": round(fs[-1]["used"] / 2**30, 2)}
    loads = [r["load"][1] for r in host if r.get("load")]
    if loads:
        S["host_load_5min"] = {"median": round(sorted(loads)[len(loads) // 2], 2), "max": round(max(loads), 2)}
    S["memory_peak_mib"] = {c: peak(host, c) for c in SOAK}
    S["process_memory_peak_mib"] = {c: None if peak_proc(host, c) is None else round(peak_proc(host, c), 1) for c in SOAK}
    return {"verdicts": V.rows, "hypotheses": {h: V.hypothesis(h) for h in ("H1", "H2", "H3", "H4", "H5")},
            "summary": S}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="the soak's results directory")
    ap.add_argument("--start", required=True, help="test-week start, ISO UTC or epoch seconds (PROTOCOL.md, Frozen)")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--window-hours", type=float, default=24.0, help="H4's first and last window")
    ap.add_argument("--spool", help="the collector's spool directory (default RESULTS/../spool)")
    ap.add_argument("--verify-fdsn", nargs="*", default=[])
    ap.add_argument("--morlet-verify")
    ap.add_argument("--manual-actions", type=int)
    ap.add_argument("--json", help="also write the full result as JSON")
    a = ap.parse_args(argv)
    out = analyze(a)
    print(f"Soak S1, week {out['summary']['week']['start']} .. {out['summary']['week']['end']} "
          f"(last record {out['summary']['week']['last_record']}, complete: {out['summary']['week']['complete']})\n")
    print("| Hypothesis | Check | Verdict | Numbers |\n|---|---|---|---|")
    for r in out["verdicts"]:
        print(f"| {r['hypothesis']} | {r['check']} | {r['verdict']} | {json.dumps(r['numbers'], default=str)} |")
    print("\n" + "  ".join(f"{h}: {v}" for h, v in out["hypotheses"].items()))
    print("\n" + json.dumps(out["summary"], indent=1, default=str))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str) + "\n")
    v = set(out["hypotheses"].values())
    return 1 if "FAIL" in v else 2 if "PENDING" in v else 0


if __name__ == "__main__":
    sys.exit(main())
