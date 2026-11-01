> Read-only audit of `main` at 68594d7 (2026-11-01). Line references: S = extensions/pg_morlet/sql/streams.sql, L = extensions/pg_morlet/src/lib.rs, E = crates/morlet-core/src/envelope.rs, at that commit. Bugs marked as predicted were found by reading, not by running; see CODE_AUDIT follow-ups in PG_SYSTEMS.md for what was confirmed and fixed.

# pg_morlet search/ingestion audit (read-only)

Tree: `/Users/zin/sophia/morletdb-e1` at `68594d7`. All paths below are relative to it. Nothing
was built, run or connected to. "Predicted" means derived from code reading, not executed.
Short names: `S` = `extensions/pg_morlet/sql/streams.sql`, `L` = `extensions/pg_morlet/src/lib.rs`,
`E` = `crates/morlet-core/src/envelope.rs`.

Classes: **(1)** implemented and tested; **(2)** implemented but not evaluated or documented
enough; **(3)** missing and needed for the paper's present claim; **(4)** optional future work.

---

## 1. Trace

### 1.1 Probe search (`morlet_search_envelope_probes`, S:734-818)
| Step | Code | Notes |
|---|---|---|
| Volatility | `VOLATILE` (S:739) | Needed for `set_config`. Under READ COMMITTED, **every statement takes its own snapshot** (see 3.4). The exact path is `STABLE` (S:597, S:668), so it runs in one snapshot. |
| Source read | `morlet_read_interval` (S:751). Fails if the interval is incomplete or hidden (S:274-276). | |
| Eligible recordings | `array_agg(recording_id)` with rate, channel and unit equality plus `metadata @> filter` (S:758-760). RLS on the recordings table applies. | |
| Routing count | Counts **envelope** rows (not probe rows) for eligible ids and the window size, stopping at `exact_threshold+1` (S:761-762) | If ≤ threshold: `morlet_envelope_ranked` with `max_candidates = greatest(exact_threshold,1)` (S:763-767). |
| Lags | `generate_series(-L, L, probe_step)` with `L` appended if it was missed (S:770-771). `L = floor(0.075·⌈n/10⌉)`: 30 at 4096, so 13 probes; 15 at 2048, so 7 probes. | docs/search-contract.md:13 says "13" without qualification. **(2)** |
| Per-probe SQL | `SELECT … FROM <probes> WHERE recording_id = ANY(eligible) ORDER BY v <#> probe LIMIT per_probe` (S:779-781). The probe is `morlet_envelope_probe(q,l)` (L:320-324, E:156-170). | Source exclusion is **not** applied here, only later. |
| ef_search / iterative | `hnsw.ef_search = max(per_probe,100)`, `hnsw.iterative_scan = relaxed_order`, both transaction-local and restored afterwards (S:775-787) | Relies on pgvector ≥0.8 iterative scan to keep traversing the graph until `LIMIT` is met under the `ANY` filter. `hnsw.max_scan_tuples` (pgvector default 20,000) and `scan_mem_multiplier` are **not** set, so a selective filter can return fewer than `per_probe` rows per probe. **(2)** |
| Union and dedup | Arrays are concatenated across probes (S:782-784). `SELECT DISTINCT` happens in the re-rank (S:790). At most `P·per_probe` (650 at defaults). | |
| Re-rank | The exact scorer `morlet_envelope_distance(q, e.envelope, L)` runs on the union joined to the envelopes and recordings tables. It re-applies eligibility and applies source exclusion (S:788-797). | |
| Order and ties | `ORDER BY distance, recording_id, start_sample` (S:797). This is the same as the exact path (S:627). | |
| Overlap suppression | Greedy, in rank order, global over the result, before `top_k` (S:801-815). | **No refill**: the loop ends when the candidates run out (see Q-A). |
| RLS | `SECURITY INVOKER` throughout. The probe table gets policy `morlet_follows_recording` from `morlet_enable_row_security` (S:451-479), and the enable functions re-apply it to tables created later (S:500, S:724). | (1): `row_security_covers_probe_tables` L:769-797 (uses `exact_threshold=0`, so the probe path runs), `row_security_covers_companion_tables` L:723-759, `row_security_rejects_writes_for_hidden_recordings` L:762-766 |

