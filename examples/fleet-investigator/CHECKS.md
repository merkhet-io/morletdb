# Risk checks: AGE and pg_morlet in one database

Five checks of what the fleet investigator claims. They run as [checks.py](checks.py) against a freshly loaded
demo database; [reference/checks_output.txt](reference/checks_output.txt) is a complete run, and
[tests/test_fleet.py](tests/test_fleet.py) asserts the same functions on a disposable container. Setup: the demo
image (MorletDB 0.3.14-alpha.1 release image, PostgreSQL 17.6, pg_morlet SQL 0.3.11, Apache AGE 1.6.0), measured on
2026-11-14. Roles: `fleet_reader` (all sites, read), `fleet_contractor` (Site A only, read), `fleet_engineer` (all
sites, may record findings); the owner `morlet` is a superuser and bypasses RLS, so no check relies on it as a
reader.

| # | Check | Outcome |
|---|---|---|
| 1 | Both extensions in one database | **Holds**, with `shared_preload_libraries = 'age'` and `ag_catalog` in `search_path` |
| 2 | One statement: Cypher joined LATERAL with the waveform search | **Holds** |
| 3 | RLS on AGE label tables restricts `cypher()` for a non-owner role, both orders | **Holds for fixed-length patterns and direct SQL, in both orders, only with two measures.** AGE's variable-length paths, `startNode`/`endNode` and `vertex_stats` **bypass RLS** with AGE's default grants |
| 4 | Atomic write-back to graph and MorletDB | **Holds** |
| 5 | One REPEATABLE READ snapshot over graph and recordings | **Holds** |

## 1. AGE and pg_morlet in one database

- `CREATE EXTENSION pg_morlet` and `CREATE EXTENSION age` in the same database: `pg_morlet 0.3.11`, `age 1.6.0`.
  Order does not matter (the loader creates pg_morlet first).
- **Loading AGE.** `LOAD 'age'` works for the superuser but a non-superuser gets `access to library "age" is not
  allowed` (the library is not under `$libdir/plugins`). Without AGE's parser hook in the session, `cypher()`
  cannot run. The demo image therefore starts PostgreSQL with `shared_preload_libraries=age` (its `CMD`); the
  release image itself is unchanged.
- **search_path.** With `search_path = public`, even a qualified `ag_catalog.cypher(...)` with a property match
  fails: `operator does not exist: ag_catalog.agtype @> ag_catalog.agtype` (AGE's generated query resolves
  operators by search path). The roles get `ALTER ROLE ... SET search_path = ag_catalog, "$user", public`. pg_morlet
  functions keep working with `ag_catalog` first (they pin their own search path); the MorletDB MCP server runs
  unchanged under these roles.
- agtype casts: `x::text` (strings, unquoted) and `x::bigint` work on cypher() output columns
  (`SELECT x::text, y::bigint FROM ag_catalog.cypher(...) AS (x agtype, y agtype)` returned `P-102, 2000`).
- Non-owner roles cannot read `ag_catalog.ag_label` (`permission denied`), yet `cypher()` works for them: AGE reads
  its catalog internally. Tools that need the label list ask as the owner.

## 2. One SQL statement over graph and search

[queries.py](queries.py) `SIMILAR_FAILURES` is a single statement in three parts:

1. `pos` (Cypher): the bearing installed in `P-102.BRG-3` at the given time (`INSTALLED_IN {from, to}`), its lot,
   the position's collection, and the install time.
2. `query` (SQL on the recordings) and `CROSS JOIN LATERAL morlet_search_similar(pos.collection::regclass,
   q.recording_id, 0, 4096, top_k => ..., time_to => pos.installed_from::timestamptz)`: the position's latest
   recording, and the most similar windows recorded **before that bearing was installed**. `morlet_search_similar`
   is the function [search-contract.md](../../docs/search-contract.md) names for this: without pgvector it takes the
   exhaustive path, so every returned distance is exact over every eligible stored window.
3. `installs` (Cypher, with `OPTIONAL MATCH` for failures and modes) joined to each hit on the hit's
   `equipment_id` and start time: which bearing was installed in the hit's position at the hit's time, and how it
   failed.

Result (reader, top 10 at 2004-04-17 12:00): all ten hits are P-102 `BRG-1` snapshots of 17–19 February 2004,
resolved to bearing SN-0311-001 and failure FL-2004-02-P102-BRG1 (`FM-OR`); best distance 0.3562.
`EXPLAIN (COSTS OFF)` (abridged; full plan in the reference output):

