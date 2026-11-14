#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The risk checks of CHECKS.md, runnable against a loaded demo database. tests/test_fleet.py asserts the same
functions; this script prints what they return.

  python examples/fleet-investigator/checks.py --host 127.0.0.1 --port 54333 --owner-password ...

Each check returns a dict of observations (no pass/fail verdicts of its own). Checks 3 and 5 build small fixture
graphs and collections next to the demo data (graphs rls_rules_first and rls_data_first, collections
fleet.rls_rules_first and fleet.rls_data_first), from two real recordings of the demo, and drop them afterwards.
"""
import argparse
import json
import sys
import threading
from pathlib import Path

import numpy as np
import psycopg
from psycopg import sql

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import confirm_finding  # noqa: E402
import fleetdb  # noqa: E402
import plant  # noqa: E402
import queries  # noqa: E402


class Db:
    """Connection strings for the owner and the demo roles."""

    def __init__(self, host, port, owner="morlet", owner_password="", dbname="morlet"):
        self.host, self.port, self.dbname, self.owner, self.owner_password = host, port, dbname, owner, owner_password
        self.passwords = {fleetdb.READER: fleetdb.READER_PASSWORD, fleetdb.CONTRACTOR: fleetdb.CONTRACTOR_PASSWORD,
                          fleetdb.ENGINEER: fleetdb.ENGINEER_PASSWORD, owner: owner_password}

    def dsn(self, role):
        return (f"host={self.host} port={self.port} dbname={self.dbname} user={role} "
                f"password={self.passwords[role]}")

    def connect(self, role=None, **kw):
        return psycopg.connect(self.dsn(role or self.owner), **kw)


def error_of(fn):
    try:
        fn()
        return None
    except psycopg.Error as exc:
        return exc.diag.message_primary


# --- 1. both extensions in one database ---------------------------------------------------------------------------

def check1(db):
    out = {}
    with db.connect(autocommit=True) as c:
        out["extensions"] = dict(c.execute("SELECT extname, extversion FROM pg_extension "
                                           "WHERE extname IN ('pg_morlet', 'age') ORDER BY 1").fetchall())
        out["server"] = c.execute("SHOW server_version").fetchone()[0]
        out["shared_preload_libraries"] = c.execute("SHOW shared_preload_libraries").fetchone()[0]
        out["owner_load_age"] = error_of(lambda: c.execute("LOAD 'age'")) or "ok"
    with db.connect(fleetdb.READER, autocommit=True) as c:
        out["reader_load_age"] = error_of(lambda: c.execute("LOAD 'age'"))
        out["reader_search_path"] = c.execute("SHOW search_path").fetchone()[0]
        out["reader_cypher"] = c.execute(
            "SELECT x::text, y::bigint FROM ag_catalog.cypher('plant', $$ MATCH (u:Unit {id: 'P-102'}) "
            "RETURN u.id, u.shaft_rpm $$) AS (x ag_catalog.agtype, y ag_catalog.agtype)").fetchall()
        c.execute("SET search_path = public")
        out["reader_cypher_without_ag_catalog_in_search_path"] = error_of(lambda: c.execute(
            "SELECT * FROM ag_catalog.cypher('plant', $$ MATCH (u:Unit {id: 'P-102'}) RETURN u.id $$) "
            "AS (x ag_catalog.agtype)"))
        c.execute('SET search_path = ag_catalog, "$user", public')
        out["reader_morlet_after_ag_catalog"] = c.execute(
            f"SELECT count(*) FROM morlet_coverage('{plant.COLLECTION}', ARRAY['P-102.BRG-3.20040417T115255'])"
        ).fetchone()[0]
    return out


# --- 2. one statement: Cypher output joined LATERAL with the waveform search ------------------------------------

PER_POSITION = f"""
SELECT g.unit::text AS unit, g.equipment_id::text AS equipment_id, g.rpm::bigint AS shaft_rpm,
       q.recording_id AS query_recording, s.recording_id AS hit, s.start_sample, round(s.distance::numeric, 4) AS distance