### 1.2 Exact search (`morlet_envelope_ranked`, S:592-650)
- Candidates are counted with the same eligibility. If the count exceeds `max_candidates`, the search raises an error (S:611-616). Source exclusion is not counted.
- One parallel-capable statement takes the best `m` rows into arrays: `m = max(200, 20·top_k)` with suppression, otherwise `top_k` (S:617-631).
- Suppression walks those rows. If fewer than `top_k` rows survive while `n = m` (more rows remain), the statement is repeated with `4m` (S:632-647). The result therefore equals a full ordered scan. **(1)** `envelope_search_refetches_after_suppression` L:995-1035 compares it against a cursor-loop reference at stride 64.
- Ties: (1) `search_contract_ties_order_by_recording_then_start` L:654-673.
- Budget error: (1) `envelope_search_enforces_its_budget` L:1079-1082.

### 1.3 Appends, representation build, probe rows, visibility
- **Append** (`morlet_append_chunk`, S:66-105): takes `FOR UPDATE` on the recording row (S:76-80), checks replay identity (S:85-88), and runs `morlet_chunk_step`. The core rejects any start other than the current end (`crates/morlet-core/src/chunks.rs:185`), so **raw data is always a contiguous prefix**. The chunk, segments and checkpoint are written in one function call. (1) `encoded_chunks_reject_conflicting_replay` L:532, `batch_append_is_atomic_and_replay_safe` L:1394.
- **Build** (`morlet_build_envelopes`, S:519-589): resumes at `(max(start_sample)/stride+1)·stride` for this window size (S:540-542). It writes batches of ≤256 windows with `INSERT … ON CONFLICT DO NOTHING` (S:546-554). When the probe table exists, probe rows go in **a separate statement in the same transaction** (S:555-559, S:569-573). docs/search-contract.md:65 says "same statement" **(2, doc wording)**. The stride is not stored anywhere (S:372-375 comment).
- **Enable probes** (S:695-726): creates the table with an FK to the envelopes table and `ON DELETE CASCADE` (S:712-719), backfills (S:721-722), builds HNSW m=16/ef_construction=64 (S:723), then applies RLS (S:724). (1) `probe_search_is_maintained_and_exact_on_small_galleries` L:945-993 (backfill count, maintenance and cascade parts).
- **Index visibility**: HNSW entries are inserted with the heap row, so they are visible at commit. A window is searchable at the commit of the transaction that ran the build. (1) `search_contract_fewer_than_top_k_and_searchable_at_commit` L:676-694. This test runs within one transaction only; visibility across sessions follows from MVCC and is not tested.

---

## 2. Question A: shortlist exhaustion

**Exact path.** Only (a) can happen: fewer rows come back only when the admissible, non-overlapping windows run out. The refetch loop (S:647-648) rules out (b) and (c). Tests: `search_contract_fewer_than_top_k_and_searchable_at_commit` (L:676-694, n ≤ 8 < 20) and `envelope_search_refetches_after_suppression` (L:1033-1034, < 30). **(1)**

**Probe path.** All three cases are possible. There is **no expansion or retry** (S:799-815): the loop simply ends when the cursor is exhausted. pgvector's iterative scan resumes graph traversal *within one probe statement* (S:778). Our code relies on it for the eligibility filter only; it does **not** re-query with a larger `per_probe` when suppression or exclusion leaves fewer than `top_k`.

