# PostgreSQL vector-search systems, extensibility and temporal types: literature to code

Compiled 2026-11-01 for paper draft 0.8 (PR #61). Sources were read critically, from full text where
accessible; notes with quotations and page references: [`notes_pg_systems.md`](notes_pg_systems.md).
A read-only audit of `main` at 68594d7 traced the corresponding code paths:
[`code_audit.md`](code_audit.md). Reported speed-ups in these papers use other data, dimensions,
metrics and systems; none is a prediction for MorletDB.

## Sources

| # | Source | Type | Read |
|---|---|---|---|
| 1 | Zhang et al., VBASE, OSDI 2023, 377–395 | peer-reviewed conference paper | full text |
| 2 | Liu et al., Fast Vector Search in PostgreSQL: A Decoupled Approach, CIDR 2026 | peer-reviewed short paper; experiments labelled preliminary | full text (its time breakdown is in the authors' ICDE 2024 paper, not read) |
| 3 | Lu et al., An In-Depth Study of Filter-Agnostic Vector Search on a PostgreSQL Database System, PACMMOD 4(3) Art. 134 (SIGMOD 2026), doi:10.1145/3802011 | peer-reviewed; arXiv:2603.23710v1 | full text (arXiv v1) |
| 4 | Kim et al., Anarchy in the Database, PVLDB 18(6):1962–1976, 2025 | peer-reviewed | full text |
| 5 | Zimányi et al., MEOS, EDBT 2024, 810–813 | demonstration | full text |
| 6 | Sakr et al., User-centered road network traffic analysis with MobilityDB, Trans. GIS 27(2):323–346, 2023 (online 2022) | peer-reviewed journal | full text |
| S1 | Zhang et al., Simple Adaptive Query Processing vs. Learned Query Optimizers, VLDB J. 34(5), 2025 | peer-reviewed | abstract only |
| S2 | Wu et al., Tuning the Lookahead Distance for PostgreSQL Asynchronous IO, PVLDB 19(12), 2026 | peer-reviewed | partial |
| S3 | Wang and Liu, A Sneak Peek at RisingWave, DEBS 2022, 190–193 | short/demo paper | partial |
| S4 | Hoang et al., MobilityDuck, EDBT/ICDT 2026 workshops | workshop paper | partial |

## Matrix: source → mechanism → assumptions → overlap → difference → evidence or action

| Source | Mechanism | Assumptions | Overlap with MorletDB | Relevant difference | Evidence or action |
|---|---|---|---|---|---|
| VBASE | vector index as a resumable Volcano iterator; stops when K results are collected *and* the traversal has moved away (relaxed monotonicity) | deterministic traversal; filters that commute with sort; one scalar distance per row; recall delegated to the index | filtered ANN inside PostgreSQL; choice of a candidate budget when filters remove rows | our score is a maximum over 61 lags of which 13 are probed, and greedy overlap suppression does not commute with sorting, so VBASE's equivalence argument does not transfer; pgvector's iterative scan already resumes within one probe statement | measure whether the fixed budget starves results (E1 short-result rate, D1 survivor counts); a resumable or coordinated budget only if a measured problem appears |
| PostgreSQL-V | index contents outside PostgreSQL pages (native libraries behind the index AM); unindexed in-memory recent rows; WAL only for an ID map | crash recovery as the consistency target; replication not discussed; filtering as in pgvector | pays the same page-indirection and insert-time HNSW maintenance costs we measure (WAL amplification) | gives up WAL-logged index pages and synchronous index visibility, which our search-at-commit contract relies on | none for the paper beyond positioning; a decoupled index is out of scope |
| Filter study | filter-agnostic vector search in a PostgreSQL-compatible engine; varies selectivity and **predicate–vector correlation**; CPU-cycle breakdowns | filters are in-memory bitmaps; timing covers the index scan only; commercial engine, not stock PostgreSQL | pgvector HNSW with iterative scan; selective filters; halfvec | our filters are SQL predicates on recordings; our router uses selectivity only | D2: equal-selectivity filters with positive, neutral and negative correlation to the query neighbourhood (synthetic, labelled) |
| Anarchy | static analysis and pairwise install tests of 441 PostgreSQL 16 extensions; failures concentrate in hook-heavy extensions and copied internals | compatibility = install + own tests + pgbench | MorletDB depends on pgvector types, operators and settings | MorletDB uses no hooks and copies no internals (audit §5); no pgvector-specific failure is reported | none needed beyond stating dependencies: PostgreSQL 17, pgvector ≥ 0.8 (doc said 0.7: fixed) |
| MEOS / MobilityDB | temporal types with explicit inclusive/exclusive bounds and interpolation; the same types as a standalone library | continuous time with interpolation; "lossless" relative to the interpolation model | domain object (a recording) instead of rows; intervals; library/database split | MorletDB uses integer sample offsets, half-open intervals, and bit-exact coding; no interpolation | acknowledge precedent for domain sequence types; the scorer and representation exist once in Rust (`morlet-core`) but are re-implemented in Python for evaluation (audit §4) |
| S1 adaptive QP | run-time switching between plans | abstract only | our router is a static plan choice | — | none |
| S2 PostgreSQL AIO | read-stream lookahead tuning in PostgreSQL 18 | PostgreSQL 18 | exact path is a parallel sequential scan | we use PostgreSQL 17 (no AIO), warm caches; the tuning is a research proposal | state the exact baseline's version, cache regime and memory settings (E1 records them) |
| S3, S4 | streaming materialized views; MEOS in DuckDB | — | weak | — | none |

## Code-audit follow-ups (fix branch `fix/search-audit`, separate from the paper)

| Finding (audit) | Confirmed how | Change |
|---|---|---|
| B3: `morlet_enable_row_security` matched other collections' probe tables (`_` as LIKE wildcard) | code reading; regression test | exact prefix and numeric suffix; test `row_security_matches_only_its_own_probe_tables` |
| B2: a window committed between the router's count and the exact scan raised "exceeds candidates" | code reading | router passes no lower cap to the exact path; contract documents it |
| B1: enabling probes while envelopes are built left envelopes without vector rows | code reading; two-session test | builder re-checks for the probe table after each insert; `tests/probe_enable_race.sh` |
| B5: one probe test compared exact with exact | code reading | test forces the probe path (`exact_threshold` 0) |
| B4: build return value can over-count under concurrent builders | code reading | documented only (no effect on stored data) |
| docs: "13 probes" unconditional; "same statement"; pgvector 0.7 | code reading | corrected; contract section on probe candidates, coverage and snapshots added |