FROM ag_catalog.cypher('plant', $cy$
  MATCH (u:Unit)-[:CONTAINS]->(:EquipmentModule)-[:CONTAINS]->(m:EquipmentModule {{monitored: true}})
  RETURN u.id, m.equipment_id, u.shaft_rpm ORDER BY m.equipment_id
$cy$) AS g(unit ag_catalog.agtype, equipment_id ag_catalog.agtype, rpm ag_catalog.agtype)
CROSS JOIN LATERAL (SELECT r.recording_id FROM {plant.COLLECTION} r
                    WHERE r.metadata->>'equipment_id' = g.equipment_id::text
                    ORDER BY (r.metadata->>'start')::timestamptz DESC LIMIT 1) q
CROSS JOIN LATERAL morlet_search_similar('{plant.COLLECTION}', q.recording_id, 0, {queries.WINDOW}, top_k => 1) s
ORDER BY 2"""


def check2(db):
    out = {}
    with db.connect(fleetdb.READER) as c:
        out["per_position"] = [list(map(str, r)) for r in c.execute(PER_POSITION).fetchall()]
        out["per_position_explain"] = [r[0] for r in c.execute("EXPLAIN (COSTS OFF) " + PER_POSITION).fetchall()]
        p = queries.similar_failures_params("P-102.BRG-3", plant.NOW, 10)
        rows = queries.similar_failures(c, "P-102.BRG-3", plant.NOW, 10)
        out["similar_failures"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                                    if k in ("recording_id", "start_sample", "distance", "asset", "failure", "mode")}
                                   for r in rows[:5]]
        out["similar_failures_rows"] = len(rows)
        out["similar_failures_explain"] = [r[0] for r in c.execute(
            "EXPLAIN (COSTS OFF) " + queries.SIMILAR_FAILURES, p).fetchall()]
    return out


# --- 3. row-level security on AGE label tables and MorletDB, both enablement orders ------------------------------

FIXTURE = """
CREATE (e:Enterprise {id: 'E', site: '*'}),
       (a:Site {id: 'SA', site: 'A'}), (b:Site {id: 'SB', site: 'B'}),
       (ua:Unit {id: 'UA', site: 'A', recording_id: $ra}), (ub:Unit {id: 'UB', site: 'B', recording_id: $rb}),
       (lot:MaterialLot {id: 'L', site: '*'}), (fm:FailureMode {id: 'FM', site: '*'}),
       (e)-[:CONTAINS {site: 'A'}]->(a), (e)-[:CONTAINS {site: 'B'}]->(b),
       (a)-[:CONTAINS {site: 'A'}]->(ua), (b)-[:CONTAINS {site: 'B'}]->(ub),
       (ua)-[:FROM_LOT {site: 'A'}]->(lot), (ub)-[:FROM_LOT {site: 'B'}]->(lot),
       (ub)-[:FAILED_AS {site: 'B'}]->(fm)