The following consume the fixed budget (≤ P·per_probe):
1. **Source self-matches.** The index query does not exclude the source (S:780-781). The source window and every grid window overlapping it score near 0, so they fill each probe's first slots, and are removed only at re-rank (S:794). Per probe, that is ⌈2·window/stride⌉−1 windows: 3 at the evaluated 4096/2048, but **63 at stride 64**.
2. **Near-duplicate overlapping neighbours of each true match.** About window/stride of them per match. They survive dedup (each is a distinct row) and are then suppressed.
3. **Correlated probes.** Neighbouring lags return mostly the same windows, so the distinct union is much smaller than P·per_probe.
4. **Selective filters.** With `max_scan_tuples` unset, a probe can return fewer than `per_probe` rows.

Cases:
- **(a) fewer because fewer admissible windows exist.** Possible on both paths.
- **(b) fewer because the budget ran out.** Possible on the probe path only. Predicted minimal repro: run `envelope_fixture()` (L:700-704), build at `('env',r,2048,64)` as in L:999-1001, `CREATE EXTENSION vector`, then `morlet_enable_envelope_probes('env',2048)` and `morlet_search_envelope_probes('env','A',2048,4096,'{}',10,0.075,true,true,50,5,0)`. The source's 63 overlapping windows and the near-duplicates around B's burst should take most of the 7×50 slots, so fewer than 10 rows should come back. The exact path returns 10 (L:1033 shows ≥ 10 admissible). At top_k = 100 and per_probe = 50 it is likely even at stride = window/2.
- **(c) top_k returned but better windows missed.** This is inherent to the approximation. One missed candidate can also change suppression: a missed W0 would have suppressed W1, and W1 in turn suppresses W2. So a single miss can change several rows. Recall@10 measures this (product check counts short results as misses: `evaluation/search_storage/product_check_routing.py:49`), but **short results are not reported separately**.

The documentation admits (b) (docs/search-contract.md:54-55). **Tests: none on the probe path.** `probe_search_routes_and_respects_filters` (L:885-930) uses per_probe 100 on a 45-window gallery, so it can never exhaust.

Classification:
- Probe-path (b) behaviour: **(2)**. It is documented, but untested and unmeasured.
- A refill/retry loop, or excluding the source in the index query: **(4)**, unless the paper claims "top_k results" from the probe path. In that case it is **(3)**.
- Reporting the frequency of short results in the evaluations: **(3)** if the paper states recall@k without it.

---

## 3. Question D: completeness during ingestion

**3.1 Three kinds of progress.** Raw progress is the checkpoint, representation progress is `max(start_sample)` per window, and index visibility is the probe rows (docs/search-contract.md:57-66; docs/adr-envelope-maintenance.md:49-51). In synchronous mode (append and build in one transaction) they are equal at every commit. **(1)** for envelopes (L:676-694); the probe-row part is (1) through L:965-967 (counts equal after build).

**3.2 Contiguous prefix or gaps?**
- Raw data: always a prefix (chunks.rs:185).
- Envelopes built by `morlet_build_envelopes` alone: a prefix on its grid. Two conditions apply. The stride must stay the same between calls: the stride is not stored, and a different stride re-grids from `last` (S:542). And nobody may write beyond the prefix.
- **A writer that leaves a hole beyond the prefix creates a permanent gap.** Examples: an external worker writing a later range, a worker split by range rather than by recording, or a partial `DELETE` of early windows. The builder resumes after `max(start_sample)` (S:540-542), and `ON CONFLICT DO NOTHING` never revisits the hole. The CW1 worker uses the same `max()` rule (`evaluation/external_worker/cw1.py:55`).
- **Status cannot see gaps.** `unbuilt_samples` is computed from `max(start_sample)+window` (S:399-403). The ADR's "completeness is checked by counting against the checkpoint" (docs/adr-envelope-maintenance.md:52-53) is not implemented, and cannot be without the stride. **(2)**. A gap or count audit would be **(4)**.

