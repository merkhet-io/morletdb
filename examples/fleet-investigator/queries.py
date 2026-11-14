# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The fixed queries of the fleet investigator: Cypher templates for the graph hops, and the single SQL statement
that joins a Cypher traversal with MorletDB's similarity search.

Used by graph_mcp.py (read-only tools), investigate.py (the scripted reference investigation) and the tests. Every
query is a constant; values are parameters. No pattern has a variable length: AGE evaluates those outside
row-level security (CHECKS.md, check 3), and this demo revokes them from non-owner roles.
"""
from datetime import datetime, timezone

import fleetdb
import plant

WINDOW = 4096


def utc(text):
    """An ISO 8601 time as the graph stores it (YYYY-MM-DDTHH:MM:SSZ, UTC), so string order is time order."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"{text}: give a time zone, e.g. 2004-04-17T06:00:00Z")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- graph hops ------------------------------------------------------------------------------------------------

PARENT = "MATCH (p)-[:CONTAINS]->(c {id: $id}) RETURN label(p), properties(p)"
NODE = "MATCH (n {id: $id}) RETURN label(n), properties(n)"
CURRENT_POSITION = ("MATCH (a:PhysicalAsset {id: $id})-[i:INSTALLED_IN]->(m:EquipmentModule) "
                    "RETURN m.id, i.from, i.to ORDER BY i.from DESC LIMIT 1")


def isa95_path(conn, node_id):
    """The ISA-95 equipment hierarchy above an equipment element (or above a physical asset's latest position), one
    fixed-length CONTAINS hop at a time, from the element up to the enterprise."""
    start = fleetdb.cypher(conn, NODE, {"id": node_id}, ("label", "props"))
    if not start:
        return None
    path, installed = [], None
    label, props = start[0]["label"], start[0]["props"]
    if label == "PhysicalAsset":
        pos = fleetdb.cypher(conn, CURRENT_POSITION, {"id": node_id}, ("module", "from", "to"))
        installed = pos[0] if pos else None
        path.append({"label": label, **props})
        if not pos:
            return {"path": path, "installed": None}
        node_id = pos[0]["module"]
        label, props = "EquipmentModule", fleetdb.cypher(conn, NODE, {"id": node_id}, ("label", "props"))[0]["props"]
    path.append({"label": label, **props})
    for _ in range(8):
        up = fleetdb.cypher(conn, PARENT, {"id": node_id}, ("label", "props"))
        if not up:
            break
        label, props = up[0]["label"], up[0]["props"]
        path.append({"label": label, **props})
        node_id = props["id"]
    return {"path": path, "installed": installed}


INSTALLED_AT = ("MATCH (a:PhysicalAsset)-[i:INSTALLED_IN]->(m:EquipmentModule {id: $id}) "
                "WHERE i.from <= $at AND (i.to IS NULL OR $at < i.to) "
                "RETURN a.id, a.lot, a.status, i.from, i.to, a.collection, a.first_recording_id, a.last_recording_id")
INSTALL_HISTORY = ("MATCH (a:PhysicalAsset)-[i:INSTALLED_IN]->(m:EquipmentModule {id: $id}) "
                   "RETURN a.id, a.lot, i.from, i.to ORDER BY i.from")
INSTALLED_COLS = ("asset", "lot", "status", "installed_from", "removed", "collection", "first_recording_id",
                  "last_recording_id")


def installed_at(conn, equipment_id, at):
    """The physical bearing installed in a bearing position at a time (INSTALLED_IN {from, to}, half-open), and the
    position's whole install history."""
    p = {"id": equipment_id, "at": utc(at)}
    now = fleetdb.cypher(conn, INSTALLED_AT, p, INSTALLED_COLS)
    history = fleetdb.cypher(conn, INSTALL_HISTORY, {"id": equipment_id}, ("asset", "lot", "installed_from", "removed"))
    return {"equipment_id": equipment_id, "at": p["at"], "installed": now[0] if now else None, "history": history}


LOT_OF = ("MATCH (a:PhysicalAsset {id: $id})-[:FROM_LOT]->(l:MaterialLot)-[:SUPPLIED_BY]->(s:Supplier) "
          "MATCH (l)-[:OF_DEFINITION]->(d:MaterialDefinition) RETURN properties(l), s.name, d.name")