```
Sort  (Sort Key: h.distance, h.recording_id, h.start_sample)
  CTE pos
    ->  Nested Loop ... Seq Scan on "EquipmentModule" m_1
              Filter: (fleet.graph_site_visible(properties) AND (properties @> '{"equipment_id": "P-102.BRG-3"}'::agtype))
        ->  Seq Scan on "INSTALLED_IN" i_1  Filter: (fleet.graph_site_visible(properties) AND (... "from" <= ...) ...)
        ->  Seq Scan on "PhysicalAsset" a_1  Filter: fleet.graph_site_visible(properties)
  ->  Hash Right Join  (Hash Cond: equipment_id of the install = h.metadata->>'equipment_id'; Join Filter: start in [from, to))
        ->  Hash Right Join ... Seq Scan on "FailureMode" / "CLASSIFIED_AS" / "Failure" / "OF_ASSET" /
            "EquipmentModule" / "PhysicalAsset" / "INSTALLED_IN", each  Filter: fleet.graph_site_visible(properties)
        ->  Hash
              ->  Nested Loop
                    ->  Limit -> Sort -> Seq Scan on vibration r
                          Filter: (fleet.site_visible((metadata ->> 'site')) AND (metadata ->> 'equipment_id') = 'P-102.BRG-3' AND ...)
                    ->  Nested Loop -> CTE Scan on pos
                                    -> Function Scan on morlet_search_similar h
```

AGE compiles each Cypher pattern into ordinary scans of its label tables, so the planner sees one query, and the
row-level security filter appears on every scan, graph and recordings alike. A second statement in check 2
(`PER_POSITION`) runs the search LATERAL **per row of Cypher output** (every monitored position, eight searches in
one statement). The joined statement took about 0.4 s in the reference trace (one run, machine shared with other
work).

## 3. Row-level security on AGE label tables

AGE stores each vertex and edge label as an ordinary table in the graph's schema, inheriting from
`_ag_label_vertex` or `_ag_label_edge`. [security.sql](security.sql) puts **one rule** on both stores: a table
`fleet.site_access(role, site)` and a function `fleet.site_visible(site)`. The recordings table gets a policy on
`metadata->>'site'`, made binding for every companion table by `morlet_enable_row_security`
([access-control.md](../../docs/access-control.md)). Every label table gets the same rule on its `properties` site
(`fleet.secure_graph`, which also grants SELECT on exactly those tables).

**Fixture, both orders.** For each order a small graph (enterprise, sites A and B, one unit each, a shared lot and
failure mode, edges with sites) and a collection with two real recordings (one per site, copied bit for bit from
the demo), with context, labels and episodes:

- *rules first*: the collection policy and `morlet_enable_row_security` before enabling context and memory and
  before any data; `fleet.secure_graph` on the empty graph, before the labels exist;
- *data first*: all data, context, labels and episodes first, then the rules.

What `fleet_contractor` (Site A) sees, the same in both orders once the graph is secured:

| Read | Contractor | Reader (all sites) |
|---|---|---|
| `MATCH (u:Unit)` | UA | UA, UB |
| `MATCH (n)` (unlabeled: scans the parent table) | E, FM, L, SA, UA | all 7 |
| `MATCH ()-[e]->()` | 3 Site A edges | 7 |
| two and three fixed hops through the hidden unit (`(:Unit)-[:FROM_LOT]->(:MaterialLot)<-[:FROM_LOT]-(v)`, on to the failure mode) | nothing | UA, UB / FM |
| direct `SELECT` on every label table and both parents | Site A and shared rows only | all |
| recordings table and every companion table (chunks, envelopes, segments, context, labels, episodes) | exactly half the rows: recording `ra` only | both |
| `morlet_search_similar` from `ra` | hits in `ra` only | `ra`, `rb` |
| search from the Site B recording `rb` | `source recording does not exist or is not visible` | ok |