**3.3 Coverage reporting.** `morlet_collection_status` (S:377-421) reports `envelopes`, `recordings_without_envelopes`, `unbuilt_samples` (includes partial tails) and `probe_rows`. `probe_backlog = envelopes − probe_rows` (S:410) is exact, because the FK makes probe rows a subset of envelopes per window size. Tested only at backlog 0: `collection_status_reports_progress_and_backlog` L:800-839. **(1)** for the columns, **(2)** for a nonzero backlog. Pilot B4 observed a backlog of 12,715 (evaluation/pilot/RESULTS.md:21), but no test exists.

**3.4 Do the two paths search the same universe? No.**
- Exact path: the envelope table (S:620-623).
- Probe path: probe-table rows only (S:779-781); envelopes are used only to re-rank them (S:791).
- Routing counts envelope rows (S:761). So a window with an envelope but no probe row is visible to exact search, and to probe search when it routes to exact (≤ threshold). It is **invisible to the probe path above the threshold**.
- Neither the function nor the contract says this. operations.md:48-50 mentions the backlog but not its effect on search. **(2)**. It is **(3)** if the paper claims probe search covers "every window searchable at commit" in worker mode. In synchronous mode the claim holds.

**3.5 Snapshot consistency.** The probe function is VOLATILE (S:739), so the eligible list (S:758), the count (S:761), each of the P index scans (S:779) and the re-rank (S:788) can each see a different committed state under READ COMMITTED. A result can therefore mix states of a concurrently ingesting collection. The exact path is single-snapshot (STABLE). Not documented. **(2)**. See also bug B2.

**3.6 Rebuilds, deletions, retries.**
- Deleting a recording cascades to chunks, segments and envelopes (S:15, S:21, S:496), and from envelopes to probe rows (S:718).
  - (1) envelope→probe: L:986-992.
  - Recording→all: pilot B5 only (evaluation/pilot/RESULTS.md:22). No pg_test. **(2)**
- `REINDEX INDEX CONCURRENTLY`: operational only (docs/operations.md:51-53; pilot B6). **(2)**
- Enabling probes on existing data: one transaction (S:712-724). An abort leaves no table (pilot B3). Enabling **concurrently with ingestion leaves a permanent backlog**: see bug B1.
- No function refills a probe backlog. operations.md:49-50 says to run "the same statement" by hand. **(2)/(4)**
- Worker retries: idempotent through the PK and `ON CONFLICT DO NOTHING`. A wrong envelope from a worker is kept silently (docs/access-control.md:54-66). (1) in the pilot (B4) and CW1, not in pg_test.

**3.7 What must share a transaction.**
- The chunk, segments and checkpoint: they already do, in one function.
- An envelope and its probe row: one build call. A worker that writes envelopes must also write the probe rows, or a backlog appears.
- Append and build, if "searchable at commit" is wanted.
- Enabling probes: create, backfill and index, already one function.

---

## 4. Recording abstraction
- **Half-open intervals.**
  - Reads: S:240-241 and S:250 (`interval_end<=interval_start` is rejected).
  - Search rows: `end_sample = start_sample + window_size` (S:620, S:789).
  - Time: `morlet_time_to_samples` is half-open, computed in numeric (S:1313-1328).
  - (1) `time_to_samples_uses_exact_boundaries` L:1232-1250.
  - `morlet_sample_time_ns` (S:1330-1333).
- **Rate.** Eligibility requires exact float8 equality of `sample_rate_hz` (S:610). There is no resampling. **(2)**: document it. Resampling would be **(4)**.
- **Representation versioning.**
  - Envelope rows carry version byte 1, enforced by a CHECK (S:499) and by `decode` (E:89).
  - `morlet_envelope_version()` returns `xc-envelope-f16-v1` (L:240-243, E:17). (1) `envelope_functions_match_the_core_definition` L:842-852.
  - Not stored: the version string per collection, and the stride. The probe table records only the dimension (S:716).
  - A v2 would need a schema change. The CHECK would at least reject v2 bytes. **(2)**
  - Feature segments record `extractor_version` in the checkpoint and reject mixed populations. (1) `search_rejects_mixed_extractor_population` L:1384.
