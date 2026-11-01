# Clean artifact check (2026-11-01)

A bounded check that the artifact can be rebuilt and gives the published results, following
`research/paper/ARTIFACT.md`. No new benchmark: no latency is compared, and no full E1 rerun was made.

| Step | What was done | Result |
|---|---|---|
| Fresh clone | `git clone https://github.com/merkhet-io/morletdb.git` into an empty directory, `git checkout 68594d7` | ok |
| Build | `cargo pgrx install --release` (Rust 1.91.1, cargo-pgrx 0.16.1) into the existing PostgreSQL 17.11, clean target directory | 54 s; library SHA-256 `a6f7bce8…`, **not** the measured `3c786684…`: builds are not bit-identical across checkout paths. Behaviour was checked instead (below) |
| Functional check | `smoke_check.sql` in a new empty database | PASS: 45 envelopes, backlog 0, probe result = exact result, best match the planted burst, interval read of 4,096 samples |
| Data | `shasum -a 256 -c` against the committed sums, for both datasets already on disk (no new download) | development 56/56 OK (sums newly committed from the fetch manifest: `evaluation/retrieval_scaling/results/SHA256SUMS.mseed`), held-out 56/56 OK |
| Database build | `prepare_db.sh heldout morlet_artifact_check data/cx-confirm-2021` (load, envelopes, `morlet_enable_envelope_probes`) | 10 min 54 s; 234,622 envelopes, 234,622 probe rows, backlog 0 |
| E1 smoke on the new database | `e1.py … smoke` with the fresh build (3 queries, no index rebuild) | plans as in E1 (HNSW index scan; 13 probe index scans; parallel exact scan); per-arm recall on the 3 queries equal to the stored values for all 9 arms |
| Lists on the new database | exact and probe search for 5 held-out queries (3 random, 2 high-energy), compared with E1's stored lists | exact identical 5/5; probe identical 5/5 (this rebuilt HNSW graph returned the same top tens for these queries; not guaranteed in general) |
| Re-search on a preserved index | `repeat_check.py` on a template copy of the development database | exact = E1 for 100/100 queries; probe = D1 (same index) 100/100; probe = E1 (index before E1's rebuild) 100/100 |
| Tables from stored results | `research/paper/gen_numbers.py --check`, `e1_distribution.py`, `figures.py` | regenerated files identical to the committed ones |

All test databases (`morlet_smoke`, `morlet_r2_check`, `morlet_artifact_check`) were created for the check and
dropped afterwards; the evaluation databases were not modified.

**Blockers found and fixed:** the product probe tables of the evaluation databases had been created by a
manual `morlet_enable_envelope_probes` call that no committed script contained (now `prepare_db.sh`); the
development data's checksums were only in an uncommitted manifest (now committed); per-structure sizes came
from an ad-hoc query (now `sizes.sql`, outputs in `results/sizes_*.csv`).

**Not checked here:** a fresh `cargo pgrx init` of PostgreSQL and pgvector (the existing pgrx PostgreSQL 17.11
with pgvector 0.8.0 was used); a fetch of the data from GEOFON (the files on disk were verified against the
committed checksums instead); the development database rebuild (only the held-out one was rebuilt); latency.