**Where AGE bypasses RLS** (measured with AGE's default grants, which give EXECUTE to PUBLIC):

- **Variable-length paths.** `MATCH (:MaterialLot)-[*2]-(f:FailureMode) RETURN f.id` returned `FM` to the
  contractor: the path runs through the hidden Site B unit. `relationships(p)` on that path returned the hidden
  Site B edges with their properties. The fixed-length version of the same question returns nothing. AGE evaluates
  variable-length patterns in `age_vle` from its own cached copy of the graph, built by scanning the tables outside
  the query, so policies never apply.
- **`startNode()` / `endNode()`.** Given a hand-written edge value whose `start_id` is the hidden unit's id,
  `age_startnode` (and Cypher `startNode(...)`) returned the hidden vertex with its properties.
- **`vertex_stats`.** `age_vertex_stats` returned the hidden unit's in- and out-degree.

**Measures, and what then holds:**

1. `fleet.restrict_age_functions()` revokes EXECUTE on `age_vle`, `age_startnode`, `age_endnode`,
   `age_vertex_stats` and `age_delete_global_graphs` from PUBLIC. The contractor then gets `permission denied for
   function age_vle` (and likewise for the others); fixed-length patterns keep working. No non-owner role in this
   demo may use variable-length paths; the graph tools do not need them (each hop is a fixed pattern, and the ISA-95
   path is walked one `CONTAINS` hop at a time).
2. **New labels.** AGE creates a table for each new label (`create_vlabel` or a `CREATE` with a new label) without
   firing event triggers: a `ddl_command_end` trigger recorded an ordinary `CREATE TABLE` (the control) but neither
   `create_vlabel` nor a Cypher `CREATE` of a new label, so no trigger can secure new labels. A new label table has no policy and no grant until `fleet.secure_graph` runs again. In the rules-first
   fixture, before re-securing, the contractor's `MATCH (u:Unit)` got `permission denied for table Unit` (fails
   closed), and an unlabeled `MATCH (n)` still returned only Site A and shared vertices: when a parent table is
   scanned, its own policy applies to the children's rows.
3. `fleet.graph_security_gaps(graph)` lists label tables without RLS or without the policy, login roles that may
   execute a bypass function, and BYPASSRLS roles. The loader refuses to finish while it is not empty. Re-granting
   `age_vle` to PUBLIC (as an `ALTER EXTENSION age UPDATE` might) shows up there for every demo role; revoking
   clears it.

**On the demo data**, as the contractor: graph sites `*` and `A` only, 8 of 16 units, no Site B unit; 4,816 of 6,256
recordings, the same 4,816 in chunks, envelopes, segments and context; the LOT-0402 siblings at Site A only;
F-212's failure history not visible.

So: **one set of access rules holds as tested** for direct SQL on every table of both stores and for fixed-length
Cypher, in both enablement orders, given the two measures above. It does not hold for AGE's variable-length paths
or id-based lookups, which this demo therefore withholds from non-owner roles. These are tested paths, not a proof.

## 4. Atomic write-back

[confirm_finding.py](confirm_finding.py) `write_finding`, as `fleet_engineer`, in one transaction:
`morlet_add_label` (label `finding:resembles` on the query interval, note = the evidence),
`morlet_add_episodes` (exempt episode, kept by compaction), then Cypher
`CREATE (a:PhysicalAsset)-[:RESEMBLES {finding_id, evidence, label_id, episode_id, ...}]->(f:Failure)`.

| Case | Result | Graph edges / labels / episodes after |
|---|---|---|
| error injected after all three writes, before commit | rolled back | 0 / 0 / 0 |
| MorletDB refuses the episode (interval beyond the recording), after the label was written | rolled back: `episode interval 0..40960 is outside recording ... (0..20480)` | 0 / 0 / 0 |
| normal | written | 1 / 1 / 1 |
| identical retry | `exists`, nothing written | 1 / 1 / 1 |
| same finding id, different evidence | refused | unchanged |
| as `fleet_contractor` | `permission denied for table vibration_labels` | nothing |

The CLI writes only after the person types the finding's id; anything else exits with status 2 and writes nothing
(tested). The MCP servers have no write tool and connect with `default_transaction_read_only=on`: a Cypher
`CREATE` through the graph server's connection fails with a read-only error even for `fleet_engineer` (tested).

## 5. One snapshot

- **Write-back.** A `fleet_reader` transaction at REPEATABLE READ counted the finding's graph edges, labels and
  episodes (0 / 0 / 0) and ran the joined search; a concurrent `fleet_engineer` transaction committed the finding;
  the first transaction read again: still 0 / 0 / 0, the same search result; a new transaction: 1 / 1 / 1. At READ
  COMMITTED the second read already saw 1 / 1 / 1.
- **New recording and its vertex.** At REPEATABLE READ a reader saw 2 recordings, 2 unit vertices and search hits
  in `ra`, `rb`. A concurrent transaction added a recording `rc` (a real snapshot, copied) and a vertex naming it,
  and committed. The reader's second look: 2, 2, `ra`, `rb`, in SQL, Cypher and search alike. After a new
  transaction: 3, 3, and `rc` among the hits.

Both stores are tables in one database, so one transaction snapshot covers both. Not tested: probe search (the
demo has no pgvector), which the search contract documents as running several statements; it needs REPEATABLE READ
for a single snapshot.