- **Stable references.** `(recording_id, start_sample, end_sample)` together with immutable chunks, since conflicting replays are rejected (S:85-88). A recording can be deleted and re-created under the same id; there is no generation or tombstone. **(4)**
- **Labels and context.** Labels and context are half-open intervals (S:849-860, S:825-838). `morlet_history_neighbors` returns labels (S:1515-1556). Envelope search returns only `metadata` and does not join labels. **(4)**
- **Duplicated logic in Python, and equivalence checks:**

| Python | Mirrors | Equivalence check |
|---|---|---|
| `evaluation/decision/timing.py:268` `envelope` | `E:27-67` | Rust test `matches_the_evaluated_python_definition` (E:394-427) against the **frozen** `testdata/xc-envelope-f16-v1/fixtures.json`. Nothing regenerates the fixture from Python in CI (.github/workflows/rust.yml:20 runs only `evaluation/**/test_*.py`). Drift in Python is not detected. **(2)** |
| `timing.py:326` `xcorr_distances` (FFT) | `E:142-150` | no automated check **(2)** |
| `evaluation/bounded_lag/probes.py:12,26,34,38,44` shifted, lagged_sum, distance, zscore, decode | `E:156,180,142,69,88` | `test_bl1.py` checks Python only against Python. Rust↔Rust: `envelope_probe_is_the_lag_term` L:863-882, E:447. Python↔Rust: none in CI. ss1.py `distance_identity` (`evaluation/search_storage/ss1.py:79`) checks against a live DB only. **(2)** |
| `probes.py:75` `suppress` | S:592-650 / S:799-815 | Hand-written expectations (test_bl1.py, Ordering). It assumes the query length equals WINDOW, which is equivalent because S:754 sets width to the query length. **(2)** |
| `evaluation/plain_postgres/halfvec_bench.py:38`, `evaluation/materialized/compact/compact.py:34`, `evaluation/pruning/feasibility.py:30`, `evaluation/compression/reference/feasibility.py:77`, `evaluation/decision/bearings.py:167` | envelope or lag scorer variants | none (benchmark-local) |

- Cross-platform byte reproducibility of envelopes: the FFT uses `sin_cos` from the platform libm (E:237). The pilot (B4) and CW1 showed byte identity only on one machine. **(4)**

## 5. Extension composition
- **pgvector is optional.** There is no `requires` in `extensions/pg_morlet/pg_morlet.control`. The schema is looked up through `pg_extension.extnamespace` (S:534, S:702, S:756), objects are created with dynamic SQL (S:712-723), and a missing pgvector raises an error (S:704-706).
  - CI covers both cases: the `postgres` job without pgvector and the `postgres-pgvector` job (pgvector **0.8.0**) (.github/workflows/rust.yml:130-182). The skip comments at L:887 and L:947 ("CI does not install it") are stale. **(1)**
  - **Docs say "pgvector 0.7 or later"** (docs/operations.md:29, docs/durable-ingestion.md:308). `hnsw.iterative_scan` exists only from 0.8. With 0.7, S:778 either fails on pgvector's reserved `hnsw.` prefix, or filtered probes silently lose iterative scan. Untested. **(3)**: fix the docs to "0.8 or later" or add a version check.
  - pgvector installed in a non-`public` schema: untested. **(2)**
  - `DROP EXTENSION vector CASCADE` would drop the probe column. **(4)**
