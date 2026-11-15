#!/usr/bin/env python3
# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Read-only MCP server for the plant's ISA-95 context graph (Apache AGE), beside MorletDB's own server.

  DATABASE_URL=postgresql://fleet_reader@localhost/morlet python examples/fleet-investigator/graph_mcp.py   # stdio

A few typed questions about the plant, each a fixed, parameterized Cypher query (queries.py): there is no tool that
runs Cypher or SQL from the model. Every call runs in a read-only transaction with a statement timeout, as the role
in DATABASE_URL, so privileges and row-level security decide what the agent sees (a contractor role sees one site).
"""
import functools
import importlib.util
import json
import os
import sys

import psycopg

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


def _sibling(name):
    """Import a module next to this file by path, so `python -I` works and nothing is picked up from elsewhere."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                     f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleetdb = _sibling("fleetdb")
plant = _sibling("plant")
queries = _sibling("queries")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = f"""The plant's context graph, structured after ISA-95: enterprise > site > area > process cell > unit
(pumps P-..., blowers F-...) > equipment modules (motor, shaft, bearing positions such as P-102.BRG-3). Bearings are
physical assets with serial numbers (SN-...), installed in a bearing position for a time interval
(INSTALLED_IN {{from, to}}, half-open), from a material lot of one supplier. Failures (with a failure mode: inner race,
outer race, rolling element) were answered by maintenance requests, work orders and responses. The plant and its
organisation are fictional; the vibration recordings of the monitored positions are real (NASA/IMS bearing data) and
live in MorletDB collection {plant.COLLECTION}, where each recording's metadata carries the position's equipment_id.

Times are UTC (ISO 8601). The plant's present is {plant.NOW}. Only P-102 and F-212 are monitored; for other units the
graph is the only evidence, and "no recordings" is not "no change".

Use these tools for the plant (which bearing, which lot, what failed, what was done, what is in stock) and the
morletdb tools for the recordings. similar_failures does both in one statement: from a bearing position at a time it
searches recordings made before the current bearing was installed and resolves each hit to the bearing installed at
the hit's time and that bearing's recorded failure. Similar vibration is evidence for a hypothesis, not a diagnosis."""

mcp = MCPServer("fleet-graph", instructions=INSTRUCTIONS)


def safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, psycopg.Error) as exc:
            msg = exc.diag.message_primary if isinstance(exc, psycopg.Error) and exc.diag.message_primary else str(exc)
            raise ToolError(msg) from exc
    return wrapper


def connect():
    dsn = os.environ.get("DATABASE_URL", "")
    timeout = int(os.environ.get("FLEET_MCP_TIMEOUT_MS", "30000"))
    conn = psycopg.connect(dsn, options=f"-c default_transaction_read_only=on -c statement_timeout={timeout}")
    conn.read_only = True
    fleetdb.set_search_path(conn)
    return conn


def jsonable(x):
    return json.loads(json.dumps(x, default=str))


@mcp.tool(annotations=READ_ONLY)
@safe
def plant_overview() -> dict:
    """Units of the plant the role can see (id, name, equipment class, process cell, site) and, for monitored units,
    their bearing positions with the MorletDB collection and first and last recording id. Start here."""
    with connect() as conn:
        return jsonable(queries.plant_overview(conn))


@mcp.tool(annotations=READ_ONLY)
@safe
def isa95_path(element_id: str) -> dict:
    """The ISA-95 equipment hierarchy above an element, up to the enterprise: e.g. P-102.BRG-3 -> shaft -> unit P-102
    -> process cell -> area -> site -> enterprise. For a bearing serial (SN-...), the path above the position it is
    (or was last) installed in."""
    with connect() as conn:
        out = queries.isa95_path(conn, element_id)
    if out is None:
        raise ValueError(f"{element_id}: no such element (or not visible to this role)")
    return jsonable(out)


@mcp.tool(annotations=READ_ONLY)
@safe
def installed_at(equipment_id: str, utc_time: str) -> dict:
    """Which physical bearing (serial, lot) was installed in a bearing position (e.g. P-102.BRG-1) at a UTC time, and
    the position's install history. Use it to resolve a recording of a position (its metadata start) to the bearing
    that was actually running; installed is null when the position was empty at that time."""
    with connect() as conn:
        return jsonable(queries.installed_at(conn, equipment_id, utc_time))


@mcp.tool(annotations=READ_ONLY)
@safe
def same_lot(asset_id: str) -> dict:
    """The material lot of a bearing (serial SN-...) and every other bearing of that lot the role can see: installed
    now (unit, position, whether the position is monitored), on the shelf (store), or removed."""
    with connect() as conn:
        out = queries.same_lot(conn, asset_id)
    if out is None:
        raise ValueError(f"{asset_id}: no such bearing (or not visible to this role)")
    return jsonable(out)


@mcp.tool(annotations=READ_ONLY)
@safe
def failure_history(element_id: str) -> dict:
    """Recorded failures of a unit (e.g. P-102) or of a bearing (SN-...), each with its failure mode and the
    maintenance that answered it (request, work order, response, personnel class, parts consumed); for a unit also
    every work order on it."""
    with connect() as conn:
        out = queries.failure_history(conn, element_id)
    if out is None:
        raise ValueError(f"{element_id}: no such element (or not visible to this role)")
    return jsonable(out)


@mcp.tool(annotations=READ_ONLY)
@safe
def spare_stock() -> dict:
    """Spare bearings per store the role can see: on hand, reorder point, and the serials on the shelf with their
    lots (so a replacement from a suspect lot can be avoided)."""
    with connect() as conn:
        return {"stores": jsonable(queries.spare_stock(conn))}


@mcp.tool(annotations=READ_ONLY)
@safe
def similar_failures(equipment_id: str, utc_time: str, top_k: int = 10) -> dict:
    """One SQL statement over graph and recordings: the bearing in `equipment_id` at `utc_time` (graph), its latest
    recording at or before then (MorletDB), the top_k most similar 4096-sample windows recorded before that bearing
    was installed (MorletDB envelope search), and for each hit the bearing installed in the hit's position at the
    hit's time with its recorded failure and failure mode (graph). Distances: lower is closer; rank, do not
    threshold. Similarity is evidence, not a diagnosis."""
    top_k = max(1, min(int(top_k), 50))
    with connect() as conn:
        rows = queries.similar_failures(conn, equipment_id, utc_time, top_k)
    if not rows:
        raise ValueError(f"{equipment_id} at {utc_time}: no bearing installed, no recording since its install, or "
                         f"not visible to this role")
    first = rows[0]
    return jsonable({"query": {"equipment_id": equipment_id, "at": queries.utc(utc_time),
                               "asset": first["query_asset"], "lot": first["query_lot"],
                               "installed_from": first["query_installed_from"],
                               "recording_id": first["query_recording"], "start_sample": 0,
                               "end_sample": queries.WINDOW, "collection": plant.COLLECTION},
                     "searched": f"windows recorded before {first['query_installed_from']} (before this bearing "
                                 f"was installed)",
                     "hits": [{k: r[k] for k in ("recording_id", "start_sample", "end_sample", "distance",
                                                 "start_utc", "equipment_id", "asset", "lot", "failure", "mode",
                                                 "mode_name")} for r in rows]})


if __name__ == "__main__":
    mcp.run("stdio")
