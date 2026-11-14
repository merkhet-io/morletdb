#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Record a confirmed finding in the graph and in MorletDB, in one transaction. A person confirms; no agent can.

  DATABASE_URL=postgresql://fleet_engineer@host/morlet python examples/fleet-investigator/confirm_finding.py \
      --trace reference/flagship_trace.json --confirmed-by PC-RELENG

Reads the answer of an investigation (investigate.py --trace), shows the finding it proposes, and writes it only
after the person types the finding's id. In one transaction:
  - MorletDB: a label `finding:resembles` on the query interval (morlet_add_label), whose note holds the evidence,
    and an exempt episode for the event library (morlet_add_episodes), so compaction keeps those samples;
  - graph: an edge (:PhysicalAsset)-[:RESEMBLES {finding_id, evidence, label_id, episode_id, ...}]->(:Failure).
Either all of it commits or none of it does. Repeating an identical finding is a no-op; a different finding under
the same id is refused. This is not an MCP tool: the MCP servers stay read-only.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fleetdb  # noqa: E402
import plant  # noqa: E402
import queries  # noqa: E402

LABEL = "finding:resembles"
EXISTING = "MATCH ()-[r:RESEMBLES {finding_id: $id}]->() RETURN properties(r)"
CREATE = ("UNWIND [$props] AS props MATCH (a:PhysicalAsset {id: $asset}), (f:Failure {id: $failure}) "
          "CREATE (a)-[r:RESEMBLES]->(f) SET r = props RETURN id(r)")
ASSET_SITE = "MATCH (a:PhysicalAsset {id: $asset}) RETURN a.site"


class Conflict(Exception):
    pass


def finding_from(answer, evidence_hits=5):
    """The finding an investigation's answer proposes: the changed bearing resembles the precedent's failure."""
    w, c = answer["what_it_was"], answer["changed"]
    if not w:
        raise ValueError("the investigation found no precedent with a recorded failure; nothing to confirm")
    hits = [p for p in answer["precedents"] if p["asset"] == w["asset"]]
    return {
        "finding_id": f"FND-{c['asset']}-{w['failure']}",
        "asset": c["asset"], "equipment_id": c["equipment_id"], "failure": w["failure"], "mode": w["mode"],
        "query": {"collection": plant.COLLECTION, "recording_id": c["latest_recording"], "start_sample": 0,
                  "end_sample": queries.WINDOW},
        "evidence": {"precedent_asset": w["asset"], "precedent_equipment_id": w["equipment_id"],
                     "best_distance": round(w["best_distance"], 6), "hits": w["hits"], "of_hits": w["of_hits"],
                     "first_hit": hits[0]["first_hit"] if hits else None,
                     "last_hit": hits[0]["last_hit"] if hits else None,
                     "rms_ratio": c["ratio"], "as_of": answer["as_of"],
                     "note": "similar vibration is evidence, not a diagnosis"},
    }


def _same(stored, finding):
    keys = ("finding_id", "asset", "failure", "mode", "query", "evidence")
    return all(stored.get(k) == finding[k] for k in keys)


def write_finding(conn, finding, confirmed_by, fail_before_commit=False):
    """Write the finding atomically. Returns 'written' or 'exists' (identical retry); raises Conflict otherwise.
    fail_before_commit (tests) raises after every write, before the commit, to show the rollback."""
    fleetdb.set_search_path(conn)
    q = finding["query"]
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (finding["finding_id"],))
        old = fleetdb.cypher(conn, EXISTING, {"id": finding["finding_id"]})
        if old:
            if _same(old[0]["r"], finding):
                return "exists"
            raise Conflict(f"{finding['finding_id']} exists with other content; not changed")
        site = fleetdb.cypher(conn, ASSET_SITE, {"asset": finding["asset"]})
        if not site:
            raise ValueError(f"bearing {finding['asset']} does not exist or is not visible to this role")
        note = json.dumps({"finding_id": finding["finding_id"], "asset": finding["asset"],
                           "failure": finding["failure"], "mode": finding["mode"], **finding["evidence"]},
                          sort_keys=True)
        label_id = conn.execute("SELECT morlet_add_label(%s::regclass, %s, %s, %s, %s, %s)",
                                (q["collection"], q["recording_id"], q["start_sample"], q["end_sample"], LABEL,
                                 note)).fetchone()[0]
        episode_id = conn.execute(
            "SELECT morlet_add_episodes(%s::regclass, %s::jsonb)",
            (q["collection"], json.dumps([{"recording_id": q["recording_id"], "start_sample": q["start_sample"],
                                           "end_sample": q["end_sample"], "importance": 1.0, "exempt": True,
                                           "context": {"finding_id": finding["finding_id"], "label": LABEL,
                                                       "asset": finding["asset"], "failure": finding["failure"]}}])
             )).fetchone()[0]
        props = {**finding, "site": site[0]["r"], "label_id": label_id, "episode_id": episode_id,
                 "confirmed_by": confirmed_by,
                 "db_role": conn.execute("SELECT current_user").fetchone()[0],
                 "confirmed_at": conn.execute("SELECT to_char(now() AT TIME ZONE 'UTC', "
                                              "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')").fetchone()[0]}
        created = fleetdb.cypher(conn, CREATE, {"props": props, "asset": finding["asset"],
                                                "failure": finding["failure"]})
        if len(created) != 1:
            raise ValueError("the bearing or the failure is not visible to this role; nothing written")
        if fail_before_commit:
            raise RuntimeError("fail_before_commit: rolling back")
    return "written"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trace", type=Path, required=True, help="JSON written by investigate.py --trace")
    p.add_argument("--confirmed-by", required=True,
                   help="who confirms, as a personnel class or staff id (e.g. PC-RELENG), not a name")
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    opts = p.parse_args(argv)
    finding = finding_from(json.loads(opts.trace.read_text())["answer"])
    print(json.dumps(finding, indent=1))
    print(f"\nThis records that bearing {finding['asset']} ({finding['equipment_id']}) resembles failure "
          f"{finding['failure']} ({finding['mode']}), in the graph and in MorletDB.")
    print(f"Type the finding id ({finding['finding_id']}) to confirm, anything else to cancel: ", end="", flush=True)
    typed = sys.stdin.readline().strip()
    if typed != finding["finding_id"]:
        print("not confirmed; nothing written")
        return 2
    try:
        with psycopg.connect(opts.dsn) as conn:
            status = write_finding(conn, finding, opts.confirmed_by)
    except Conflict as exc:
        print(f"refused: {exc}")
        return 1
    print(f"{status}: {finding['finding_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