LOT_INSTALLED = ("MATCH (l:MaterialLot {id: $lot})<-[:FROM_LOT]-(s:PhysicalAsset)-[i:INSTALLED_IN]->(m:EquipmentModule) "
                 "WHERE i.to IS NULL "
                 "MATCH (m)<-[:CONTAINS]-(:EquipmentModule)<-[:CONTAINS]-(u:Unit) "
                 "RETURN s.id, u.id, m.id, m.monitored, i.from, u.site ORDER BY u.id, m.id")
LOT_STORED = ("MATCH (l:MaterialLot {id: $lot})<-[:FROM_LOT]-(s:PhysicalAsset)-[:STORED_AT]->(st:StorageLocation) "
              "RETURN s.id, st.id, st.site ORDER BY st.id, s.id")
LOT_REMOVED = ("MATCH (l:MaterialLot {id: $lot})<-[:FROM_LOT]-(s:PhysicalAsset {status: 'removed'}) "
               "RETURN s.id ORDER BY s.id")


def same_lot(conn, asset_id):
    """The asset's material lot and every other bearing of that lot: installed now (where), on the shelf, removed."""
    lot = fleetdb.cypher(conn, LOT_OF, {"id": asset_id}, ("lot", "supplier", "definition"))
    if not lot:
        return None
    lot_id = lot[0]["lot"]["id"]
    installed = [r for r in fleetdb.cypher(conn, LOT_INSTALLED, {"lot": lot_id},
                                           ("asset", "unit", "equipment_id", "monitored", "installed_from", "site"))
                 if r["asset"] != asset_id]
    stored = fleetdb.cypher(conn, LOT_STORED, {"lot": lot_id}, ("asset", "store", "site"))
    removed = [r["asset"] for r in fleetdb.cypher(conn, LOT_REMOVED, {"lot": lot_id}, ("asset",))]
    return {"asset": asset_id, "lot": lot[0]["lot"], "supplier": lot[0]["supplier"],
            "material_definition": lot[0]["definition"], "installed_elsewhere": installed, "in_stock": stored,
            "removed": [a for a in removed if a != asset_id]}


FAILURES_OF_ASSET = ("MATCH (f:Failure)-[:OF_ASSET]->(a:PhysicalAsset {id: $id}) "
                     "MATCH (f)-[:CLASSIFIED_AS]->(fm:FailureMode)-[:SUBCLASS_OF]->(parent:FailureMode) "
                     "RETURN properties(f), fm.id, fm.name, parent.name ORDER BY f.detected")
FAILURES_OF_UNIT = ("MATCH (u:Unit {id: $id})-[:CONTAINS]->(:EquipmentModule)-[:CONTAINS]->(m:EquipmentModule) "
                    "MATCH (a:PhysicalAsset)-[:INSTALLED_IN]->(m) "
                    "MATCH (f:Failure)-[:OF_ASSET]->(a) "
                    "MATCH (f)-[:CLASSIFIED_AS]->(fm:FailureMode)-[:SUBCLASS_OF]->(parent:FailureMode) "
                    "RETURN properties(f), fm.id, fm.name, parent.name ORDER BY f.detected")
REPAIR_OF_FAILURE = ("MATCH (f:Failure {id: $id})<-[:FOUND]-(rs:MaintenanceResponse)-[:RESPONDS_TO]->(wo:WorkOrder) "
                     "MATCH (wo)-[:FULFILS]->(mr:MaintenanceRequest) "
                     "MATCH (wo)-[:REQUIRES]->(pc:PersonnelClass) "
                     "MATCH (wo)-[c:CONSUMED]->(l:MaterialLot) "
                     "RETURN properties(mr), properties(wo), properties(rs), pc.name, l.id, c.quantity")
ORDERS_OF_UNIT = ("MATCH (wo:WorkOrder)-[:ON_UNIT]->(u:Unit {id: $id}) "
                  "MATCH (wo)-[:FULFILS]->(mr:MaintenanceRequest)-[:FOR_EQUIPMENT]->(m:EquipmentModule) "
                  "MATCH (rs:MaintenanceResponse)-[:RESPONDS_TO]->(wo) "
                  "RETURN properties(wo), properties(mr), m.id, properties(rs) ORDER BY wo.opened")


