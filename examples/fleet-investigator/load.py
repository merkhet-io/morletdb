#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Load the fleet investigator: IMS bearing recordings into MorletDB and the plant's context graph into AGE.

  MORLET_DATA=/path/to/ims DATABASE_URL=postgresql://owner@host/db python examples/fleet-investigator/load.py

MORLET_DATA is the folder holding the extracted IMS runs (1st_test, 2nd_test, 4th_test/txt), directly or under
runs/. Nothing is downloaded; README.md says where the archive comes from and how to extract it.

1. Selects every 6th snapshot file of IMS tests 1, 2 and 3 (test 3 only before the plant's "now", plant.NOW) and
   checks each file's SHA-256 against manifest.json. A mismatch stops the load.
2. Creates the collection fleet.vibration with envelopes, context and episodic memory, and the access rule
   (security.sql) before any data, for recordings and graph alike.
3. Imports each snapshot as one recording per bearing position (1.024 s at 20 kHz, values as distributed on their
   0.001 grid, stored exactly) with the ISA-95 equipment ID in its metadata, and builds the search envelopes.
4. Mirrors the operating context (ISA-95 job order and operations segment) into MorletDB context.
5. Writes the plant graph (plant.py) in one transaction, secures every label table, and creates the roles.

Running it again skips what is done and changes nothing. --write-manifest (maintainers) recomputes manifest.json
from the files instead of checking them.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/ingest"))
import fleetdb  # noqa: E402
import morlet_import as mi  # noqa: E402
import plant  # noqa: E402
from parquet_import import Config, import_files  # noqa: E402

MANIFEST = HERE / "manifest.json"
MODEL_VERSION = "fleet-model-v1"
SAMPLES = 20480
WINDOW = 4096
LABEL = "finding:resembles"
LABEL_DEFINITION = ("A person confirmed that this interval resembles an earlier recorded failure; the note holds the "
                    "finding: similar recordings, distances, the failure and its mode. Similarity is evidence for the "
                    "finding, not a diagnosis.")


def start_of(name):
    """IMS file names are the snapshot's start on the rig clock (time zone not stated), e.g. 2004.02.12.10.32.39."""
    return datetime.strptime(name, "%Y.%m.%d.%H.%M.%S").replace(tzinfo=timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_root(data):
    for base in (data, data / "runs"):
        if all((base / spec["folder"]).is_dir() for spec in plant.IMS_RUNS.values()):
            return base
    raise SystemExit(f"{data}: expected the extracted IMS runs 1st_test, 2nd_test and 4th_test/txt (directly or "
                     f"under runs/); see README.md")


def selection(root):
    """The snapshot files this demo loads, per IMS test: every 6th file, test 3 only before plant.NOW."""
    now = datetime.strptime(plant.NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    out = {}
    for test, spec in plant.IMS_RUNS.items():
        names = sorted(p.name for p in (root / spec["folder"]).iterdir() if not p.name.startswith("."))
        if len(names) != spec["files"]:
            raise SystemExit(f"IMS test {test}: expected {spec['files']} files in {spec['folder']}, found {len(names)}")
        if test == "3":
            names = [n for n in names if start_of(n) < now]
        out[test] = names[::plant.SNAPSHOT_EVERY]
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_manifest(root, chosen, write=False):
    files = {f"{plant.IMS_RUNS[t]['folder']}/{n}": sha256(root / plant.IMS_RUNS[t]["folder"] / n)
             for t, names in chosen.items() for n in names}
    if write:
        MANIFEST.write_text(json.dumps({
            "source": "IMS bearing data (NASA Prognostics Data Repository), extracted from 4. Bearings.zip",
            "archive_sha256": "21001ac266c465f5d345ec42d7b508c6a6328487fd9d4d7774422dd5ea10ad83",
            "selection": f"every {plant.SNAPSHOT_EVERY}th file in name order; test 3 only before {plant.NOW}",
            "files": files}, indent=1, sort_keys=True) + "\n")
        print(f"wrote {MANIFEST.name}: {len(files)} files", flush=True)
        return
    want = json.loads(MANIFEST.read_text())["files"]
    bad = sorted(set(want) ^ set(files)) + sorted(k for k in files if k in want and want[k] != files[k])
    if bad:
        raise SystemExit(f"{len(bad)} files differ from manifest.json (first: {bad[0]}); refusing to load")
    print(f"{len(files)} snapshot files match manifest.json", flush=True)


def recording_id(unit, position, t0):
    return f"{plant.equipment_id(unit, position)}.{t0.strftime('%Y%m%dT%H%M%S')}"


def read_snapshot(path, test):
    """One IMS snapshot file as a float64 array (20,480 rows), checking the column count of its test."""
    values = np.loadtxt(path, dtype=np.float64)
    spec = plant.IMS_RUNS[test]
    if values.shape != (SAMPLES, spec["columns"]):
        raise ValueError(f"{path.name}: expected {SAMPLES} x {spec['columns']} values, got {values.shape}")
    return values


def metadata_for(test, position_index, path):
    spec = plant.IMS_RUNS[test]
    unit, position = spec["unit"], plant.POSITIONS[position_index]
    t0 = start_of(path.name)
    return recording_id(unit, position, t0), {
        "site": plant.site_of(unit), "unit": unit, "position": position,
        "equipment_id": plant.equipment_id(unit, position), "start": iso(t0),
        "ims_test": test, "ims_bearing": str(position_index + 1), "ims_column": spec["use"][position_index] + 1,
        "source": f"{spec['folder']}/{path.name}",
        "provenance": "IMS bearing data, University of Cincinnati / NASA Prognostics Data Repository; "
                      "values as distributed (unit not stated); rig clock, time zone not stated"}


def make_reader(test_of):
    def read_ims(path, _opts):
        test = test_of[path.name]
        values = read_snapshot(path, test)
        out = []
        for i, col in enumerate(plant.IMS_RUNS[test]["use"]):
            rid, meta = metadata_for(test, i, path)
            out.append(mi.Recording(rid, plant.RATE, "housing-accel", "as distributed (unit not stated)", 0.001,
                                    values[:, col], meta))
        return out
    return read_ims


def setup(conn):
    """Extensions, the access rule and the empty collection, before any data (policies first)."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    conn.execute((HERE / "security.sql").read_text())
    for group in (fleetdb.ALL_SITES, fleetdb.SITE_A):
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (group,)).fetchone():
            conn.execute(f"CREATE ROLE {group} NOLOGIN")
    for role, password, group in ((fleetdb.READER, fleetdb.READER_PASSWORD, fleetdb.ALL_SITES),
                                  (fleetdb.CONTRACTOR, fleetdb.CONTRACTOR_PASSWORD, fleetdb.SITE_A),
                                  (fleetdb.ENGINEER, fleetdb.ENGINEER_PASSWORD, fleetdb.ALL_SITES)):
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        conn.execute(f"GRANT {group} TO {role}")
        conn.execute(f'ALTER ROLE {role} SET search_path = ag_catalog, "$user", public')
    conn.execute("INSERT INTO fleet.site_access VALUES (%s, 'A'), (%s, 'B'), (%s, '*'), (%s, 'A'), (%s, '*') "
                 "ON CONFLICT DO NOTHING", (fleetdb.ALL_SITES,) * 3 + (fleetdb.SITE_A,) * 2)
    schema, name = plant.COLLECTION.split(".")
    if conn.execute("SELECT to_regclass(%s)", (plant.COLLECTION,)).fetchone()[0] is None:
        conn.execute("SELECT morlet_create_stream_collection(%s, %s)", (schema, name))
    conn.execute("SELECT fleet.secure_collection(%s::regclass)", (plant.COLLECTION,))
    for suffix, enable in (("_envelopes", "morlet_enable_envelopes"), ("_ctx", "morlet_enable_context"),
                           ("_episodes", "morlet_enable_memory")):
        if conn.execute("SELECT to_regclass(%s)", (plant.COLLECTION + suffix,)).fetchone()[0] is None:
            conn.execute(f"SELECT {enable}(%s::regclass)", (plant.COLLECTION,))
    conn.execute("SELECT morlet_define_label(%s::regclass, %s, %s, '', 'examples/fleet-investigator')",
                 (plant.COLLECTION, LABEL, LABEL_DEFINITION))
    conn.commit()


def complete_recordings(conn):
    """Recordings with all their samples and all envelope windows; anything else is imported (again)."""
    return {r for (r,) in conn.execute(
        f"SELECT r.recording_id FROM {plant.COLLECTION} r WHERE (r.checkpoint->>'end')::bigint = %s "
        f"AND (SELECT count(*) FROM {plant.COLLECTION}_envelopes e WHERE e.recording_id = r.recording_id "
        f"AND e.window_size = %s) = %s", (SAMPLES, WINDOW, (SAMPLES - WINDOW) // (WINDOW // 2) + 1))}


def import_recordings(dsn, root, chosen):
    test_of, want = {}, {}
    for test, names in chosen.items():
        for n in names:
            p = root / plant.IMS_RUNS[test]["folder"] / n
            test_of[n] = test
            want[p] = [metadata_for(test, i, p)[0] for i in range(4)]
    with psycopg.connect(dsn) as conn:
        done = complete_recordings(conn)
    todo = [p for p, rids in want.items() if not all(r in done for r in rids)]
    if not todo:
        print(f"{plant.COLLECTION}: all {4 * len(want)} recordings are loaded", flush=True)
        return
    print(f"importing {len(todo)} snapshot files x 4 bearing positions (of {len(want)}) and building envelopes",
          flush=True)
    mi.READERS["fleet-ims"] = make_reader(test_of)
    opts = SimpleNamespace(format="fleet-ims", prefix="", extractor=None)
    t0 = time.monotonic()
    for i in range(0, len(todo), 200):
        batch = todo[i:i + 200]
        report = import_files(batch, plant.COLLECTION, Config(), dsn=dsn, reader=mi.chunk_reader(opts))
        if report.get("status") != "complete":
            raise SystemExit(f"import failed: {json.dumps(report, default=str)[:2000]}")
        ids = [r for p in batch for r in want[p]]
        with psycopg.connect(dsn) as conn:
            mi.build(conn, plant.COLLECTION, ids, WINDOW, False)
        print(f"  {min(i + 200, len(todo))}/{len(todo)} files, {time.monotonic() - t0:.0f} s", flush=True)


def mirror_context(conn, chosen):
    """ISA-95 operating context per recording (job order, operations segment, speed, load) as MorletDB context,
    one idempotent batch per IMS test and block of 1,000 recordings."""
    n = 0
    for test, names in chosen.items():
        job = plant.job_order_for(test)
        rows = []
        for name in names:
            for i in range(4):
                rid, _ = metadata_for(test, i, Path(name))
                for key, value in (("job_order", job["id"]), ("operations_segment", job["segment"]),
                                   ("shaft_rpm", job["shaft_rpm"]), ("radial_load_lbs", job["radial_load_lbs"])):
                    rows.append({"recording_id": rid, "key": key, "value": value, "start_sample": 0,
                                 "end_sample": SAMPLES})
        for k in range(0, len(rows), 4000):
            n += conn.execute("SELECT morlet_add_context(%s::regclass, %s, %s::jsonb)",
                              (plant.COLLECTION, f"isa95-{job['id']}-{k // 4000}", json.dumps(rows[k:k + 4000]))
                              ).fetchone()[0]
    conn.commit()
    print(f"operating context: {n} rows added" if n else "operating context: already mirrored", flush=True)


VERTEX_LABELS = ("Meta", "Enterprise", "Site", "Area", "ProcessCell", "Unit", "EquipmentModule", "EquipmentClass",
                 "MaterialClass", "MaterialDefinition", "MaterialLot", "Supplier", "PhysicalAsset", "StorageLocation",
                 "SparePart", "MaintenanceRequest", "WorkOrder", "MaintenanceResponse", "Failure", "FailureMode",
                 "PersonnelClass", "OperationsSegment", "JobOrder")
EDGE_LABELS = ("CONTAINS", "OF_CLASS", "OF_DEFINITION", "SUPPLIED_BY", "FROM_LOT", "INSTALLED_IN", "STORED_AT",
               "HELD_AT", "FOR_EQUIPMENT", "FULFILS", "ON_UNIT", "REQUIRES", "CONSUMED", "RESPONDS_TO", "FOUND",
               "OF_ASSET", "CLASSIFIED_AS", "SUBCLASS_OF", "EXECUTED_ON", "FOR_SEGMENT", "RESEMBLES")


def graph_rows(chosen):
    """Vertices per label and edges per type, from plant.py and the recordings actually selected."""
    v = {"Meta": [{"id": "model", "site": plant.SHARED, "model_version": MODEL_VERSION, "now": plant.NOW,
                   "collection": plant.COLLECTION}],
         "Enterprise": [plant.ENTERPRISE]}
    e = {k: [] for k in EDGE_LABELS}

    def strip(d, *keys):
        return {k: x for k, x in d.items() if k not in keys}

    def edge(kind, a, b, site, **props):
        e[kind].append({"a": a, "b": b, "props": {"site": site, **props}})

    v["Site"] = plant.SITES
    v["Area"] = [strip(a, "parent") for a in plant.AREAS]
    v["ProcessCell"] = [strip(c, "parent") for c in plant.PROCESS_CELLS]
    v["Unit"] = [strip(u, "parent", "class") for u in plant.UNITS]
    v["EquipmentClass"] = plant.EQUIPMENT_CLASSES
    for s in plant.SITES:
        edge("CONTAINS", plant.ENTERPRISE["id"], s["id"], s["site"])
    for x in plant.AREAS + plant.PROCESS_CELLS + plant.UNITS:
        edge("CONTAINS", x["parent"], x["id"], x["site"])
    for u in plant.UNITS:
        edge("OF_CLASS", u["id"], u["class"], u["site"])
    # Bearing positions link to their recordings by collection and the equipment ID in recording metadata; the
    # first and last recording of each monitored position are named as recording_id properties.
    span = {}
    for test, names in chosen.items():
        unit = plant.IMS_RUNS[test]["unit"]
        for i, p in enumerate(plant.POSITIONS):
            rids = [recording_id(unit, p, start_of(n)) for n in names]
            span.setdefault(plant.equipment_id(unit, p), []).extend(rids)
            asset = plant.asset_of(test, str(i + 1))
            span[asset] = rids
    mods = []
    for m in plant.EQUIPMENT_MODULES:
        row = strip(m, "parent", "class")
        if m["id"] in span:
            row["first_recording_id"], row["last_recording_id"] = min(span[m["id"]]), max(span[m["id"]])
        mods.append(row)
        edge("CONTAINS", m["parent"], m["id"], m["site"])
        edge("OF_CLASS", m["id"], m["class"], m["site"])
    v["EquipmentModule"] = mods
    v["MaterialClass"] = [plant.MATERIAL_CLASS]
    v["MaterialDefinition"] = [plant.MATERIAL_DEFINITION]
    edge("OF_CLASS", plant.MATERIAL_DEFINITION["id"], plant.MATERIAL_CLASS["id"], plant.SHARED)
    v["Supplier"] = plant.SUPPLIERS
    v["MaterialLot"] = [strip(lot, "supplier") for lot in plant.LOTS]
    for lot in plant.LOTS:
        edge("OF_DEFINITION", lot["id"], plant.MATERIAL_DEFINITION["id"], plant.SHARED)
        edge("SUPPLIED_BY", lot["id"], lot["supplier"], plant.SHARED)
    assets = []
    for a in plant.PHYSICAL_ASSETS:
        row = dict(a)
        if a["id"] in span:
            row["collection"] = plant.COLLECTION
            row["first_recording_id"], row["last_recording_id"] = min(span[a["id"]]), max(span[a["id"]])
        assets.append(row)
        edge("FROM_LOT", a["id"], a["lot"], a["site"])
        edge("OF_DEFINITION", a["id"], plant.MATERIAL_DEFINITION["id"], a["site"])
    v["PhysicalAsset"] = assets
    for i in plant.INSTALLS:
        edge("INSTALLED_IN", i["asset"], i["module"], i["site"], **{k: i[k] for k in ("from", "to") if k in i})
    v["StorageLocation"] = plant.STORES
    for s in plant.STORED:
        edge("STORED_AT", s["asset"], s["store"], s["site"], **{"from": s["from"]})
    v["SparePart"] = [strip(s, "store") | {"material_definition": plant.MATERIAL_DEFINITION["id"]}
                      for s in plant.SPARE_PARTS]
    for s in plant.SPARE_PARTS:
        edge("HELD_AT", s["id"], s["store"], s["site"])
        edge("OF_DEFINITION", s["id"], plant.MATERIAL_DEFINITION["id"], s["site"])
    v["FailureMode"] = [strip(f, "parent") for f in plant.FAILURE_MODES]
    for f in plant.FAILURE_MODES:
        if f["parent"]:
            edge("SUBCLASS_OF", f["id"], f["parent"], plant.SHARED)
    fails = []
    for f in plant.FAILURES:
        unit = plant.IMS_RUNS[f["evidence_test"]]["unit"]
        pos = plant.POSITIONS[int(f["evidence_bearing"]) - 1]
        last = recording_id(unit, pos, start_of(chosen[f["evidence_test"]][-1]))
        fails.append({**strip(f, "asset", "mode", "evidence_test", "evidence_bearing"),
                      "collection": plant.COLLECTION, "evidence_recording_id": last})
        edge("OF_ASSET", f["id"], f["asset"], f["site"])
        edge("CLASSIFIED_AS", f["id"], f["mode"], f["site"])
    v["Failure"] = fails
    v["PersonnelClass"] = plant.PERSONNEL_CLASSES
    v["MaintenanceRequest"], v["WorkOrder"], v["MaintenanceResponse"] = [], [], []
    for m in plant.MAINTENANCE:
        rq, wo, rs = m["request"], m["order"], m["response"]
        v["MaintenanceRequest"].append(strip(rq, "module"))
        v["WorkOrder"].append(strip(wo, "unit", "personnel", "consumed_lot", "consumed"))
        v["MaintenanceResponse"].append(strip(rs, "failures"))
        edge("FOR_EQUIPMENT", rq["id"], rq["module"], rq["site"])
        edge("FULFILS", wo["id"], rq["id"], wo["site"])
        edge("ON_UNIT", wo["id"], wo["unit"], wo["site"])
        edge("REQUIRES", wo["id"], wo["personnel"], wo["site"])
        edge("CONSUMED", wo["id"], wo["consumed_lot"], wo["site"], quantity=wo["consumed"])
        edge("RESPONDS_TO", rs["id"], wo["id"], rs["site"])
        for f in rs["failures"]:
            edge("FOUND", rs["id"], f, rs["site"])
    v["OperationsSegment"] = plant.OPERATIONS_SEGMENTS
    v["JobOrder"] = [strip(j, "unit", "segment") for j in plant.JOB_ORDERS]
    for j in plant.JOB_ORDERS:
        edge("EXECUTED_ON", j["id"], j["unit"], j["site"])
        edge("FOR_SEGMENT", j["id"], j["segment"], j["site"])
    return v, e


def write_graph(conn, chosen):
    """The whole graph in one transaction; skipped when the graph holds this model version."""
    fleetdb.set_search_path(conn)
    if conn.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s", (fleetdb.GRAPH,)).fetchone():
        meta = fleetdb.cypher(conn, "MATCH (m:Meta {id: 'model'}) RETURN m.model_version")
        if [r["r"] for r in meta] != [MODEL_VERSION]:
            raise SystemExit(f"graph {fleetdb.GRAPH} exists with another model ({meta}); start from an empty "
                             f"database (docker compose down -v)")
        print(f"graph {fleetdb.GRAPH}: already written ({MODEL_VERSION})", flush=True)
        conn.rollback()
        return
    vertices, edges = graph_rows(chosen)
    conn.execute("SELECT ag_catalog.create_graph(%s)", (fleetdb.GRAPH,))
    for label in VERTEX_LABELS:
        conn.execute("SELECT ag_catalog.create_vlabel(%s, %s)", (fleetdb.GRAPH, label))
    for label in EDGE_LABELS:
        conn.execute("SELECT ag_catalog.create_elabel(%s, %s)", (fleetdb.GRAPH, label))
    ends = {}   # id -> label, so each edge MATCHes its endpoints by label and id
    for label in VERTEX_LABELS:
        rows = vertices.get(label, [])
        for r in rows:
            if r["id"] in ends:
                raise ValueError(f"duplicate vertex id {r['id']}")
            ends[r["id"]] = label
        if rows:
            fleetdb.cypher(conn, f"UNWIND $rows AS r CREATE (n:{label}) SET n = r", {"rows": rows})
    n_edges = 0
    for kind, rows in edges.items():
        groups = {}
        for r in rows:
            groups.setdefault((ends[r["a"]], ends[r["b"]]), []).append(r)
        for (la, lb), rs in groups.items():
            fleetdb.cypher(conn, f"UNWIND $rows AS r WITH r.a AS ai, r.b AS bi, r.props AS props "
                                 f"MATCH (a:{la} {{id: ai}}), (b:{lb} {{id: bi}}) CREATE (a)-[e:{kind}]->(b) "
                                 f"SET e = props", {"rows": rs})
            n_edges += len(rs)
    conn.commit()
    print(f"graph {fleetdb.GRAPH}: {len(ends)} vertices, {n_edges} edges", flush=True)


def grant(conn):
    """Read for every role, through the access rule; the engineer may also record confirmed findings."""
    roles = [fleetdb.READER, fleetdb.CONTRACTOR, fleetdb.ENGINEER]
    schema = plant.COLLECTION.split(".")[0]
    for r in roles:
        conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO {r}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {r}")
    conn.execute("SELECT fleet.secure_graph(%s, %s::name[])", (fleetdb.GRAPH, roles))
    conn.execute("SELECT fleet.restrict_age_functions()")
    conn.execute(f"GRANT INSERT ON {plant.COLLECTION}_labels, {plant.COLLECTION}_episodes TO {fleetdb.ENGINEER}")
    conn.execute(f'GRANT INSERT ON {fleetdb.GRAPH}."RESEMBLES" TO {fleetdb.ENGINEER}')
    conn.execute(f'GRANT USAGE ON SEQUENCE {fleetdb.GRAPH}."RESEMBLES_id_seq" TO {fleetdb.ENGINEER}')
    gaps = conn.execute("SELECT * FROM fleet.graph_security_gaps(%s)", (fleetdb.GRAPH,)).fetchall()
    if gaps:
        raise SystemExit(f"access rule gaps: {gaps}")
    conn.commit()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path(os.environ.get("MORLET_DATA", "data")))
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--write-manifest", action="store_true", help=argparse.SUPPRESS)
    opts = p.parse_args(argv)
    started = time.monotonic()
    root = runs_root(opts.data)
    chosen = selection(root)
    check_manifest(root, chosen, write=opts.write_manifest)
    if opts.write_manifest:
        return
    with psycopg.connect(opts.dsn) as conn:
        setup(conn)
    import_recordings(opts.dsn, root, chosen)
    with psycopg.connect(opts.dsn) as conn:
        mirror_context(conn, chosen)
        write_graph(conn, chosen)
        grant(conn)
        n, windows = conn.execute(f"SELECT (SELECT count(*) FROM {plant.COLLECTION}), "
                                  f"(SELECT count(*) FROM {plant.COLLECTION}_envelopes)").fetchone()
    print(f"\nready in {time.monotonic() - started:.0f} s: {n} recordings, {windows} search windows in "
          f"{plant.COLLECTION}; graph {fleetdb.GRAPH}; roles {fleetdb.READER} (read, all sites), "
          f"{fleetdb.CONTRACTOR} (read, Site A), {fleetdb.ENGINEER} (confirms findings)", flush=True)


if __name__ == "__main__":
    main()