- **PostgreSQL hooks:** none. A grep for hook, shmem, ProcessUtility, ExecutorStart, planner_hook, `_PG_init` and `pg_sys::` finds nothing in `extensions/pg_morlet/src` or `crates/morlet-core/src`. No `_PG_init`, no copied PostgreSQL internals; only `pg_module_magic!` (L:4). All SQL comes from `extension_sql_file!` (L:428-429).
- **Supported versions:** PG17 only. Cargo features are `default=["pg17"]` (extensions/pg_morlet/Cargo.toml:17-19), the CI matrix is pg17 only (rust.yml:69,72), and docs/development.md:9 and extensions/pg_morlet/README.md:12 agree. pgrx 0.16.1. **(2)** state it in the paper. More versions: **(4)**
- **Install order:** pg_morlet first; `vector` at any time before `morlet_enable_envelope_probes`. The control file sets `superuser = true`, `relocatable = false`, `schema = public`.
- **Other extensions in the pilot:** only `vector` (evaluation/pilot/load.py:62-63; recovery.py:58-59). No pg_cron or `shared_preload_libraries` usage anywhere (grep).
- **Tests:**
  - pgrx `#[pg_test]` functions with assertions (L:431 onward); `sql/interval_tests.sql`, run via `include_str!` (L:438).
  - **No pg_regress expected-output files.** `sql/smoke.sql` is manual (extensions/pg_morlet/README.md:37).
  - Upgrade tests compare `md5(pg_get_functiondef)` catalogues against a fresh install (extensions/pg_morlet/tests/upgrade.sh:45-48, 151-153). The upgrade script's bodies of the probe, ranked, build, status and RLS functions are identical to S (checked by diff). **(1)**

## 6. Exact-baseline measurement environment
- **Documented:**
  - Apple M5 Max, 18 cores, 128 GB, macOS, PostgreSQL 17.11 (pgrx build), pgvector 0.8.0, warm caches, one client over localhost (evaluation/search_storage/INVENTORY.md:5-6; evaluation/pilot/STAGE2_PLAN.md:21-28).
  - Cold cache not measured (INVENTORY.md:52-53; STAGE2_PLAN.md:28).
  - Background load is recorded in the pilot (`load_avg` in evaluation/pilot/results/c2.json, c3.json; evaluation/pilot/load.py:214).
- **Parallel workers are stated inconsistently across documents:**
  - "2 workers" (docs/durable-ingestion.md:302-303);
  - "3 workers" (INVENTORY.md:28);
  - `max_parallel_workers_per_gather = 8` in SS1's exact arm (evaluation/search_storage/ss1.py:120);
  - "default (2) or higher" (docs/operations.md:39);
  - access-control timings (docs/access-control.md:70-77) and the pilot do not say.
