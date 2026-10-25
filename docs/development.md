# Monorepo development

Read the [canonical architecture](architecture.md) first. `make test` checks the active Rust core. The bounded SQL models remain legacy experiments; Go is retained only as a frozen numerical reference.

## Layout and migration

The legacy Go application, its file store, HTTP API, CLI and dashboard have been removed. `tools/reference-go` is a standalone module containing only the original extractor, generator and numerical tests. It keeps its old module identity and source unchanged. Root Go workspace and server/build aliases are removed. Old file-store data is not automatically migrated; preserve source data and explicitly import approved recordings into PostgreSQL.

The root Cargo workspace contains a portable `morlet-core` crate and the `pg_morlet` pgrx extension. Default workspace members include only the portable core so `cargo test` does not need PostgreSQL installed. Cargo still resolves the entire workspace for the lockfile. Commit Cargo.lock for reproducible dependency resolution. Only PostgreSQL 17 is initially supported; do not use `--all-features` when adding PostgreSQL versions later because pgrx builds target one server major at a time.

## Checks

- `make test-reference`: optional Go numerical-reference race tests and vet.
- `make test-rust`: portable Rust tests and workspace formatting.
- `cargo clippy --locked -p morlet-core --all-targets -- -D warnings`: core lint.
- `make pg-test`: in-server pgrx SQL integration tests, including error and NULL handling.

CI runs a small Numerical reference workflow, portable Rust and PostgreSQL jobs. Extension integration uses a non-root GitHub Actions runner and the same pgrx CLI/library versions. CI and local development both use a user-owned pgrx-managed installation. The native CI job builds PostgreSQL from source using the runner's preinstalled compiler tools and caches the installation, avoiding an external package repository and system-directory write permissions. All crates and the Go numerical reference use the root Apache 2.0 license.

## Boundaries

The Rust core includes a port of `morlet-energy-v1`, validated against eight shared fixtures covering all signature fields, tokens and features. These fixtures are committed under `testdata/morlet-energy-v1`. Regenerate explicitly with `make golden`; never regenerate automatically in CI. Changing transform semantics requires a new representation version and reviewed fixtures. See [extractor contract](morlet-extractor.md). Keep Postgres-specific types and SQL boundaries in `extensions/pg_morlet`, and portable algorithms in `crates/morlet-core`. Keep example datasets synthetic and small.