def _failures(rows):
    return [{**r["failure"], "mode": r["mode"], "mode_name": r["mode_name"], "mode_class": r["parent"]} for r in rows]


def repair_of(conn, failure_id):
    rows = fleetdb.cypher(conn, REPAIR_OF_FAILURE, {"id": failure_id},
                          ("request", "work_order", "response", "personnel", "consumed_lot", "consumed"))
    return [{"request": r["request"], "work_order": r["work_order"], "response": r["response"],
             "personnel_class": r["personnel"], "consumed": {"lot": r["consumed_lot"], "quantity": r["consumed"]}}
            for r in rows]


def failure_history(conn, node_id):
    """Failures (with their mode in the failure-mode hierarchy) and the maintenance that answered them, for a physical
    asset or a unit; for a unit also every work order on it."""
    kind = fleetdb.cypher(conn, NODE, {"id": node_id}, ("label", "props"))
    if not kind:
        return None
    cols = ("failure", "mode", "mode_name", "parent")
    if kind[0]["label"] == "Unit":
        fails = _failures(fleetdb.cypher(conn, FAILURES_OF_UNIT, {"id": node_id}, cols))
        orders = [{"work_order": r["wo"], "request": r["mr"], "equipment_id": r["m"], "response": r["rs"]}
                  for r in fleetdb.cypher(conn, ORDERS_OF_UNIT, {"id": node_id}, ("wo", "mr", "m", "rs"))]
    elif kind[0]["label"] == "PhysicalAsset":
        fails, orders = _failures(fleetdb.cypher(conn, FAILURES_OF_ASSET, {"id": node_id}, cols)), None
    else:
        raise ValueError(f"{node_id} is a {kind[0]['label']}; give a unit (e.g. P-102) or a bearing serial")
    for f in fails:
        f["repair"] = repair_of(conn, f["id"])
    out = {"id": node_id, "label": kind[0]["label"], "failures": fails}
    if orders is not None:
        out["work_orders"] = orders
    return out


SPARES = ("MATCH (sp:SparePart)-[:HELD_AT]->(st:StorageLocation) MATCH (sp)-[:OF_DEFINITION]->(d:MaterialDefinition) "
          "RETURN properties(sp), st.id, st.name, d.name ORDER BY sp.id")
SPARES_ON_SHELF = ("MATCH (a:PhysicalAsset)-[s:STORED_AT]->(st:StorageLocation {id: $store}) "
                   "RETURN a.id, a.lot ORDER BY a.id")


def spare_stock(conn):
    """Spare bearings per store: stock record (on hand, reorder point) and the serials on the shelf with their lots."""
    out = []
    for r in fleetdb.cypher(conn, SPARES, None, ("spare", "store", "store_name", "definition")):
        shelf = fleetdb.cypher(conn, SPARES_ON_SHELF, {"store": r["store"]}, ("asset", "lot"))
        out.append({**r["spare"], "store": r["store"], "store_name": r["store_name"],
                    "material_definition": r["definition"], "serials": shelf})
    return out


UNITS = ("MATCH (c:ProcessCell)-[:CONTAINS]->(u:Unit)-[:OF_CLASS]->(ec:EquipmentClass) "
         "MATCH (s:Site)-[:CONTAINS]->(:Area)-[:CONTAINS]->(c) "
         "RETURN u.id, u.name, ec.name, c.name, s.name, u.site ORDER BY u.id")
MONITORED = ("MATCH (u:Unit)-[:CONTAINS]->(:EquipmentModule)-[:CONTAINS]->(m:EquipmentModule {monitored: true}) "
             "RETURN u.id, m.id, m.collection, m.first_recording_id, m.last_recording_id ORDER BY m.id")
META = "MATCH (m:Meta {id: 'model'}) RETURN properties(m)"


