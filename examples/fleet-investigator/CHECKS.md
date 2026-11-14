# Risk checks: AGE and pg_morlet in one database

Five checks of what the fleet investigator claims. They run as [checks.py](checks.py) against a freshly loaded
demo database; [reference/checks_output.txt](reference/checks_output.txt) is a complete run, and
[tests/test_fleet.py](tests/test_fleet.py) asserts the same functions on a disposable container. Setup: the demo
image (MorletDB 0.3.14-alpha.1 release image, PostgreSQL 17.6, pg_morlet SQL 0.3.11, Apache AGE 1.7.0 for PostgreSQL 17),
measured on 2026-11-14. Roles: `fleet_reader` (all sites, read), `fleet_contractor` (Site A only, read), `fleet_engineer` (all
sites, may record findings); the owner `morlet` is a superuser and bypasses RLS, so no check relies on it as a
reader.

| # | Check | Outcome |
|---|---|---|
| 1 | Both extensions in one database | **Holds**, with `shared_preload_libraries = 'age'` and `ag_catalog` in `search_path` |
| 2 | One statement: Cypher joined LATERAL with the waveform search | **Holds** |
| 3 | RLS on AGE label tables restricts `cypher()` for a non-owner role, both orders | **Holds for fixed-length patterns and direct SQL, in both orders, only with two measures.** In AGE 1.7.0, variable-length paths, `vertex_stats` and `graph_stats` **bypass RLS** with AGE's default grants; they are withheld from restricted roles |
| 4 | Atomic write-back to graph and MorletDB | **Holds** |
| 5 | One REPEATABLE READ snapshot over graph and recordings | **Holds** |

## 1. AGE and pg_morlet in one database

- `CREATE EXTENSION pg_morlet` and `CREATE EXTENSION age` in the same database: `pg_morlet 0.3.11`, `age 1.7.0`.
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
    ->  Nested Loop ... Seq Scan on "INSTALLED_IN" i_1  Filter: (fleet.graph_site_visible(properties) AND (... "from" <= ...) ...)
        ->  Materialize -> Seq Scan on "EquipmentModule" m_1
              Filter: (fleet.graph_site_visible(properties) AND (properties @> '{"equipment_id": "P-102.BRG-3"}'::agtype))
        ->  Seq Scan on "PhysicalAsset" a_1  Filter: fleet.graph_site_visible(properties)
  ->  Hash Right Join  (Hash Cond: equipment_id of the install = h.metadata->>'equipment_id'; Join Filter: start in [from, to))
        ->  Hash Left Join ... Seq Scan on "PhysicalAsset" / "INSTALLED_IN" / "EquipmentModule" / "OF_ASSET" /
            "Failure" / "CLASSIFIED_AS" / "FailureMode", each  Filter: fleet.graph_site_visible(properties)
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

