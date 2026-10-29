#!/usr/bin/env python3
"""C7, post hoc (decides nothing; RESULTS.md "Post hoc: alarm direction").

The score is two-sided (|x - median| / |median|), so an alarm can come from a value *falling* below
the baseline, for example as vibration settles after run-in. For each horizontal-channel alarm this
reports whether the value behind it was above or below its reference at the alarm snapshot:

- H-PIN: the alarm's top value against the pin's median;
- H-ALL: the alarm's top value against the median of all earlier snapshots;
- B-RMS: RMS against the median of the first 30 snapshots;
- the no-crest arms: whichever of std and kurtosis deviates most from its reference (the harness
  does not store their top value).

It then recomputes Q1 and Q2, with and without crest, with alarms driven by a fall counted as no
alarm (lead 0). That is a sensitivity check written after the verdicts, not a rule of the protocol.

  posthoc_direction.py OUT     (OUT holds values/ and c7.json from `c7.py run`)
"""
import copy
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c7  # noqa: E402
c5 = c7.c5

CHANNEL = 0  # horizontal, the decided channel
PAIRS = (("H-PIN", "H-ALL"), ("H-PIN", "B-RMS"), ("H-PIN-no-crest", "H-ALL-no-crest"), ("H-PIN-no-crest", "B-RMS"))
ARMS = ("H-PIN", "H-ALL", "B-RMS", "H-PIN-no-crest", "H-ALL-no-crest")


def direction(values, arm, row):
    i = row["alarm"]
    ref = values[:c7.PIN] if arm.startswith("H-PIN") or arm == "B-RMS" else values[:i]
    if arm == "B-RMS":
        j = c5.RMS
    elif "top_value" in row:
        j = c5.NAMES.index(row["top_value"])
    else:
        j = max((c5.NAMES.index(n) for n in ("std", "kurtosis")),
                key=lambda k: abs(values[i, k] / np.median(ref[:, k]) - 1))
    med = float(np.median(ref[:, j]))
    return {"value": c5.NAMES[j], "at_alarm": float(values[i, j]), "reference_median": med,
            "direction": "up" if values[i, j] > med else "down"}


def main(out):
    out = Path(out)
    res = json.loads((out / "c7.json").read_text())
    rows = res["channels"]["horizontal"]["per_bearing"]
    report, upward_only = {}, copy.deepcopy(rows)
    for b in c7.BEARINGS:
        values = np.load(out / "values" / f"{b}.npz")["values"][CHANNEL]
        report[b] = {}
        for arm in ARMS:
            r = rows[b][arm]
            if r["alarm"] is None:
                report[b][arm] = None
                continue
            report[b][arm] = {"life_fraction": r["life_fraction"], "stable": r["stable"], **direction(values, arm, r)}
            if report[b][arm]["direction"] == "down":
                upward_only[b][arm]["lead"] = 0.0
    result = {
        "note": "post hoc; decides nothing",
        "per_bearing": report,
        "downward_alarms": {arm: sum(1 for b in c7.BEARINGS if report[b][arm] and report[b][arm]["direction"] == "down")
                            for arm in ARMS},
        "upward_only": {f"{c} vs {k}": c5.decide(upward_only, c7.BEARINGS, c, k) for c, k in PAIRS},
    }
    (out / "posthoc_direction.json").write_text(json.dumps(result, indent=1))
    for b in c7.BEARINGS:
        cells = []
        for arm in ARMS:
            x = report[b][arm]
            cells.append("-" if x is None else
                         f"{x['life_fraction']:.2f} {x['value']} {x['direction']}")
        print(f"{b:<12}" + " | ".join(f"{c:<20}" for c in cells))
    print("downward alarms", result["downward_alarms"])
    for q, d in result["upward_only"].items():
        print(f"upward only {q}: delta {d['delta']:+.3f} CI [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}] -> {d['verdict']}")


if __name__ == "__main__":
    main(sys.argv[1])
