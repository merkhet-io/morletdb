# Frozen Go numerical reference

This standalone, dependency-free module preserves the original `morlet-energy-v1`
extractor and fixture generator. It is maintenance tooling, not a supported
MorletDB runtime, HTTP service or SDK. Active product code is Rust/PostgreSQL/Python.

The four Go source/test files and go.mod were moved byte-for-byte from
`apps/server` at commit
[1b3f86d](https://github.com/merkhet-io/morletdb/tree/1b3f86dcfedfce1248f49c60e958bffab94dc86f/apps/server).
The module identity is intentionally preserved to avoid touching the reference
generator's imports. There is no root Go workspace and no Go-to-Rust dependency.
Legacy capture/context types remain here only because they are part of the
frozen numerical input contract.

## Check or deliberately regenerate

From the repository root:

```sh
make test-reference
# Explicit numerical-maintenance operation; overwrites the eight fixture JSON files:
make golden
```

Go 1.24 or newer is required. The Numerical reference workflow checks formatting,
vet, race tests, shared fixture agreement and generator compilation. CI does not
regenerate expected values.

The [fixture provenance](../../testdata/morlet-energy-v1/README.md) describes the
frozen inputs/expectations. Do not replace this reference with calls to the Rust
implementation whose output those fixtures check. Preserve reference code and
review numerical changes separately from application changes.

The former server, CLI, monitoring, storage and waveform HTTP example were removed.
Their code remains in Git history/the alpha.1 source tag; there is no automatic
migration from their file store to PostgreSQL. See the
[current recording workflow](../../docs/recording-workflow.md).