"""
GRAPH_READS = {   # label: Cypher returning one agtype column
    "labeled MATCH (u:Unit)": "MATCH (u:Unit) RETURN u.id",
    "unlabeled MATCH (n)": "MATCH (n) RETURN n.id",
    "unlabeled edges": "MATCH ()-[e]->() RETURN e.site",
    "two fixed hops via a shared lot": "MATCH (u:Unit)-[:FROM_LOT]->(:MaterialLot)<-[:FROM_LOT]-(v:Unit) RETURN v.id",
    "three fixed hops to a failure mode": "MATCH (:Unit)-[:FROM_LOT]->(:MaterialLot)<-[:FROM_LOT]-(:Unit)"
                                          "-[:FAILED_AS]->(f:FailureMode) RETURN f.id",
    "count(*)": "MATCH (n) RETURN count(*)",
}
BYPASS_READS = {   # the same question as the last fixed pattern, as a variable-length path (through hidden UB)
    "variable-length path lot -[*2]- failure mode": "MATCH (:MaterialLot)-[*2]-(f:FailureMode) RETURN f.id",
    "variable-length path, its edges": "MATCH p = (:MaterialLot)-[*2]-(:FailureMode) RETURN relationships(p)",
}
FORGED_EDGE = "SELECT ag_catalog.age_startnode(%s::ag_catalog.agtype, %s::ag_catalog.agtype)"


def _copy_recording(c, collection, source_id, new_id, site):
    """A real demo recording copied into a fixture collection, bit for bit, with another site."""
    rate, ch, unit, meta = c.execute(f"SELECT sample_rate_hz, channel, unit, metadata FROM {plant.COLLECTION} "
                                     "WHERE recording_id = %s", (source_id,)).fetchone()
    raw = c.execute("SELECT morlet_read_interval_raw(%s::regclass, %s, 0, 20480)",
                    (plant.COLLECTION, source_id)).fetchone()[0]
    c.execute("SELECT morlet_start_recording(%s::regclass, %s, %s, %s, %s, %s::jsonb, 512, 256, 'zscore', "
              "'morlet-energy-v1', 0.001)",
              (collection, new_id, rate, ch, unit, json.dumps({**meta, "site": site})))
    c.execute("SELECT morlet_append_chunk(%s::regclass, %s, 0, %s::float8[])",
              (collection, new_id, np.frombuffer(raw, "<f8").tolist()))
    while c.execute("SELECT morlet_build_envelopes(%s::regclass, %s, 4096, 2048)", (collection, new_id)).fetchone()[0]:
        pass


def _morlet_side(c, collection, rules_first):
    """Collection with recordings for Site A and Site B, context, labels and episodes; the rule before or after."""
    schema, name = collection.split(".")
    c.execute("SELECT morlet_create_stream_collection(%s, %s)", (schema, name))
    c.execute("SELECT morlet_enable_envelopes(%s::regclass)", (collection,))
    if rules_first:
        c.execute("SELECT fleet.secure_collection(%s::regclass)", (collection,))
    c.execute("SELECT morlet_enable_context(%s::regclass)", (collection,))
    c.execute("SELECT morlet_enable_memory(%s::regclass)", (collection,))
    for rid, src, site in (("ra", "P-102.BRG-1.20040219T053239", "A"), ("rb", "F-212.BRG-3.20031125T232956", "B")):
        _copy_recording(c, collection, src, rid, site)
        c.execute("SELECT morlet_add_context(%s::regclass, %s, %s::jsonb)", (collection, f"ctx-{rid}", json.dumps(
            [{"recording_id": rid, "key": "job_order", "value": f"JO-{site}", "start_sample": 0, "end_sample": 20480}])))
        c.execute("SELECT morlet_add_label(%s::regclass, %s, 0, 4096, 'finding:resembles', %s)",
                  (collection, rid, site))
        c.execute("SELECT morlet_add_episodes(%s::regclass, %s::jsonb)", (collection, json.dumps(
            [{"recording_id": rid, "start_sample": 0, "end_sample": 4096, "exempt": True}])))
    if not rules_first:
        c.execute("SELECT fleet.secure_collection(%s::regclass)", (collection,))
    roles = [fleetdb.READER, fleetdb.CONTRACTOR, fleetdb.ENGINEER]
    for t in c.execute("SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE n.nspname = %s AND c.relname LIKE %s AND c.relkind IN ('r', 'p')",
                       (schema, name + "%")).fetchall():
        for r in roles:
            c.execute(f"GRANT SELECT ON {t[0]} TO {r}")


def companion_tables(c, collection):
    schema, name = collection.split(".")
    return [r[0] for r in c.execute(
        "SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'recording_id' AND NOT a.attisdropped "
        "WHERE n.nspname = %s AND (c.relname = %s OR c.relname LIKE %s) AND c.relkind = 'r' ORDER BY 1",
        (schema, name, name + r"\_%")).fetchall()]


def rls_fixture(db, order):
    """Build graph rls_<order> and collection fleet.rls_<order>; return what the contractor and the reader see."""
    graph, collection = f"rls_{order}", f"fleet.rls_{order}"
    rules_first = order == "rules_first"
    roles = [fleetdb.READER, fleetdb.CONTRACTOR, fleetdb.ENGINEER]
    out = {"order": order}
    drop_fixture(db, order)
    with db.connect() as c:
        fleetdb.set_search_path(c)
        _morlet_side(c, collection, rules_first)
        c.execute("SELECT ag_catalog.create_graph(%s)", (graph,))
        if rules_first:
            c.execute("SELECT fleet.secure_graph(%s, %s::name[])", (graph, roles))
        fleetdb.cypher(c, FIXTURE, {"ra": "ra", "rb": "rb"}, graph=graph)
        c.commit()
    out["gaps_after_data"] = _gaps(db, graph)
    if rules_first:
        # Labels created by the data after the rule ran: no policy, no grant yet. The contractor is refused.
        with db.connect(fleetdb.CONTRACTOR, autocommit=True) as u:
            out["contractor_on_unsecured_new_label"] = error_of(lambda: u.execute(
                fleetdb.cypher_sql("MATCH (u:Unit) RETURN u.id", ("r",), False, graph)))
            out["contractor_unlabeled_match_before_resecuring"] = sorted(
                str(r[0]) for r in u.execute(fleetdb.cypher_sql("MATCH (n) RETURN n.id", ("r",), False, graph)
                                             ).fetchall())
        with db.connect() as c:
            c.execute("SELECT fleet.secure_graph(%s, %s::name[])", (graph, roles))
            c.commit()
    else:
        with db.connect() as c:
            c.execute("SELECT fleet.secure_graph(%s, %s::name[])", (graph, roles))
            c.commit()
    out["gaps_after_securing"] = _gaps(db, graph)
    with db.connect() as c:
        ub = c.execute(f'''SELECT id::text FROM {graph}."Unit" WHERE properties::text LIKE '%%"UB"%%' ''').fetchone()[0]
        label_tables = [r[0] for r in c.execute(
            "SELECT l.relation::regclass::text FROM ag_catalog.ag_label l JOIN ag_catalog.ag_graph g "
            "ON g.graphid = l.graph WHERE g.name = %s ORDER BY 1", (graph,)).fetchall()]
    ub_edge = '{"id": 1, "label": "CONTAINS", "start_id": %s, "end_id": %s, "properties": {}}::edge' % (ub, ub)
    if not rules_first:
        out["contractor with the AGE functions granted (AGE's default)"] = bypass_when_granted(db, graph, ub_edge)
    for role in (fleetdb.CONTRACTOR, fleetdb.READER):
        seen = {}
        with db.connect(role, autocommit=True) as u:
            for label, q in {**GRAPH_READS, **BYPASS_READS}.items():
                text = fleetdb.cypher_sql(q, ("r",), False, graph)
                try:
                    seen[label] = sorted(str(r[0]) for r in u.execute(text).fetchall())
                except psycopg.Error as exc:
                    seen[label] = "ERROR: " + exc.diag.message_primary
            seen["direct SQL per label table"] = {t: u.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                                                  for t in label_tables}
            hidden = u.execute(f'SELECT count(*) FROM {graph}."Unit"').fetchone()[0]
            seen["forged edge into startNode"] = error_of(lambda: u.execute(FORGED_EDGE, (f'"{graph}"', ub_edge)))
            seen["vertex_stats"] = error_of(lambda: u.execute(
                "SELECT ag_catalog.age_vertex_stats(%s::ag_catalog.agtype, "
                "'{\"id\": 1, \"label\": \"Unit\", \"properties\": {}}::vertex'::ag_catalog.agtype)", (f'"{graph}"',)))
            seen["units visible by SQL"] = hidden
            seen["MorletDB tables (rows)"] = {t: u.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                                              for t in companion_tables(u, collection)}
            seen["MorletDB recordings"] = [r[0] for r in u.execute(
                f"SELECT recording_id FROM {collection} ORDER BY 1").fetchall()]
            seen["search from ra"] = sorted({r[0] for r in u.execute(
                "SELECT recording_id FROM morlet_search_similar(%s::regclass, 'ra', 0, 4096, top_k => 10, "
                "exclude_source => false)", (collection,)).fetchall()})
            seen["search from rb"] = error_of(lambda: u.execute(
                "SELECT * FROM morlet_search_similar(%s::regclass, 'rb', 0, 4096, top_k => 10)",
                (collection,)).fetchall()) or "ok"
        out[role] = seen
    return out


def _gaps(db, graph):
    with db.connect() as c:
        return [list(r) for r in c.execute("SELECT * FROM fleet.graph_security_gaps(%s)", (graph,)).fetchall()]


def bypass_when_granted(db, graph, ub_edge):
    """What the contractor reads when the AGE functions keep their default EXECUTE grant to PUBLIC."""
    with db.connect(autocommit=True) as c:
        fns = [r[0] for r in c.execute("SELECT f::text FROM fleet.age_bypass_functions() f").fetchall()]
        for f in fns:
            c.execute(f"GRANT EXECUTE ON FUNCTION {f} TO PUBLIC")
    out = {}
    try:
        with db.connect(fleetdb.CONTRACTOR, autocommit=True) as u:
            for label, q in BYPASS_READS.items():
                out[label] = sorted(str(r[0]) for r in u.execute(fleetdb.cypher_sql(q, ("r",), False, graph)))
            out["forged edge into startNode"] = [str(r[0]) for r in u.execute(FORGED_EDGE, (f'"{graph}"', ub_edge))]
            ub = ub_edge.split('"start_id": ')[1].split(",")[0]
            out["vertex_stats of the hidden unit"] = [str(r[0]) for r in u.execute(
                "SELECT ag_catalog.age_vertex_stats(%s::ag_catalog.agtype, %s::ag_catalog.agtype)",
                (f'"{graph}"', '{"id": %s, "label": "Unit", "properties": {}}::vertex' % ub))]
    finally:
        with db.connect(autocommit=True) as c:
            c.execute("SELECT fleet.restrict_age_functions()")
    return out


def drop_fixture(db, order):
    with db.connect(autocommit=True) as c:
        fleetdb.set_search_path(c)
        if c.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = %s", (f"rls_{order}",)).fetchone():
            c.execute("SELECT ag_catalog.drop_graph(%s, true)", (f"rls_{order}",))
        c.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier("fleet", f"rls_{order}")))
        for t in c.execute("SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                           "WHERE n.nspname = 'fleet' AND c.relname LIKE %s AND c.relkind = 'r'",
                           (f"rls\\_{order}\\_%",)).fetchall():
            c.execute(f"DROP TABLE IF EXISTS {t[0]} CASCADE")


def regrant_detected(db):
    """If an AGE bypass function is granted to PUBLIC again (e.g. by an extension update), the gap check shows it."""
    with db.connect(autocommit=True) as c:
        f = c.execute("SELECT f::text FROM fleet.age_bypass_functions() f WHERE f::text LIKE 'ag_catalog.age_vle(%%' "
                      "LIMIT 1").fetchone()[0]
        c.execute(f"GRANT EXECUTE ON FUNCTION {f} TO PUBLIC")
        gaps = [list(r) for r in c.execute("SELECT * FROM fleet.graph_security_gaps('plant')").fetchall()]
        c.execute("SELECT fleet.restrict_age_functions()")
        after = [list(r) for r in c.execute("SELECT * FROM fleet.graph_security_gaps('plant')").fetchall()]
    return {"function": f, "gaps_while_granted": gaps, "gaps_after_revoking": after}


def event_trigger_on_new_labels(db):
    """Does a ddl_command_end event trigger see AGE creating a label table? (If it did, it could secure new labels.)"""
    with db.connect(autocommit=True) as c:
        fleetdb.set_search_path(c)
        c.execute("CREATE TABLE fleet.ddl_seen(tag text, object text)")
        c.execute("CREATE FUNCTION fleet.ddl_log() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN "
                  "INSERT INTO fleet.ddl_seen SELECT command_tag, object_identity FROM pg_event_trigger_ddl_commands(); "
                  "END $$")
        c.execute("CREATE EVENT TRIGGER fleet_ddl_probe ON ddl_command_end EXECUTE FUNCTION fleet.ddl_log()")
        try:
            c.execute("SELECT ag_catalog.create_graph('ddl_probe')")
            c.execute("TRUNCATE fleet.ddl_seen")
            c.execute("SELECT ag_catalog.create_vlabel('ddl_probe', 'ByFunction')")
            fleetdb.cypher(c, "CREATE (:ByCreate {site: 'A'})", graph="ddl_probe")
            c.execute("CREATE TABLE fleet.ddl_control(x int)")       # control: ordinary DDL is seen
            seen = [list(r) for r in c.execute("SELECT * FROM fleet.ddl_seen ORDER BY 1, 2").fetchall()]
            tables = [r[0] for r in c.execute("SELECT l.name FROM ag_catalog.ag_label l JOIN ag_catalog.ag_graph g "
                                              "ON g.graphid = l.graph WHERE g.name = 'ddl_probe' ORDER BY 1")]
        finally:
            c.execute("DROP EVENT TRIGGER fleet_ddl_probe")
            c.execute("DROP FUNCTION fleet.ddl_log()")
            c.execute("DROP TABLE IF EXISTS fleet.ddl_seen, fleet.ddl_control")
            c.execute("SELECT ag_catalog.drop_graph('ddl_probe', true)")
    return {"labels created": tables, "ddl events seen": seen}


def check3(db):
    out = {"event trigger on new labels": event_trigger_on_new_labels(db)}
    for order in ("rules_first", "data_first"):
        try:
            out[order] = rls_fixture(db, order)
        finally:
            drop_fixture(db, order)
    out["regrant"] = regrant_detected(db)
    out["demo"] = demo_rls(db)
    return out


def demo_rls(db):
    """The demo itself: what the contractor (Site A) and the reader (all sites) see of graph and recordings."""
    out = {}
    for role in (fleetdb.CONTRACTOR, fleetdb.READER):
        with db.connect(role, autocommit=True) as u:
            fleetdb.set_search_path(u)
            sites = fleetdb.cypher(u, "MATCH (n) RETURN DISTINCT n.site")
            units = [r["r"] for r in fleetdb.cypher(u, "MATCH (u:Unit) RETURN u.id ORDER BY u.id")]
            recs = dict(u.execute(f"SELECT metadata->>'site', count(*) FROM {plant.COLLECTION} GROUP BY 1 ORDER BY 1"
                                  ).fetchall())
            tables = {t: u.execute(f"SELECT count(DISTINCT recording_id) FROM {t}").fetchone()[0]
                      for t in companion_tables(u, plant.COLLECTION)}
            siblings = queries.same_lot(u, "SN-0402-007")
            out[role] = {"graph sites": sorted(str(s["r"]) for s in sites), "units": len(units),
                         "units at site B": [x for x in units if x.startswith(("P-2", "F-2"))],
                         "recordings by site": recs, "distinct recordings per table": tables,
                         "LOT-0402 installed elsewhere": [s["equipment_id"] for s in siblings["installed_elsewhere"]],
                         "F-212 history": error_of(lambda: queries.failure_history(u, "F-212")) or
                         ("visible" if queries.failure_history(u, "F-212") else "not visible")}
    return out


# --- 4. atomic write-back --------------------------------------------------------------------------------------

def _counts(c, finding_id):
    fleetdb.set_search_path(c)
    edges = len(fleetdb.cypher(c, confirm_finding.EXISTING, {"id": finding_id}))
    labels = c.execute(f"SELECT count(*) FROM {plant.COLLECTION}_labels WHERE note LIKE %s",
                       (f'%"{finding_id}"%',)).fetchone()[0]
    episodes = c.execute(f"SELECT count(*) FROM {plant.COLLECTION}_episodes WHERE context->>'finding_id' = %s",
                         (finding_id,)).fetchone()[0]
    return {"graph_edges": edges, "labels": labels, "episodes": episodes}


def test_finding(suffix, recording="P-102.BRG-4.20040417T115255", end=4096):
    return {"finding_id": f"FND-CHECK-{suffix}", "asset": "SN-0402-008", "equipment_id": "P-102.BRG-4",
            "failure": "FL-2004-02-P102-BRG1", "mode": "FM-OR",
            "query": {"collection": plant.COLLECTION, "recording_id": recording, "start_sample": 0,
                      "end_sample": end},
            "evidence": {"note": "check fixture, not a real finding"}}


def remove_finding(db, finding_id):
    with db.connect() as c:
        fleetdb.set_search_path(c)
        fleetdb.cypher(c, "MATCH ()-[r:RESEMBLES {finding_id: $id}]->() DELETE r", {"id": finding_id})
        c.execute(f"DELETE FROM {plant.COLLECTION}_labels WHERE note LIKE %s", (f'%"{finding_id}"%',))
        c.execute(f"DELETE FROM {plant.COLLECTION}_episodes WHERE context->>'finding_id' = %s", (finding_id,))
        c.commit()


def check4(db):
    out = {}
    cases = {"injected failure before commit": (test_finding("ROLLBACK"), True),
             "MorletDB refuses the episode (interval beyond the recording)": (test_finding("BAD", end=40960), False),
             "committed": (test_finding("OK"), False)}
    for name, (finding, inject) in cases.items():
        with db.connect(fleetdb.ENGINEER) as c:
            try:
                status = confirm_finding.write_finding(c, finding, "PC-RELENG", fail_before_commit=inject)
            except (RuntimeError, psycopg.Error, ValueError) as exc:
                status = f"rolled back: {exc}".splitlines()[0]
        with db.connect() as c:
            out[name] = {"status": status, "after": _counts(c, finding["finding_id"])}
    with db.connect(fleetdb.ENGINEER) as c:
        out["identical retry"] = confirm_finding.write_finding(c, test_finding("OK"), "PC-RELENG")
    with db.connect(fleetdb.ENGINEER) as c:
        changed = test_finding("OK")
        changed["evidence"] = {"note": "different evidence under the same id"}
        try:
            out["conflicting retry"] = confirm_finding.write_finding(c, changed, "PC-RELENG")
        except confirm_finding.Conflict as exc:
            out["conflicting retry"] = f"refused: {exc}"
    with db.connect(fleetdb.CONTRACTOR) as c:
        try:
            out["contractor writes"] = confirm_finding.write_finding(c, test_finding("CONTRACTOR"), "PC-CONTRACT")
        except psycopg.Error as exc:
            out["contractor writes"] = "refused: " + exc.diag.message_primary
    for f in ("ROLLBACK", "BAD", "OK", "CONTRACTOR"):
        remove_finding(db, f"FND-CHECK-{f}")
    return out


# --- 5. one snapshot over graph and recordings ------------------------------------------------------------------

def check5(db):
    """A REPEATABLE READ transaction reads graph and MorletDB, a concurrent transaction commits a finding (graph edge,
    MorletDB label and episode), and the first transaction reads again."""
    finding = test_finding("SNAPSHOT")
    out = {}
    for level in ("REPEATABLE READ", "READ COMMITTED"):
        remove_finding(db, finding["finding_id"])
        with db.connect(fleetdb.READER) as t1:
            t1.execute(f"BEGIN ISOLATION LEVEL {level}")
            before = _counts(t1, finding["finding_id"])
            before["search_hits"] = len(queries.similar_failures(t1, "P-102.BRG-3", plant.NOW, 5))
            done = threading.Event()

            def writer():
                with db.connect(fleetdb.ENGINEER) as t2:
                    confirm_finding.write_finding(t2, finding, "PC-RELENG")
                done.set()
            th = threading.Thread(target=writer)
            th.start()
            th.join(60)
            during = _counts(t1, finding["finding_id"])
            during["search_hits"] = len(queries.similar_failures(t1, "P-102.BRG-3", plant.NOW, 5))
            t1.execute("COMMIT")
            after = _counts(t1, finding["finding_id"])
            t1.commit()
        out[level] = {"writer committed": done.is_set(), "before": before, "after the concurrent commit": during,
                      "new transaction": after}
    remove_finding(db, finding["finding_id"])
    out["new recording and its graph vertex"] = snapshot_with_new_recording(db)
    return out


def snapshot_with_new_recording(db):
    """A concurrent transaction adds a recording (a real demo snapshot, copied) to a fixture collection and a vertex
    naming it to a fixture graph, as an ingester keeping both in step would; a REPEATABLE READ reader sees neither
    until it starts a new transaction, in tables, Cypher and search alike."""
    graph, collection = "rls_data_first", "fleet.rls_data_first"
    drop_fixture(db, "data_first")
    try:
        with db.connect() as c:
            fleetdb.set_search_path(c)
            _morlet_side(c, collection, False)
            c.execute("SELECT ag_catalog.create_graph(%s)", (graph,))
            fleetdb.cypher(c, FIXTURE, {"ra": "ra", "rb": "rb"}, graph=graph)
            c.execute("SELECT fleet.secure_graph(%s, %s::name[])", (graph, [fleetdb.READER]))
            c.commit()

        def look(t):
            return {"recordings": t.execute(f"SELECT count(*) FROM {collection}").fetchone()[0],
                    "unit vertices": len(fleetdb.cypher(t, "MATCH (u:Unit) RETURN u.id", graph=graph)),
                    "search hits from ra": sorted({r[0] for r in t.execute(
                        "SELECT recording_id FROM morlet_search_similar(%s::regclass, 'ra', 0, 4096, top_k => 20, "
                        "exclude_source => false)", (collection,)).fetchall()})}
        with db.connect(fleetdb.READER) as t1:
            fleetdb.set_search_path(t1)
            t1.commit()
            t1.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            before = look(t1)
            with db.connect() as t2:
                fleetdb.set_search_path(t2)
                _copy_recording(t2, collection, "P-102.BRG-3.20040417T115255", "rc", "A")
                fleetdb.cypher(t2, "CREATE (:Unit {id: 'UC', site: 'A', recording_id: 'rc'})", graph=graph)
                t2.commit()
            during = look(t1)
            t1.execute("COMMIT")
            after = look(t1)
            t1.commit()
        return {"before": before, "after the concurrent commit": during, "new transaction": after}
    finally:
        drop_fixture(db, "data_first")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=54333)
    p.add_argument("--owner", default="morlet")
    p.add_argument("--owner-password", default="morlet-demo-only")
    p.add_argument("--only", help="comma-separated check numbers")
    opts = p.parse_args(argv)
    db = Db(opts.host, opts.port, opts.owner, opts.owner_password)
    checks = {"1": check1, "2": check2, "3": check3, "4": check4, "5": check5}
    for k in (opts.only.split(",") if opts.only else checks):
        print(f"=== check {k}")
        print(json.dumps(checks[k](db), indent=1, default=str))


if __name__ == "__main__":
    main()