- **Not recorded** for the BL1, SS1, SS3, product-check or pilot measurements:
  - `shared_buffers`, `work_mem`, `effective_io_concurrency`, `io_method`, `jit`.
  - The result JSONs have no settings key (keys listed from evaluation/search_storage/results/*.json and evaluation/pilot/results/*.json).
  - Only the older feature-path harnesses record settings (evaluation/scale.py:41-42, benchmarks/run.py:89-90). The 2025 materialized evaluation states `shared_buffers` 128 MB (evaluation/materialized/results/eval-2025/RESULTS.md:15).
  - Likely consequence: with a 4.8 GB database and the pgrx default `shared_buffers`, "warm" means the OS page cache, not shared buffers.
- **Exact-path numbers also drift across documents:** 2,834 ms (PL/pgSQL, before #64), 698 ms (harness, 3 workers), 946 ms (product, access-control.md:75), 9.7 s at about 960 k windows (operations.md:14).
- **Classification:** **(3)** for the paper. It must state PG version, parallel-worker setting, `shared_buffers`/`work_mem`, cache regime and machine load for every exact-versus-probe latency it quotes, and pick one exact-baseline configuration. Re-measuring cold caches: **(4)**.

---

## 7. Correctness bugs

**B1. Enabling probes concurrently with ingestion leaves a permanent probe backlog.**
- Mechanism:
  1. `morlet_build_envelopes` looks up the probe table once, before its inserts (S:532).
  2. `morlet_enable_envelope_probes` creates the table with an FK to the envelopes table (S:712-719). That takes SHARE ROW EXCLUSIVE on the envelopes table until commit, which can take minutes (5.1 min on 235 k windows, docs/durable-ingestion.md:367-368).
  3. A builder that starts during that window sees no probe table (not yet committed), so `probes = NULL`. It then blocks on its envelope `INSERT`.
  4. After the enable commits, the builder writes envelopes **without** probe rows. Later builds resume after them (S:540-542), so the rows are never filled.
- Repro (predicted): session 1 runs `BEGIN; SELECT morlet_enable_envelope_probes('c',4096);` and does not commit. Session 2 runs `SELECT morlet_build_envelopes('c','r',4096,2048)` after appending, and blocks. Session 1 commits. Session 2 completes. Then `morlet_collection_status('c')` shows `probe_backlog > 0`, and those windows are missing from probe results above the threshold.
- Not covered by the pilot: enabling ran after the fill (evaluation/pilot/load.py:69-77).
- Fix: re-check the probe relation after the envelope insert, or have the enable function lock the recordings table or fill rows written after its snapshot.

**B2. The routing race turns a valid probe search into an error.**
- The routing count (S:761) and `morlet_envelope_ranked`'s own count (S:611-616) run in different snapshots, because the outer function is VOLATILE.
- If concurrent builds commit between them and push the eligible count from ≤ `exact_threshold` to above it, the search raises `envelope search exceeds N candidates`. That error comes from S:615, with `max_candidates = exact_threshold` (S:765).
- Repro (predicted): eligible windows = exactly 5000, and a concurrent session commits one more window between the two statements. It is a narrow window, but under continuous ingestion near the threshold it will happen.
- Fix: pass a larger `max_candidates` to the exact path, or count once.

**B3. `morlet_enable_row_security` uses an unescaped LIKE pattern on the collection name.**
- `cl.relname LIKE c||'\_probes\_%'` (S:458) treats `_` and `%` in the collection name as wildcards.
- Repro (predicted):
  1. Create collections `line_1` and `line11`; enable envelopes and probes (2048) on `line11`.
  2. `SELECT morlet_enable_row_security('line_1')` matches `line11_probes_2048`. It drops any existing `morlet_follows_recording` policy there and installs one tied to `line_1`'s recordings (S:473-475).
  3. Non-owner probe searches on `line11` then return nothing, or rows governed by the wrong collection's visibility. If `line11` had its own RLS, its probe-table isolation is replaced by `line_1`'s.
- Fix: compare names exactly with `left(relname, …)`/`substr` plus a regex on the `_probes_<digits>` suffix, or escape `c`.

**B4 (minor). `morlet_build_envelopes` over-reports its return value.**
- It adds `n` per batch even when `ON CONFLICT DO NOTHING` skipped rows (S:560, S:575). The documented "returns the number of rows written; 0 means up to date" (S:516-518) is then wrong under concurrent builders. Only effect: a worker loop runs one extra iteration.

**B5 (test defect, not code).** `probe_search_is_maintained_and_exact_on_small_galleries` asserts "probe search must equal the exact search" (L:970-976) and the top-1 result (L:977). It uses the default `exact_threshold = 5000` with 45 windows, so both calls **route to the exact path** and compare exact with exact. The probe path is exercised only by `probe_search_routes_and_respects_filters` (threshold 0, L:911) and `row_security_covers_probe_tables` (L:796).

**Doc inaccuracies (2):**
- docs/search-contract.md:13 ("13 probes", true only at 4096 windows).
- docs/search-contract.md:65 ("same statement").
- INVENTORY.md:47-50 is stale: it says probe filtering happens after probing and probe RLS is untested; both have been fixed or tested since.
- L:887 and L:947 have stale CI comments.