def plant_overview(conn):
    meta = fleetdb.cypher(conn, META)
    units = fleetdb.cypher(conn, UNITS, None, ("unit", "name", "equipment_class", "process_cell", "site_name", "site"))
    mon = fleetdb.cypher(conn, MONITORED, None, ("unit", "equipment_id", "collection", "first_recording_id",
                                                 "last_recording_id"))
    by_unit = {}
    for m in mon:
        by_unit.setdefault(m["unit"], []).append({k: m[k] for k in m if k != "unit"})
    for u in units:
        u["monitored_positions"] = by_unit.get(u["unit"], [])
    return {"now": meta[0]["r"]["now"] if meta else None, "units": units}


# --- the showcase: one SQL statement, a Cypher traversal joined with the waveform search ------------------------

SIMILAR_FAILURES = f"""
WITH pos AS (            -- graph: the bearing in this position at the time, and since when it is installed
  SELECT * FROM ag_catalog.cypher('{fleetdb.GRAPH}', $cy$
    MATCH (a:PhysicalAsset)-[i:INSTALLED_IN]->(m:EquipmentModule {{equipment_id: $equipment_id}})
    WHERE i.from <= $at AND (i.to IS NULL OR $at < i.to)
    RETURN a.id, a.lot, m.collection, i.from
  $cy$, %(graph_params)s) AS (asset text, lot text, collection text, installed_from text)
), query AS (            -- MorletDB: the position's latest recording at or before the time
  SELECT r.recording_id FROM {plant.COLLECTION} r, pos
  WHERE r.metadata->>'equipment_id' = %(equipment_id)s
    AND (r.metadata->>'start')::timestamptz <= %(at)s::timestamptz
    AND (r.metadata->>'start')::timestamptz >= pos.installed_from::timestamptz
  ORDER BY (r.metadata->>'start')::timestamptz DESC LIMIT 1
), installs AS (         -- graph: every bearing ever installed in a position, with its recorded failure, if any
  SELECT * FROM ag_catalog.cypher('{fleetdb.GRAPH}', $cy$
    MATCH (a:PhysicalAsset)-[i:INSTALLED_IN]->(m:EquipmentModule)
    OPTIONAL MATCH (f:Failure)-[:OF_ASSET]->(a)
    OPTIONAL MATCH (f)-[:CLASSIFIED_AS]->(fm:FailureMode)
    RETURN m.equipment_id, a.id, a.lot, i.from, i.to, f.id, fm.id, fm.name
  $cy$, %(graph_params)s) AS (equipment_id text, asset text, lot text, installed_from text, removed text,
                             failure text, mode text, mode_name text)
)
SELECT pos.asset AS query_asset, pos.lot AS query_lot, pos.installed_from AS query_installed_from,
       q.recording_id AS query_recording, h.recording_id, h.start_sample, h.end_sample, h.distance,
       h.metadata->>'start' AS start_utc, i.equipment_id, i.asset, i.lot, i.failure, i.mode, i.mode_name
FROM pos
CROSS JOIN query q
CROSS JOIN LATERAL public.morlet_search_similar(pos.collection::regclass, q.recording_id, 0, {WINDOW},
                                                 top_k => %(top_k)s,
                                                 time_to => pos.installed_from::timestamptz) h
LEFT JOIN installs i
  ON i.equipment_id = h.metadata->>'equipment_id'
 AND (h.metadata->>'start')::timestamptz >= i.installed_from::timestamptz
 AND (i.removed IS NULL OR (h.metadata->>'start')::timestamptz < i.removed::timestamptz)
ORDER BY h.distance, h.recording_id, h.start_sample
"""


def similar_failures_params(equipment_id, at, top_k=10):
    import json
    at = utc(at)
    return {"graph_params": json.dumps({"equipment_id": equipment_id, "at": at}), "equipment_id": equipment_id,
            "at": at, "top_k": top_k}


def similar_failures(conn, equipment_id, at, top_k=10):
    """One statement: graph (which bearing, installed since when) -> MorletDB (its latest recording; similar windows
    recorded before it was installed) -> graph (which bearing was in each hit's position at the hit's time, and how
    it failed). Similarity is evidence, not a diagnosis."""
    cur = conn.execute(SIMILAR_FAILURES, similar_failures_params(equipment_id, at, top_k))
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
