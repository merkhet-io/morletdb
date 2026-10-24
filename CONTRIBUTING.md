# Contributing to MorletDB

Start with [the recording walkthrough](docs/recording-workflow.md), [architecture](docs/architecture.md) and [support boundaries](SUPPORT.md).

The active path is Rust core → PostgreSQL 17 → Python import/search/inspection.
The Go server and bounded SQL layouts are reference experiments; discuss changes there before expanding them.

## Development and validation

Use the pinned Rust toolchain and cargo-pgrx version in the repository.
Follow [development setup](docs/development.md) and the [extension guide](extensions/pg_morlet/README.md).

```sh
make test
cargo clippy --locked -p morlet-core --all-targets -- -D warnings
make pg-test
python -m pip install -r tools/ingest/requirements.txt -r evaluation/requirements.txt
cargo build --locked --release -p morlet-core --example features
python -m unittest discover -s tools/ingest -p 'test_*.py'
python -m unittest discover -s evaluation -p 'test_*.py'
docker compose run --rm walkthrough
```

Run checks relevant to your change. SQL changes need native PostgreSQL tests;
ingestion changes need replay and rollback checks. The preview workflow exercises
a fresh database and restart with preserved data.

Open a focused PR explaining the problem, behavior, checks and limitations.
Use small synthetic fixtures; do not upload customer recordings, credentials or
third-party data without permission. Numerical changes require an explicit
representation version decision and independent expected values. Never regenerate
golden fixtures merely to make a failing test pass.

Good first contributions include reproducible onboarding fixes, documentation
examples, importer validation cases and evaluation controls. Discuss new storage
engines, indexing schemes and extractor changes in an issue first.

Contributions are made under Apache-2.0. Be respectful, explain disagreements
with evidence and protect other people's private data.
