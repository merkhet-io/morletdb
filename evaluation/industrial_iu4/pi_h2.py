#!/usr/bin/env python3
"""IU4 H2 on the Pi (PROTOCOL.md, Latency): one decision = segments_at + decision_states for the run's 9 signals +
C's prediction (full arm), for the last 50 runs of T4 and of T7, after 10 warm-up runs. The end-of-run build
(context, segments, values), reported without criterion, is measured separately after this run.

  pi_h2.py MODEL_JSON OUT_JSON      (MORLET_DSN; collection public.dk loaded by pi_load4.py, under e512)

Gate before the run: 5-minute load below 1. Device state (load, SoC temperature, clock, throttle flags) before, every 10
decisions and after.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import iu4  # noqa: E402
import decision  # noqa: E402  (iu4 put tools/mcp on the path)
from features import SIGNALS, feature_dict, predict  # noqa: E402


def device():
    def run(*a):
        try:
            return subprocess.run(a, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"
    return {"load": open("/proc/loadavg").read().split()[:3], "temp": run("vcgencmd", "measure_temp"),
            "clock": run("vcgencmd", "measure_clock", "arm"), "throttled": run("vcgencmd", "get_throttled"),
            "t": time.strftime("%Y-%m-%dT%H:%M:%S")}


def gate():
    while float(open("/proc/loadavg").read().split()[1]) >= 1.0:
        time.sleep(30)


def runs_of(c, machine, tool):
    return [r[0] for r in c.execute(
        "SELECT DISTINCT (metadata->>'run')::int FROM dk WHERE metadata->>'machine' = %s AND metadata->>'tool' = %s "
        "ORDER BY 1", (str(machine), str(tool)))]


def decide(c, model, machine, tool, run, base):
    segs = decision.segments_at(c, iu4.COLL, [(iu4.rid(machine, tool, run, s), 0) for s in SIGNALS])
    states = decision.decision_states(c, iu4.COLL, segs, match_keys=["process", "machine", "tool"], min_history=3,
                                      neighbors=3, baselines={sg: base[s] for sg, s in zip(segs, SIGNALS)})
    return predict(model, feature_dict(dict(zip(SIGNALS, states)), "full"))[0]


def main():
    model = json.loads(Path(sys.argv[1]).read_text())["full"]
    out = {"device_before": None, "decisions_ms": [], "device": []}
    with iu4.connect() as c:
        plan = []
        for machine, tool in ((2, 4), (3, 7)):
            runs = runs_of(c, machine, tool)
            base = {s: [decision.segment_at(c, iu4.COLL, iu4.rid(machine, tool, k, s), 0) for k in range(1, 11)]
                    for s in SIGNALS}
            plan.append((machine, tool, runs[-60:-50], runs[-50:], base))
        gate()
        out["device_before"] = device()
        for machine, tool, warm, _timed, base in plan[:1]:
            for run in warm:
                decide(c, model, machine, tool, run, base)
        for machine, tool, _warm, timed, base in plan:
            for run in timed:
                t = time.perf_counter()
                decide(c, model, machine, tool, run, base)
                out["decisions_ms"].append((time.perf_counter() - t) * 1000)
                if len(out["decisions_ms"]) % 10 == 0:
                    out["device"].append(device())
        out["device_after"] = device()
    ms = sorted(out["decisions_ms"])
    out["p50_ms"], out["p95_ms"], out["max_ms"] = ms[len(ms) // 2], ms[int(0.95 * len(ms)) - 1], ms[-1]
    out["H2"] = "pass" if out["p95_ms"] <= 250 else "fail"
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("p50_ms", "p95_ms", "max_ms", "H2")}))


if __name__ == "__main__":
    main()
