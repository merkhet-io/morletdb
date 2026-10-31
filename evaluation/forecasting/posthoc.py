#!/usr/bin/env python3
"""F1, POST HOC (not in the frozen protocol): sensitivity of the gate result to the two very
short bearings (1_5: 13 origins, 2_4: 3 origins at H=10) and to origin-weighted averaging.
Decides nothing; explains the gate result.

  .venv-seismic/bin/python -I evaluation/forecasting/posthoc.py evaluation/forecasting/results
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402

METHODS = ("persistence", "ar", "knn", "random", "shuffled")


def main():
    out = Path(sys.argv[1])
    r = json.load(open(out / "gate.json"))
    res = {}
    for h in ("H1", "H10", "H30"):
        rows = r[h]["rows"]
        kept = {q: v for q, v in rows.items() if q not in ("1_5", "2_4")}
        s = gate.summarize(kept)
        res[h] = {"excluding_1_5_and_2_4": {"mean_mae": s["mean_mae"], "best_baseline": s["best_baseline"],
                                            "knn_vs_best": s["knn_vs_best"]},
                  "origin_weighted_mae": {m: sum(v["mae"][m] * v["origins"] for v in rows.values())
                                          / sum(v["origins"] for v in rows.values()) for m in METHODS}}
    json.dump(res, open(out / "posthoc.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
