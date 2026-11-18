#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The reference investigation, without a language model: direct queries, every hop recorded.

  DATABASE_URL=postgresql://fleet_reader@host/morlet python examples/fleet-investigator/investigate.py \
      [--unit P-102] [--at 2004-04-17T12:00:00Z] [--trace reference/flagship_trace.json]

The question: "Pump P-102's vibration changed. Have we seen this before, what was it, which other running assets
share the risk (same lot), and what fixed it last time?"

Each hop is one query against the graph (AGE), the recordings (MorletDB) or both in one statement; the trace
records its query, parameters, result and time. The trace is the ground truth for the tests and for a replay. The
answer it builds is a hypothesis with its evidence: similar vibration is evidence, not a diagnosis.
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fleetdb  # noqa: E402
import plant  # noqa: E402
import queries  # noqa: E402

QUESTION = ("Pump {unit}'s vibration changed. Have we seen this before, what was it, which other running assets "
            "share the risk (same lot), and what fixed it last time?")
BASELINE_SNAPSHOTS = 24       # the first day after the current bearings went in (one snapshot an hour)
CHANGED_RATIO = 2.0           # RMS of the latest snapshot at least this many times the baseline median


class Trace:
    def __init__(self, conn):
        self.conn, self.hops = conn, []

    def hop(self, name, store, query, params, fn):
        t0 = time.perf_counter()
        result = fn()
        ms = (time.perf_counter() - t0) * 1000
        self.hops.append({"hop": len(self.hops) + 1, "name": name, "store": store, "query": query.strip(),
                          "params": params, "result": json.loads(json.dumps(result, default=str)),
                          "elapsed_ms": round(ms, 1)})
        return result


RMS_SQL = f"""
WITH since AS (          -- the position's recordings since the current bearing went in
  SELECT r.recording_id, r.metadata->>'start' AS start_utc, (r.checkpoint->>'end')::bigint AS n,
         row_number() OVER (ORDER BY (r.metadata->>'start')::timestamptz) AS k,
         row_number() OVER (ORDER BY (r.metadata->>'start')::timestamptz DESC) AS back
  FROM {plant.COLLECTION} r
  WHERE r.metadata->>'equipment_id' = %(equipment_id)s
    AND (r.metadata->>'start')::timestamptz >= %(since)s::timestamptz
    AND (r.metadata->>'start')::timestamptz <= %(at)s::timestamptz
)
SELECT s.recording_id, s.start_utc, st.rms, s.k, s.back, (SELECT count(*) FROM since) AS total
FROM since s, LATERAL morlet_interval_stats('{plant.COLLECTION}', s.recording_id, 0, s.n) st
WHERE s.k <= %(baseline)s OR s.back = 1
ORDER BY s.k"""


def rms_change(conn, equipment_id, since, at):
    """RMS of the position's latest recording against the median of its first BASELINE_SNAPSHOTS recordings since
    the current bearing went in."""
    rows = conn.execute(RMS_SQL, {"equipment_id": equipment_id, "since": since, "at": at,
                                  "baseline": BASELINE_SNAPSHOTS}).fetchall()
    total = rows[0][5] if rows else 0
    if total <= BASELINE_SNAPSHOTS:
        return {"equipment_id": equipment_id, "recordings": total, "note": "too few recordings for a baseline"}
    base = statistics.median(r[2] for r in rows if r[3] <= BASELINE_SNAPSHOTS)
    last = next(r for r in rows if r[4] == 1)
    return {"equipment_id": equipment_id, "recordings": total, "baseline_rms": round(base, 5),
            "baseline": f"median of the first {BASELINE_SNAPSHOTS} recordings from {rows[0][1]}",
            "latest_recording": last[0], "latest_start": last[1], "latest_rms": round(last[2], 5),
            "ratio": round(last[2] / base, 2), "changed": last[2] / base >= CHANGED_RATIO}


