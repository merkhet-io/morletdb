#!/usr/bin/env python3
"""Replay archived industrial analyses, without a database or waveform download.

Outputs go to a separate directory. --check compares with committed derived results;
it never overwrites them. Requires numpy (the measured analyses used 2.1.x).
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
JOBS = {
    "iu1": ("industrial_iu1/analyze.py", [], "summary.json"),
    "drift": ("industrial_iu1/drift_analyze.py", [], "drift_summary.json"),
    "leadin": ("industrial_iu1/leadin.py", [], "leadin_summary.json"),
    "iu2": ("industrial_iu2/iu2_analyze.py", ["--from-cycle-scores"], "summary.json"),
}


def equivalent(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10)
    return a == b


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--studies", nargs="+", choices=JOBS, default=list(JOBS))
    args = parser.parse_args()
    if not args.output and not args.check:
        parser.error("use --output DIR or --check")
    with tempfile.TemporaryDirectory(prefix="morlet-industrial-replay-") as temporary:
        dest = args.output.resolve() if args.output else Path(temporary)
        for name in args.studies:
            relative, extra, result = JOBS[name]
            script = ROOT / "evaluation" / relative
            source = script.parent / "results"
            target = dest / name
            if target.resolve() == source.resolve():
                parser.error("output must differ from archived input directory")
            target.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, MORLET_ANALYSIS_INPUT=str(source), MORLET_ANALYSIS_OUTPUT=str(target))
            print(f"Replaying {name} (10,000 bootstrap resamples where specified)", flush=True)
            with (target / "replay.log").open("w") as log:
                subprocess.run([sys.executable, "-I", str(script), *extra], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            if args.check:
                actual = json.loads((target / result).read_text())
                expected = json.loads((source / result).read_text())
                if not equivalent(actual, expected):
                    raise SystemExit(f"{name}: differs from archived derived result")
            print(f"{name}: OK", flush=True)


if __name__ == "__main__":
    main()
