# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""Replay the synthetic machine into MorletDB and the companion, then start one investigation.

    python enterprise/winccoa/replay/replay.py --dsn "service=morlet_writer" --companion 127.0.0.1:8770 \
        --token-file ~/.config/morlet-winccoa/token [--collection plant.rig] [--schema morlet_winccoa]

1. History: four acquisitions with repeated impacts and rubs in different operating contexts; their context and
   synthetic labels are written directly (a historical import, not through WinCC OA).
2. Current acquisition: samples in bounded batches (each transaction also builds the search windows it completes);
   between batches, the simulated adapter sends the operating-context changes up to that time, with a heartbeat,
   to the companion; at the end the writer marks the acquisition finished.
3. An investigation from the current impact window; the ranked matches are printed with their labels.

Similarity is not diagnosis: the labels of matches are annotations of earlier intervals.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "companion" / "src"))

import psycopg  # noqa: E402

from adapter_sim import Adapter, Client  # noqa: E402
from machine import Plan, current, history  # noqa: E402
from writer import recording_id, setup_collection, write_acquisition, write_history_context  # noqa: E402

DAY_NS = 86_400 * 1_000_000_000
HISTORY_ORIGIN_NS = 1_790_812_800_000_000_000     # 2026-10-01T00:00:00Z
ASSET = "rig-1"
SPEED_DPE = "System1:Rig1.speed"
LOAD_DPE = "System1:Rig1.load"
GOOD = {"good": True, "invalid": False}


def write_history(conn: psycopg.Connection, collection: str, metadata: dict | None = None,
                  batch_samples: int = 65_536) -> list[Plan]:
    plans = history()
    for i, p in enumerate(plans):
        write_acquisition(conn, collection, p, HISTORY_ORIGIN_NS + i * DAY_NS, batch_samples=batch_samples,
                          metadata=metadata)
        write_history_context(conn, collection, p)
    return plans


def context_changes(plan: Plan) -> list[tuple[int, str, str, str]]:
    """(acquisition time ns, dpe, value_text, value_type) of every change, in time order."""
    out = []
    prev = None
    for c in plan.contexts:
        if prev is None or prev.speed != c.speed:
            out.append((c.t_start_ns, SPEED_DPE, json.dumps(c.speed), "int"))
        if prev is None or prev.load != c.load:
            out.append((c.t_start_ns, LOAD_DPE, json.dumps(c.load), "string"))
        prev = c
    return out


def replay_current(conn: psycopg.Connection, collection: str, adapter: Adapter, origin_ns: int, *,
                   schema: str = "morlet_winccoa", batch_samples: int = 65_536, metadata: dict | None = None,
                   finish: bool = True, plan: Plan | None = None) -> Plan:
    """Write the current acquisition in bounded batches and send its context through the adapter as time passes."""
    from morlet_winccoa.config import Config
    from morlet_winccoa.observations import mark_finished
    plan = plan or current()
    changes = context_changes(plan)
    sent = 0

    def on_batch(i: int, ends: dict[str, int]) -> None:
        nonlocal sent
        t_now = ends["vib"] * 1_000_000_000 // 8192            # acquisition time covered by stored vib samples
        adapter.heartbeat(plan.acquisition_id, ASSET)
        while sent < len(changes) and changes[sent][0] <= t_now:
            t, dpe, text, vtype = changes[sent]
            # Recorded changes with their source times. (A live adapter tags the initial answer of a connect as a
            # snapshot, which opens at max(source time, reconnect time); a replay faster than real time sends the
            # recorded first values as changes instead.)
            adapter.change(plan.acquisition_id, dpe, text, vtype, origin_ns + t, quality=GOOD)
            sent += 1
        for r in adapter.flush():
            if r.status != 200:
                raise RuntimeError(f"companion refused observations: {r.status} {r.body}")

    write_acquisition(conn, collection, plan, origin_ns, batch_samples=batch_samples, metadata=metadata,
                      on_batch=on_batch)
    if finish:
        cfg = Config(dsn="", token_file="", schema=schema)
        with conn.transaction():
            mark_finished(conn, cfg, collection, plan.acquisition_id, plan.end_ns)
        conn.commit()
    return plan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn", default=os.environ.get("MORLET_WRITER_DSN", ""))
    p.add_argument("--companion", default="127.0.0.1:8770")
    p.add_argument("--token-file", required=True)
    p.add_argument("--collection", default="plant.rig")
    p.add_argument("--schema", default="morlet_winccoa")
    a = p.parse_args()
    host, _, port = a.companion.rpartition(":")
    token = Path(a.token_file).read_text().split()[0]
    adapter = Adapter(Client(host, int(port), token))
    with psycopg.connect(a.dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{a.collection.split(".")[0]}"')
        conn.commit()
        setup_collection(conn, a.collection)
        write_history(conn, a.collection)
        origin_ns = (time.time_ns() // 1000) * 1000 - current().end_ns
        plan = replay_current(conn, a.collection, adapter, origin_ns, schema=a.schema)
    impact = next(e for e in plan.events if e.label == "impact")
    body = adapter.start_investigation("panel-1", a.collection, recording_id(plan, "vib"), impact.vib_start,
                                       impact.vib_start + 4096)
    time.sleep(1)
    r = adapter.send_investigation(body)
    print(f"investigation {body['request_id']}: HTTP {r.status}, status {r.body.get('status')}, "
          f"mode {r.body.get('search_mode')}")
    for m in r.body.get("matches", []):
        labels = ",".join(x["label"] for x in m.get("labels", [])) or "-"
        print(f"  {m['rank']:2d} {m['recording_id']:<16} [{m['start_sample']}, {m['end_sample']}) "
              f"d={m['distance']:.4f} labels={labels}")
    return 0 if r.status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
