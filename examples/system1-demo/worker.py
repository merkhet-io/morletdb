#!/usr/bin/env python3
"""System 1 demo worker: replays one insert's life on the NASA milling machine cut by cut, has a fast decision
model decide after each cut whether to replace the insert, and logs every decision in MorletDB (README.md).

  worker.py MILL_MAT [--case 10] [--decider jev|score] [--threshold 0.6] [--reload]

Each cut is decided from MorletDB's decision state only (tools/mcp/decision.py): the cut's deviations from earlier
cuts of the same setting with a sharp insert. --decider jev sends the state to TypeSafe's Jev (key read from
~/.config/typesafe/jev.key, never printed); --decider score uses the plain conditioned score (C1) and needs no
key. Decisions are stored as labels "s1:replace_insert" or "s1:continue" on the cut's six recordings, with the
model, probability, threshold and the exact state, so Claude can explain them later over MCP (tool `decisions`).

DATABASE_URL must name a role that may write (the demo's loader role), not the MCP server's read-only role.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from scipy import io as sio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "mcp"))
sys.path.insert(0, str(ROOT / "evaluation" / "system1_j1"))
sys.path.insert(0, str(ROOT / "evaluation" / "context"))
import decision  # noqa: E402
import jev  # noqa: E402
import states  # noqa: E402

COLLECTION = "public.milling"


def decide_jev(state, api_key):
    body = {"model": jev.MODEL, "state": state, "questions": {"replace_insert": jev.QUESTIONS["replace_insert"]}}
    res = jev.call(body, api_key)
    if res["http_status"] != 200:
        raise SystemExit(f"Jev call failed: HTTP {res['http_status']}")
    return res["response"]["answers"]["replace_insert"]["noul"], res["response"].get("model", jev.MODEL), res["wall_ms"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mill_mat")
    ap.add_argument("--case", type=int, default=10)
    ap.add_argument("--decider", choices=("jev", "score"), default="jev")
    ap.add_argument("--threshold", type=float, default=None,
                    help="decide replace_insert at or above this (default 0.6 for jev, 1.0 for score)")
    ap.add_argument("--reload", action="store_true", help="rebuild the milling collection first")
    a = ap.parse_args()
    threshold = a.threshold if a.threshold is not None else (0.6 if a.decider == "jev" else 1.0)
    api_key = jev.key() if a.decider == "jev" else None
    mill = sio.loadmat(a.mill_mat, squeeze_me=True, struct_as_record=False)["mill"]
    with psycopg.connect(os.environ.get("DATABASE_URL", states.DSN), autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('public.milling') IS NOT NULL").fetchone()[0]
        if a.reload or not exists:
            bad = {(int(r.case), int(r.run)) for r in mill for _, (f, _) in states.CHANNELS.items()
                   if not np.all(np.isfinite(getattr(r, f))) or np.abs(getattr(r, f)).max() > 1e9}
            print("loading NASA milling into MorletDB ...")
            states.load(conn, [r for r in mill if (int(r.case), int(r.run)) not in bad])
        cuts = sorted((int(r.run), float(r.VB) if np.size(r.VB) == 1 and np.isfinite(float(r.VB)) else None)
                      for r in mill if int(r.case) == a.case)
        print(f"case {a.case}: {len(cuts)} cuts; decider {a.decider}, replace at >= {threshold}")
        print(f"{'cut':>4} {'VB mm':>6} {'p':>6} {'state ms':>9} {'model ms':>9}  decision")
        for run, vb in cuts:
            t = time.perf_counter()
            try:
                state, score, _ = states.run_state(conn, a.case, run)
            except ValueError as exc:
                print(f"{run:>4}  skipped: {exc}")
                continue
            state_ms = 1e3 * (time.perf_counter() - t)
            if a.decider == "jev":
                p, model, model_ms = decide_jev(state, api_key)
            else:
                p, model, model_ms = score, "conditioned-score (C1)", 0.0
            answer = "replace_insert" if p >= threshold else "continue"
            segs = [(f"c{a.case:02d}r{run:03d}-{v}", 0, 9000) for v in states.CHANNELS]
            decision.log_decision(conn, COLLECTION, segs, answer, model=model, question="replace_insert",
                                  probability=p, threshold=threshold, state=state,
                                  extra={"cut": f"case {a.case} run {run}"})
            vbs = f"{vb:.2f}" if vb is not None else "  -"
            print(f"{run:>4} {vbs:>6} {p:>6.2f} {state_ms:>9.0f} {model_ms:>9.0f}  {answer}")
    print("decisions logged; ask Claude over MCP to explain them (README.md)")


if __name__ == "__main__":
    main()
