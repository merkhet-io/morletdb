# Monorepo development

## Layout and migration

The Go application lives in `apps/server`, keeping its original module identity and internal package imports. Root Make targets build `bin/morletdb`; existing data directories need no migration. Paths passed with `-data` resolve relative to the process working directory, so use an absolute path when switching between root and application-directory commands.

The root Cargo workspace contains a portable `morlet-core` crate and the `pg_morlet` pgrx extension. Default workspace members include only the portable core so `cargo test` does not need PostgreSQL installed. Cargo still resolves the entire workspace for the lockfile. Commit Cargo.lock for reproducible dependency resolution. Only PostgreSQL 17 is initially supported; do not use `--all-features` when adding PostgreSQL versions later because pgrx builds target one server major at a time.

## Checks

- `make test-go`: Go race tests and vet.
- `make test-rust`: portable Rust tests and workspace formatting.
- `cargo clippy --locked -p morlet-core --all-targets -- -D warnings`: core lint.
- `make pg-test`: in-server pgrx SQL integration tests, including error and NULL handling.

CI runs separate Go, portable Rust and PostgreSQL jobs. Extension integration uses a non-root GitHub Actions runner and the same pgrx CLI/library versions. CI and local development both use a user-owned pgrx-managed installation. The native CI job builds PostgreSQL from source using the runner's preinstalled compiler tools and caches the installation, avoiding an external package repository and system-directory write permissions. All crates and the Go prototype use the root Apache 2.0 license.

## Boundaries

The Rust core includes a port of `morlet-energy-v1`, validated against eight shared fixtures covering all signature fields, tokens and features. These fixtures are committed under `testdata/morlet-energy-v1`. Regenerate explicitly with `make golden`; never regenerate automatically in CI. Changing transform semantics requires a new representation version and reviewed fixtures. See [extractor contract](morlet-extractor.md). Keep Postgres-specific types and SQL boundaries in `extensions/pg_morlet`, and portable algorithms in `crates/morlet-core`. Keep example datasets synthetic and small.