def run(conn, unit="P-102", at=plant.NOW, top_k=50):
    """The investigation; returns the trace and the answer (a dict)."""
    fleetdb.set_search_path(conn)
    at = queries.utc(at)
    tr = Trace(conn)
    positions = [plant.equipment_id(unit, p) for p in plant.POSITIONS]

    # 1. Graph: which bearings are in the unit now, and since when (the baseline starts there).
    installed = {}
    for eq in positions:
        installed[eq] = tr.hop(f"installed bearing, {eq}", "graph", queries.INSTALLED_AT, {"id": eq, "at": at},
                               lambda eq=eq: queries.installed_at(conn, eq, at)["installed"])

    # 2. MorletDB: which position changed (RMS against its first day).
    change = {}
    for eq in positions:
        since = installed[eq]["installed_from"]
        change[eq] = tr.hop(f"vibration level, {eq}", "morletdb", RMS_SQL,
                            {"equipment_id": eq, "since": since, "at": at, "baseline": BASELINE_SNAPSHOTS},
                            lambda eq=eq, since=since: rms_change(conn, eq, since, at))
    ranked = sorted((c for c in change.values() if "ratio" in c), key=lambda c: -c["ratio"])
    target = ranked[0]["equipment_id"]
    query_asset = installed[target]["asset"]

    # 3. Graph: where the position sits in the plant.
    path = tr.hop(f"ISA-95 path, {target}", "graph", queries.PARENT, {"id": target},
                  lambda: [{"label": p["label"], "id": p["id"]} for p in queries.isa95_path(conn, target)["path"]])

    # 4. One statement over both: similar recordings from before this bearing went in, resolved to the bearing then
    #    installed in each hit's position and its recorded failure.
    params = queries.similar_failures_params(target, at, top_k)
    hits = tr.hop(f"similar recordings and the bearings behind them, {target}", "graph+morletdb (one SQL statement)",
                  queries.SIMILAR_FAILURES, params, lambda: queries.similar_failures(conn, target, at, top_k))

    by_asset = {}
    for rank, h in enumerate(hits, 1):
        a = by_asset.setdefault(h["asset"], {"asset": h["asset"], "equipment_id": h["equipment_id"], "lot": h["lot"],
                                             "failure": h["failure"], "mode": h["mode"], "mode_name": h["mode_name"],
                                             "first_rank": rank, "hits": 0, "best_distance": h["distance"],
                                             "first_hit": h["start_utc"],
                                             "last_hit": h["start_utc"]})
        a["hits"] += 1
        a["best_distance"] = min(a["best_distance"], h["distance"])
        a["first_hit"], a["last_hit"] = min(a["first_hit"], h["start_utc"]), max(a["last_hit"], h["start_utc"])
    precedents = sorted(by_asset.values(), key=lambda a: (a["best_distance"], a["asset"]))
    failed = [a for a in precedents if a["failure"]]
    precedent = failed[0] if failed else None

    # 5. Graph: the precedent's failure and what fixed it.
    history = repair = None
    if precedent:
        history = tr.hop(f"failure history, {precedent['asset']}", "graph", queries.FAILURES_OF_ASSET,
                         {"id": precedent["asset"]}, lambda: queries.failure_history(conn, precedent["asset"]))
        repair = history["failures"][0]["repair"] if history["failures"] else []

    # 6. Graph: who else carries a bearing from the same lot.
    lot = tr.hop(f"same lot as {query_asset}", "graph", queries.LOT_INSTALLED, {"id": query_asset},
                 lambda: queries.same_lot(conn, query_asset))

    # 7. MorletDB evidence for those siblings: monitored positions have a level; unmonitored ones have none.
    siblings = []
    for s in lot["installed_elsewhere"]:
        entry = {**s}
        if s["monitored"] and s["equipment_id"] in change:
            c = change[s["equipment_id"]]
            entry["evidence"] = f"monitored: RMS {c['ratio']}x its first day"
            entry["rms_ratio"] = c["ratio"]
        else:
            entry["evidence"] = "not monitored: no recordings, so no vibration evidence either way"
        siblings.append(entry)

    # 8. Graph: spares, and whether they come from the same lot.
    stock = tr.hop("spare bearings", "graph", queries.SPARES, {}, lambda: queries.spare_stock(conn))

    answer = {
        "question": QUESTION.format(unit=unit),
        "as_of": at,
        "changed": {"equipment_id": target, "asset": query_asset, "lot": installed[target]["lot"],
                    "installed_from": installed[target]["installed_from"],
                    "isa95_path": [x["id"] for x in path], **{k: change[target][k] for k in
                    ("baseline_rms", "latest_rms", "ratio", "latest_recording")}},
        "seen_before": bool(precedent),
        "precedents": precedents,
        "what_it_was": ({"failure": precedent["failure"], "mode": precedent["mode"],
                         "mode_name": precedent["mode_name"], "asset": precedent["asset"],
                         "equipment_id": precedent["equipment_id"], "best_distance": precedent["best_distance"],
                         "hits": precedent["hits"], "of_hits": len(hits)} if precedent else None),
        "same_lot": {"lot": lot["lot"]["id"], "supplier": lot["supplier"], "installed": siblings,
                     "in_stock": lot["in_stock"]},
        "fixed_last_time": repair,
        "spares": [{"store": s["store"], "on_hand": s["on_hand"], "reorder_point": s["reorder_point"],
                    "from_same_lot": sum(1 for x in s["serials"] if x["lot"] == lot["lot"]["id"])} for s in stock],
        "caveats": [
            "Similarity is evidence for a hypothesis, not a diagnosis: the closest earlier recordings came from a "
            "bearing that failed with this mode; confirming the mode needs inspection (or an envelope spectrum at the "
            "defect frequencies).",
            "Searched: recordings made before the current bearing was installed, on the stored 4096-sample window "
            "grid; not found there is not 'never happened'.",
            "Same lot is a shared risk factor, not evidence of a defect; unmonitored siblings have no vibration "
            "evidence either way.",
            "The plant, its organisation, lots and work orders are fictional; the recordings and their failures are "
            "the IMS bearing data's."],
    }
    return {"trace": tr.hops, "answer": answer}


