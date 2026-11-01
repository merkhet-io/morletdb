# PostgreSQL compatibility

pg_morlet is a pgrx 0.16.1 extension. One build targets one PostgreSQL major version
(the Cargo feature `pg17` or `pg18`; never `--all-features`).

## Support table

| PostgreSQL | Build checked | Functional tests checked | pgvector mode | Known limits |
|---|---|---|---|---|
| **17** (17.11), reference | yes, CI | yes, CI: all `#[pg_test]`s without and with pgvector, probe-enable race test, upgrades from v0.1.0-alpha.3, v0.2.0-alpha.1 and v0.3.0-alpha.1, seismic example | without; with pgvector 0.8.0 | release images and packages are built for 17 only |
| **18** (18.6) | yes, CI | yes, CI: all `#[pg_test]`s without and with pgvector, probe-enable race test | without; with pgvector 0.8.7 | no upgrade path tested (earlier releases were never built for 18; install fresh), no release image, no measurements |
| 13–16 | no | no | — | not supported; pgrx 0.16.1 could build them, but no user need is documented |

A version is listed as supported only while its CI jobs pass (`compat` in
[`.github/workflows/rust.yml`](../.github/workflows/rust.yml)). An experimental entry would set
`experimental: true` (the job then runs with `continue-on-error` and does not block the required
`postgres` check); none is experimental now.

What each column means:

- **Build checked:** the extension compiles against that major version and installs.
- **Functional tests checked:** the tests listed run in CI on Linux (ubuntu-24.04, x86_64) and pass.
- **Scientifically benchmarked:** only PostgreSQL 17. All paper and evaluation results refer to the
  frozen PostgreSQL 17 build (commit 68594d7) with pgvector 0.8.0. Compatibility tests are not a
  performance evaluation and do not replace one; supporting another PostgreSQL version claims
  correctness of the tested contract there, not equal performance, and needs no new benchmark.

pg_morlet does not depend on pgvector. Without it, everything except probe search works;
`morlet_enable_envelope_probes` raises `probe search needs the pgvector extension (CREATE EXTENSION
vector)`. Probe search needs pgvector 0.8 or later; for PostgreSQL 18, 0.8.1 or later (the first
release supporting 18).

## What the matrix runs

`compat` runs one job per PostgreSQL major and pgvector mode (four jobs), each calling
`extensions/pg_morlet/tests/ci_local.sh <major> <mode>`:

| Mode | Checks |
|---|---|
| `without-pgvector` | fails unless pgvector is absent; runs every `#[pg_test]`. The probe tests that need pgvector return early (they pass without asserting); `probe_search_requires_the_vector_extension` and `probe_search_requires_enabling` check the errors |
| `with-pgvector` | fails unless pgvector is installed, so the probe tests cannot skip; runs every `#[pg_test]`, then `tests/probe_enable_race.sh` (two sessions, synchronised on lock waits in `pg_stat_activity`, not on sleeps) |
| `upgrade` (17 only, separate `upgrade` job) | `tests/upgrade.sh v0.1.0-alpha.3`, `tests/upgrade_from_release.sh` from v0.2.0-alpha.1 and v0.3.0-alpha.1 |

The `postgres` job (a required status check) passes only when every non-experimental `compat`
job passed. The bounded benchmark smoke test and tool tests run once, in the PostgreSQL 17 job
without pgvector, as a smoke test, not a measurement.

Pinned versions: Rust from `rust-toolchain.toml`, pgrx and cargo-pgrx from `Cargo.lock` (0.16.1),
PostgreSQL 17.11 and 18.6, pgvector v0.8.0 (commit 2627c5f) and v0.8.7 (commit f37c13f), checked
against the tag's commit. `cargo pgrx init --pgNN download` always fetches the latest minor
release, so the job fails when a new minor appears and the cache is cold; bump `pg-minor` in the
workflow and this page together. Cache keys include runner OS and architecture, Rust, pgrx,
PostgreSQL minor and pgvector version; the PostgreSQL cache is saved before any test installs
pg_morlet into it. The Rust build cache is keyed by PostgreSQL major (pgvector does not change
what is compiled).

### Contract coverage

The `#[pg_test]`s in `extensions/pg_morlet/src/lib.rs` (and `sql/interval_tests.sql`), by contract:

