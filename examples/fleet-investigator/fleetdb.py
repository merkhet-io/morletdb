# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Small helpers shared by the loader, the tools and the tests: Cypher calls with parameters, and roles.

Cypher text is always a constant of the calling code; values go in as one agtype parameter (AGE's third argument
of cypher(), a bound statement parameter), never into the query text.
"""
import json
import re

GRAPH = "plant"

# Login roles of the demo (local demo credentials, as in the other examples).
READER, READER_PASSWORD = "fleet_reader", "fleet-demo-only"              # all sites, read only (MCP servers)
CONTRACTOR, CONTRACTOR_PASSWORD = "fleet_contractor", "fleet-contractor-demo-only"   # Site A only, read only
ENGINEER, ENGINEER_PASSWORD = "fleet_engineer", "fleet-engineer-demo-only"           # all sites, confirms findings
# Group roles that carry the site rule (fleet.site_access).
ALL_SITES, SITE_A = "fleet_all_sites", "fleet_site_a"

_COLUMN = re.compile(r"^[a-z_][a-z0-9_]*$")


def cypher_sql(query, names, params=True, graph=GRAPH):
    """The SQL text of one cypher() call returning agtype columns `names`."""
    if not _COLUMN.match(graph):
        raise ValueError("graph names here are lower-case identifiers")
    if any(not _COLUMN.match(n) for n in names):
        raise ValueError("column names must be lower-case identifiers")
    if "$cy$" in query:
        raise ValueError("query must not contain the dollar-quote tag")
    cols = ", ".join(f"{n} ag_catalog.agtype" for n in names)
    arg = ", %s" if params else ""
    return f"SELECT * FROM ag_catalog.cypher('{graph}', $cy${query}$cy${arg}) AS ({cols})"


def parse(value):
    """An agtype result as a Python value (maps, lists, strings, numbers, null)."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if text.endswith(("::vertex", "::edge", "::path")):
        raise ValueError("return properties(...) instead of whole vertices or edges")
    return json.loads(text)


def cypher(conn, query, params=None, names=("r",), graph=GRAPH):
    """Rows of a Cypher query as dicts. `params` is a dict passed as the agtype parameter ($name in the query)."""
    sql = cypher_sql(query, names, params is not None, graph)
    cur = conn.execute(sql, (json.dumps(params),) if params is not None else ())
    return [{n: parse(v) for n, v in zip(names, row)} for row in cur.fetchall()]


def set_search_path(conn):
    conn.execute('SET search_path = ag_catalog, "$user", public')