def summary(result):
    a = result["answer"]
    lines = [a["question"], f"as of {a['as_of']}", ""]
    c = a["changed"]
    lines.append(f"Where: {' < '.join(c['isa95_path'])}")
    lines.append(f"Changed: {c['equipment_id']} (bearing {c['asset']}, {c['lot']}, installed {c['installed_from']}): "
                 f"RMS {c['latest_rms']} against {c['baseline_rms']} on its first day ({c['ratio']}x).")
    w = a["what_it_was"]
    if w:
        lines.append(f"Seen before: {w['hits']} of the {w['of_hits']} closest earlier windows come from bearing "
                     f"{w['asset']} in {w['equipment_id']} (best distance {w['best_distance']:.3f}), which failed: "
                     f"{w['failure']}, {w['mode_name']}.")
    else:
        lines.append("Seen before: no close earlier recording of a bearing with a recorded failure (in the searched "
                     "windows).")
    for p in a["precedents"]:
        lines.append(f"  {p['asset']} {p['equipment_id']}: first at rank {p['first_rank']}, {p['hits']} hits, "
                     f"best {p['best_distance']:.3f}, "
                     f"{p['first_hit']}..{p['last_hit']}, failure: {p['failure'] or 'none recorded'}")
    s = a["same_lot"]
    lines.append(f"Same lot ({s['lot']}, {s['supplier']}): " + "; ".join(
        f"{x['unit']} {x['equipment_id']} ({x['evidence']})" for x in s["installed"]))
    lines.append(f"  in stock from this lot: {', '.join(x['asset'] + ' at ' + x['store'] for x in s['in_stock'])}")
    for r in a["fixed_last_time"] or []:
        lines.append(f"Fixed last time: {r['work_order']['id']} ({r['work_order']['kind']}): {r['work_order']['task']}; "
                     f"{r['response']['findings']}; {r['response']['outcome']}.")
    for sp in a["spares"]:
        lines.append(f"Spares at {sp['store']}: {sp['on_hand']} on hand (reorder at {sp['reorder_point']}), "
                     f"{sp['from_same_lot']} from {s['lot']}.")
    lines += ["", *("Note: " + x for x in a["caveats"])]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--unit", default="P-102")
    p.add_argument("--at", default=plant.NOW)
    p.add_argument("--top-k", type=int, default=50)
    p.add_argument("--trace", type=Path, help="write the trace and answer as JSON here")
    opts = p.parse_args(argv)
    with psycopg.connect(opts.dsn, options="-c default_transaction_read_only=on") as conn:
        result = run(conn, opts.unit, opts.at, opts.top_k)
    print(summary(result))
    print(f"\n{len(result['trace'])} hops, {sum(h['elapsed_ms'] for h in result['trace']):.0f} ms in queries")
    if opts.trace:
        opts.trace.parent.mkdir(parents=True, exist_ok=True)
        opts.trace.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
