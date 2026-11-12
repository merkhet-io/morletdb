# Working on MorletDB

MorletDB stores and retrieves waveform recordings. Correct samples, timing, provenance,
and access control matter more than a faster benchmark. Keep changes focused and preserve
the user's unrelated work; use an isolated branch/worktree when needed.

## Find the contract first

- Read `README.md` and the docs for the affected feature. Check relevant open issues
  before proposing new work. Treat shipped code, proposals, and measured results separately.
- `crates/grizzbit`: lossless codec; `crates/morlet-core`: portable signal algorithms.
- `extensions/pg_morlet`: PostgreSQL integration, SQL API, migrations and database tests.
- `crates/morlet-sqlite` and `crates/morlet-ffi`: embedded storage and native interfaces.
- `sdk/python`, `tools/ingest`, `tools/steward`, `tools/mcp`: clients and operational workflows.
- `evaluation` and `research`: protocols and evidence; `docs/index.html` is the website.
- Keep PostgreSQL dependencies out of portable crates. `tools/reference-go` is a frozen
  numerical reference, not an application to expand.

## Invariants to preserve

- **Lossless means bit-exact**, including signed zero. Reject unsupported input explicitly;
  never silently round, resample, interpolate, or change calibration. Follow `docs/chunk-format.md`
  and `docs/file-format.md`; old persisted formats must remain readable under their contracts.
- Recordings are uniformly sampled channels with zero-based, half-open sample intervals.
  Transfers must preserve both samples and their recording specification/provenance.
- Appends stay contiguous; identical retries are no-ops, conflicting retries fail. Test
  rollback and interrupted resume at actual commit boundaries. Progress is not completion.
- Raw data is authoritative until explicitly compacted. Compaction must preserve retained
  dependencies, summaries, coverage reporting and subsequent maintenance. Missing evidence
  must be distinguishable from a negative search result.
- Follow `docs/search-contract.md`: exact scoring, approximate candidate selection and
  diagnostic relevance are different claims. Preserve budgets, exclusions and deterministic ties.
- Access checks must hold for direct SQL and every companion table, not just API calls.
  Test non-owner roles and both feature-enablement orders. Approval rules must survive the
  privileges actually granted; the MCP interface stays read-only. Follow `SECURITY.md`
  for vulnerability disclosure; never commit credentials or private recordings.

## Change and verify

- Use the pinned toolchain and dependencies. Consult `.github/workflows/` for the current
  test matrix; do not use `--all-features` with pgrx (select one PostgreSQL major).
- For behavioral fixes, add a small regression that demonstrates the failure. Test feature
  interactions, especially retention + builders, RLS + new tables, and retries + transfers.
- Rust: `cargo fmt --all -- --check`; run `cargo test --locked -p <affected-crate>` and
  `cargo clippy --locked -p <affected-crate> --all-targets -- -D warnings`.
- SQL: run native PostgreSQL tests (`make pg-test` for PG17); cover fresh installation and
  upgrades when changing SQL definitions/schema. Follow `docs/compatibility.md` and
  `docs/api-stability.md`; keep migration scripts and API inventories consistent.
- Python: run the affected `unittest` suite, with a disposable database/native library
  where required. A skipped integration test is not a passing integration test.
- Docs-only changes need link/example and diff checks, not a database rebuild. For website,
  paper or reported-number changes, use the relevant checks in `tools/precheck.sh`.
- Never regenerate golden fixtures to hide failures. Numerical changes need independent
  expected values and an explicit format/representation-version decision.
- Freeze evaluation protocols before confirmation runs. Preserve recorded results and
  deviations; label development measurements and negative results honestly. Do not tune on
  held-out data or generalize a benchmark beyond its measured workload.
- Before handing off, inspect the diff and run `git diff --check`. Report the behavior changed,
  checks actually run, skips/limitations, and any migration implications in the PR.