| Contract | Tests |
|---|---|
| Bit-exact read-back, across chunk boundaries | `encoded_chunks_roundtrip_bitwise_and_report_storage` (float8send comparison across a chunk boundary), `reads_span_maximal_chunks_with_bounded_lookup`, `decode_interval_slices_chunks_and_leaves_gaps_out`, `legacy_float_chunk_collections_keep_working`; corruption: `reads_verify_chunk_integrity`, `encoded_chunk_check_rejects_*` |
| Append, identical replay, rejected conflicts | `durable_chunks_resume_and_rollback`, `batch_append_is_atomic_and_replay_safe`, `rejects_conflicting_chunk_replay`, `encoded_chunks_reject_conflicting_replay`, `rejects_gapped_and_overlapping_chunk_offsets` (new), `import_rejects_changed_recording_spec` |
| Envelope and probe build | `batched_envelope_build_equals_the_per_window_encoding`, `envelope_build_resumes_and_search_finds_matching_burst`, `envelope_functions_match_the_core_definition`, `envelope_probe_is_the_lag_term`, `collection_status_reports_progress_and_backlog`, `probe_search_is_maintained_and_exact_on_small_galleries` |
| Exact search, tie-breaking, source exclusion, overlap suppression | `search_contract_ties_order_by_recording_then_start`, `search_contract_fewer_than_top_k_and_searchable_at_commit`, `envelope_search_refetches_after_suppression` (against a reference implementation), `interval_search_and_overlap_policy`, `rejects_partial_source_exclusion`, `rejects_out_of_bounds_source` |
| Approximate (probe) search | probe = exact when `per_probe` covers the gallery and when routed to the exact path (`probe_search_is_maintained_and_exact_on_small_galleries`, `probe_search_routes_and_respects_filters`); no test compares result lists across rebuilt indexes |
| Filters, roles, RLS, hidden sources | `probe_search_routes_and_respects_filters`, `row_security_covers_companion_tables`, `row_security_rejects_writes_for_hidden_recordings`, `row_security_covers_probe_tables`, `row_security_matches_only_its_own_probe_tables`, `source_query_respects_row_security`, `typed_search_respects_row_security`, `search_respects_table_permissions`, `feature_search_respects_table_permissions`, `stream_search_keeps_parent_visibility_and_compatibility` |
| Upgrade paths | `tests/upgrade.sh`, `tests/upgrade_from_release.sh` (PostgreSQL 17) |
| Races | probe enable during an envelope build: `tests/probe_enable_race.sh` (with-pgvector jobs). The routing fix (a window committed between the router's two counts) has no deterministic regression test: it needs a commit between two statements inside one function call |

Tests share one database and run concurrently; tests that create the shared `env` fixture or the
vector extension take one advisory lock first (`serialize_shared_ddl`).

## Running the checks locally

The script never uses an unspecified PostgreSQL: it requires `PGRX_HOME`, installs pg_morlet into
that pgrx-managed PostgreSQL, runs `#[pg_test]`s in pgrx's throwaway test cluster
(`target/pgrx-test-data-NN`, port 32200+NN), and starts that home's own server (port 28800+NN)
only for the race and upgrade tests, refusing if another server already listens there. Use a
dedicated `PGRX_HOME` so an existing `~/.pgrx` (and its databases and configuration) stays
untouched.

```sh
# 1. Tools (Debian/Ubuntu; macOS: Xcode command line tools, plus bison, flex, pkg-config)
sudo apt-get install build-essential bison flex clang libclang-dev pkg-config libssl-dev
cargo install cargo-pgrx --version 0.16.1 --locked

# 2. A PostgreSQL for the chosen major version, in a dedicated pgrx home
export PGRX_HOME=$HOME/.pgrx-compat
cargo pgrx init --pg18 download        # or --pg17 download, or --pg18 /path/to/pg_config
#    (CI adds --configure-flag=--without-icu --configure-flag=--without-readline --configure-flag=--without-zlib)

# 3. Without pgvector
extensions/pg_morlet/tests/ci_local.sh 18 without-pgvector

# 4. With pgvector: build the pinned release into the same PostgreSQL (or use a second PGRX_HOME,
#    since the without-pgvector mode refuses a PostgreSQL that has pgvector)
PG_CONFIG=$(sed -n 's/^pg18 = "\(.*\)"/\1/p' "$PGRX_HOME/config.toml")
git clone --depth 1 --branch v0.8.7 https://github.com/pgvector/pgvector.git /tmp/pgvector
make -C /tmp/pgvector PG_CONFIG="$PG_CONFIG" OPTFLAGS="" && make -C /tmp/pgvector install PG_CONFIG="$PG_CONFIG" OPTFLAGS=""
extensions/pg_morlet/tests/ci_local.sh 18 with-pgvector

# 5. Install and upgrade checks (PostgreSQL 17 only; needs the release tags)
git fetch origin tag v0.1.0-alpha.3 tag v0.2.0-alpha.1 tag v0.3.0-alpha.1 --no-tags
extensions/pg_morlet/tests/ci_local.sh 17 upgrade
```

The scripts in `extensions/pg_morlet/tests` find `pg_config` through `PG_CONFIG` or the
`pgNN` entry of `$PGRX_HOME/config.toml` (`~/.pgrx` by default) and connect on `PGPORT` or
28800+NN.

## Known limits

- CI covers Linux x86_64 (ubuntu-24.04) only. macOS (development and the evaluation machine) and
  arm64 are not tested in CI.
- Release images (`deploy/Dockerfile`, `postgres:17.6-bookworm`) and `docs/releases.md` target
  PostgreSQL 17 on Linux amd64 only.
- Some evaluation scripts (`evaluation/pilot/*.py`, `evaluation/compression/extension/read_bench.py`)
  default to `~/.pgrx/17.11` paths; they belong to frozen protocols and are not part of this matrix.
- PostgreSQL 16 or older would need a Cargo feature and matrix entries; it is not offered without a
  documented need. A pgrx upgrade (for example for a later PostgreSQL major) is a separate change.