**What AGE itself enforces.** AGE 1.7.0 for PG17 includes [apache/age#2309](https://github.com/apache/age/pull/2309)
(*Add RLS support and fix permission checks*): row-level security and table privileges for Cypher's own scans and
writes (`CREATE`, `SET`, `DELETE`, `MERGE`), and RLS checks in `startNode()`/`endNode()`. The demo started on AGE
1.6.0, which predates it; the table below compares both on the same fixture.

**Where AGE bypasses RLS** (measured as the contractor with AGE's default grants, EXECUTE to PUBLIC; the
`data_first` fixture, which has 7 vertices and 7 edges, of which the contractor may see 5 and 3):

| Read | AGE 1.6.0 | AGE 1.7.0 | With `fleet.restrict_age_functions` |
|---|---|---|---|
| `MATCH (:MaterialLot)-[*2]-(f:FailureMode) RETURN f.id` (variable-length path through the hidden Site B unit) | `FM` | `FM`: **still bypasses** | `permission denied for function age_vle` |
| `relationships(p)` on that path | the hidden Site B edges with their properties | the same: **still bypasses** | `permission denied for function age_vle` |
| `age_startnode` / `age_endnode` on a hand-written edge whose ends are the hidden unit's id | the hidden vertex with its properties | `access to vertex ... denied by row-level security policy on "Unit"`: **closed by 1.7.0** | `permission denied for function age_startnode` (kept, defence in depth) |
| `age_vertex_stats` of the hidden unit | its in- and out-degree | `in_degree 1, out_degree 2`: **still bypasses** | `permission denied for function age_vertex_stats` |
| `age_graph_stats` of the graph | not measured on 1.6.0 (same code) | `num_loaded_vertices 7, num_loaded_edges 7`: counts of hidden elements | `permission denied for function age_graph_stats` |

The fixed-length version of the variable-length question returns nothing to the contractor in every case.
Variable-length paths and both stats functions read AGE's global graph: a cached copy of the graph's vertices and
edges, loaded by a direct heap scan outside the query, so policies and table privileges never apply to it. AGE's
global-graph code is the same in 1.6.0 and 1.7.0 apart from a memory-allocation change, so `graph_stats`, which the
1.6.0 checks missed, very likely leaked the same counts there (not measured). The 1.6.0 column is the earlier run of
these checks on the 1.6.0 image. The fix is
[apache/age#2561](https://github.com/apache/age/pull/2561) (*Add RLS and SELECT privilege enforcement to global
graph*, merged 2026-09-29), with the follow-ups [apache/age#2592](https://github.com/apache/age/pull/2592) and
[apache/age#2593](https://github.com/apache/age/pull/2593) (merged 2026-10-12 and 2026-10-13): the global graph is
then loaded with SELECT privilege checks and, where RLS applies, through the planner (setting
`age.enforce_rls_in_traversal`, on by default). They are on AGE's
master branch only; checked on 2026-11-14, no PG17 release or the PG17 branch contains them (the latest PG17 release
is 1.7.0, 2026-02-11). **Variable-length paths stay withheld from restricted roles until a PG17 release includes
#2561**, and are then to be measured again before the restriction is lifted.

**Measures, and what then holds:**

1. `fleet.restrict_age_functions()` revokes EXECUTE on `age_vle`, `age_startnode`, `age_endnode`,
   `age_vertex_stats`, `age_graph_stats` and `age_delete_global_graphs` from PUBLIC. The contractor then gets
   `permission denied for function age_vle` (and likewise for the others); fixed-length patterns keep working. No
   non-owner role in this demo may use variable-length paths; the graph tools do not need them (each hop is a fixed
   pattern, and the ISA-95 path is walked one `CONTAINS` hop at a time). `startNode`/`endNode` stay revoked although
   1.7.0 checks RLS in them: nothing here needs them, and keeping them out costs nothing.
2. **New labels.** AGE (1.7.0 as 1.6.0) creates a table for each new label (`create_vlabel` or a `CREATE` with a
   new label) without firing event triggers: a `ddl_command_end` trigger recorded an ordinary `CREATE TABLE` (the control) but neither
   `create_vlabel` nor a Cypher `CREATE` of a new label, so no trigger can secure new labels. A new label table has no policy and no grant until `fleet.secure_graph` runs again. In the rules-first
   fixture, before re-securing, the contractor's `MATCH (u:Unit)` got `permission denied for table Unit` (fails
   closed), and an unlabeled `MATCH (n)` still returned only Site A and shared vertices: when a parent table is
   scanned, its own policy applies to the children's rows.
3. `fleet.graph_security_gaps(graph)` lists label tables without RLS or without the policy, login roles that may
   execute a restricted AGE function, and BYPASSRLS roles. The loader refuses to finish while it is not empty. Re-granting
   `age_vle` to PUBLIC (as an `ALTER EXTENSION age UPDATE` might) shows up there for every demo role; revoking
   clears it.

**On the demo data**, as the contractor: graph sites `*` and `A` only, 8 of 16 units, no Site B unit; 4,816 of 6,256
recordings, the same 4,816 in chunks, envelopes, segments and context; the LOT-0402 siblings at Site A only;
F-212's failure history not visible.

So: **one set of access rules holds as tested** for direct SQL on every table of both stores and for fixed-length
Cypher, in both enablement orders, given the two measures above. With AGE 1.7.0 it does not hold for variable-length paths or the
graph statistics functions, which this demo therefore withholds from non-owner roles. These are tested paths, not a proof.

## 4. Atomic write-back

[confirm_finding.py](confirm_finding.py) `write_finding`, as `fleet_engineer`, in one transaction:
`morlet_add_label` (label `finding:resembles` on the query interval, note = the evidence),
`morlet_add_episodes` (exempt episode, kept by compaction), then Cypher
`CREATE (a:PhysicalAsset)-[:RESEMBLES {finding_id, evidence, label_id, episode_id, ...}]->(f:Failure)`. The
properties are written in the `CREATE` itself: since AGE 1.7.0 a `SET` needs UPDATE on the label table
([apache/age#2309](https://github.com/apache/age/pull/2309)), and `fleet_engineer` has INSERT only on `RESEMBLES`.

| Case | Result | Graph edges / labels / episodes after |
|---|---|---|
| error injected after all three writes, before commit | rolled back | 0 / 0 / 0 |
| MorletDB refuses the episode (interval beyond the recording), after the label was written | rolled back: `episode interval 0..40960 is outside recording ... (0..20480)` | 0 / 0 / 0 |
| normal | written | 1 / 1 / 1 |
| identical retry | `exists`, nothing written | 1 / 1 / 1 |
| same finding id, different evidence | refused | unchanged |
| `fleet_engineer` runs `SET` on the recorded edge | `permission denied for table RESEMBLES` | unchanged |
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
