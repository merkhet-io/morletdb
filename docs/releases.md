# Alpha releases

The current package identifier is in VERSION (`0.3.11-alpha.1`). GitHub tags use
a leading v; GHCR image tags do not.
- **Up to 0.1.0-alpha.3:** releases report SQL extension version 0.1.0, with no
  upgrade scripts between them.
- **0.2.0-alpha.1:** reports SQL version 0.2.0, with an update script from
  alpha.3's 0.1.0 objects only (see durable-ingestion.md, Upgrading).
- **0.3.0-alpha.1:** reports SQL version 0.3.0, with an update script from
  0.2.0-alpha.1's 0.2.0 objects; the chained update from alpha.3's 0.1.0 is also
  tested.
- **0.3.1-alpha.1:** reports SQL version 0.3.1, with an update script from
  0.3.0-alpha.1's 0.3.0 objects; the chained updates from 0.2.0 and 0.1.0 are also
  tested. The image does not include pgvector, so probe search needs it installed
  separately.
- **0.3.2-alpha.1:** reports SQL version 0.3.2, with an update script from
  0.3.1-alpha.1's 0.3.1 objects; the chained updates from 0.3.0, 0.2.0 and 0.1.0 are
  also tested. Adds `morlet_search_similar`, raises the exact-search default to
  1,000,000 eligible windows and makes exact scoring faster with identical results.
- **0.3.3-alpha.1:** the same SQL version 0.3.2 and extension as 0.3.2-alpha.1 (no update script needed);
  the first image for both linux/amd64 and linux/arm64. Ships with the MCP server for AI agents and the
  seismic and machine demos.
- **0.3.4-alpha.1:** reports SQL version 0.3.3, with an update script from 0.3.2-alpha.1's and 0.3.3-alpha.1's
  0.3.2 objects; the chained updates from 0.3.1, 0.3.0, 0.2.0 and 0.1.0 are also tested. Fixes
  GHSA-pg6f-hcvw-vw34 (acquisitions under row-level security) and the findings of the 0.3.3 review; adds raw
  float64 reads and writes, chunk summaries, interval statistics and overviews, search time filters and
  evidence, and screened exact search. The first release with an SBOM per architecture and build provenance.
- **0.3.5-alpha.1:** the same SQL version 0.3.3 and extension code as 0.3.4-alpha.1 (no update script needed).
  The first release with Debian packages for existing PostgreSQL 17 and 18 servers (amd64 and arm64), each
  installed with apt and trialled before release, with checksums and build provenance.
- **0.3.6-alpha.1:** SQL version 0.3.4, with an update script from 0.3.3 (`ALTER EXTENSION pg_morlet UPDATE`). Fixes
  #225–#227 (search budget on the probe route, concurrent acquisitions, concurrent envelope builders) and adds
  faster single-connection ingestion (I1): batched raw appends (`morlet_append_chunks_raw`), encoding on worker
  threads (`morlet.encode_threads`) and verified appends (`morlet.verify_appends`, on by default); the importer
  commits 256 chunks per transaction and batches automatically. Stored data is byte-identical to 0.3.5-alpha.1.
- **0.3.7-alpha.1:** SQL version 0.3.5, with an update script from 0.3.4. Adds compressed transfer
  (`morlet_read_interval_encoded`; the Python SDK decodes stored chunks in the client with the native decoder in its
  platform wheels), long reads decoded on worker threads (`morlet.decode_threads`), chunk summaries in every new
  collection, the integrity scrub `morlet_verify` and episodic memory, phase 1. The first release with Python SDK
  wheels. The chunk format is unchanged; existing collections keep their layout.
- **0.3.8-alpha.1:** SQL version 0.3.6, with an update script from 0.3.5; Python SDK 0.1.0a2. Adds the steward and
  the ingest hold (`tools/steward`, refusals with SQLSTATE 53100 before the disk is full), synchronous channel
  groups, commit notifications, `morlet_latest`, `morlet_coverage`, exact roll-ups and exact merging of interval
  statistics, and exact search 10x faster on small machines. Builds on PostgreSQL 19 (19rc1, from source); release
  images and Debian packages stay on PostgreSQL 17 and 18 until 19.0. The chunk format is unchanged; existing
  collections keep their layout.
- **0.3.9-alpha.1:** SQL version 0.3.7, with an update script from 0.3.6. The C functions find their library through
  `dynamic_library_path`, so on PostgreSQL 18 and later pg_morlet loads from a directory outside the server, such as
  an image volume (`extension_control_path`). The first release with an extension-only image,
  `ghcr.io/merkhet-io/pg_morlet:0.3.9-alpha.1-pg18` (amd64, arm64), trialled mounted into a clean `postgres:18` before
  publishing. The chunk format is unchanged; existing collections keep their layout.
- **0.3.10-alpha.1:** SQL version 0.3.8, with an update script from 0.3.7; Python SDK 0.1.0a3.
  - **Exact times at slow rates (#343):** rates are read as exact fractions.
  - **Interval statistics:** a native fast path for intervals below 2^20 samples (#346, #349), and no JIT compilation
    on very long recordings (#340).
  - **Embedded edition (experimental, #351):** MorletDB in one SQLite file. The SDK's platform wheels carry it
    (`morletdb.open`), and the release attaches `morlet-sqlite-<platform>.tar.gz` (command line, library, C header).
  - **File format 1:** frozen with a compatibility promise ([file-format.md](file-format.md)).
  - The chunk format is unchanged; existing collections keep their layout.
- **0.3.12-alpha.1:** SQL version 0.3.9 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0a5.
  - **Grizzbit 2.1, chunk format version 5, opt-in (#377):** `SET morlet.chunk_format = grizzbit21`; a misspelt value
    fails. Releases up to 0.3.11-alpha.1 cannot read version-5 chunks.
  - **Lossless chunk shipping from edge to centre (#420):** `morlet_encoded_chunks`, `morlet_append_encoded_chunk` and
    `tools/ingest/morlet_ship.py` (resumable, SHA-256 compared at the end).
  - **Decision states:** about ten times faster and all channels of a cut in one call (#403, #414); precedents in
    logged decisions (#418); a label glossary (#333); alarm and PLC events inside a segment (#421); value rules
    `simple-v2` for new collections (#387; existing collections keep `simple-v1` until `morlet_set_value_rules`).
  - **Steward:** integrity checks get a minimum share under constant load; missing summaries are backlog, not damage.
  - **Importer:** wide plant exports with a time column (#388), clock drift fitted and reported (#393).
  - **MCP server:** `trend` per context segment and over an order key (#385, #334), `search_similar`
    `exclude="occurrence"` (#384), negative answers state their scope (#379).
- **0.3.11-alpha.1:** SQL version 0.3.8, unchanged (no `ALTER EXTENSION` needed); Python SDK 0.1.0a4.
  - **Write-time proof on every append path (#366):** with `morlet.verify_appends` on (the default), every chunk is
    decoded again and compared bit for bit with its input before it is stored.
  - **Transfers verify samples, not chunk bytes (#356):** file and server transfers compare decoded samples.
  - **Sample rates up to 100 MHz (#342)** for recordings without feature extraction.
  - **MCP server:** starts under `python -I` again (#382; 0.3.10-alpha.1's server fails there with
    `No module named 'decision'`). `search_similar` can leave out the query's own recording or part (`exclude`,
    #384), and `coverage`, `locate_time` and `latest` give clearer answers on snapshot data (#386).
  - **The chunk codec is the `grizzbit` crate** (Grizzbit 1 = chunk format versions 2 and 3); no byte changed.
  - The chunk format is unchanged; existing collections keep their layout.

Releases target PostgreSQL 17 on Linux. From 0.3.3-alpha.1, the image is published for
linux/amd64 and linux/arm64: each architecture is built natively on its own CI runner, trialled from a clean
checkout, and the release tag is one multi-architecture index of the two (the per-architecture tags
`<version>-amd64` and `<version>-arm64` remain). 0.3.2-alpha.1 and earlier are linux/amd64 only. The compose
file `compose.release.yaml` uses `MORLET_PLATFORM` (default `linux/amd64`); on ARM machines such as Apple
Silicon, set `MORLET_PLATFORM=linux/arm64` with 0.3.3-alpha.1 or later to run it without emulation. Building from
source also supports PostgreSQL 18 ([compatibility](compatibility.md)).

`0.1.0-alpha.1` was published before the repository's
[history rewrite](history-rewrite.md). Its release record was transferred
unchanged. Its image digest is the same, but the image's OCI labels name the
original commit `0594ee0`, whose tree is identical to the tag's rewritten
commit. `0.1.0-alpha.3` is the first image built from the rewritten history;
`0.1.0-alpha.2` was withdrawn before announcement and must not be used.

## Release process

A reviewed change to VERSION on main triggers Alpha release. The workflow builds
one candidate image, runs import/search/inspection/replay and restart in a fresh
Compose project, and pushes that same image to
`ghcr.io/merkhet-io/morletdb:<VERSION>`. It then pulls the registry digest,
repeats the trial with a fresh volume and creates a GitHub prerelease containing
the image digest and source commit. Failed trials prevent the GitHub release.
In parallel it builds a Debian package of the extension for PostgreSQL 17
and 18 on amd64 and arm64 (`deploy/Dockerfile.deb`, against the PostgreSQL
project's packages on Debian bookworm), installs each one with apt into a clean
official `postgres` image and runs the same quickstart and restart; the release
attaches the four packages with `SHA256SUMS` and a build provenance attestation.
It also builds the Python SDK wheels (`.github/workflows/sdk-wheels.yml`): one per
platform with the native decoder for compressed transfer (Linux x86-64 and arm64,
tagged with the glibc version the library needs; macOS 11+ on arm64), each installed
into a clean environment and tested from the installed package, and a pure wheel for
other platforms; they are attached, checksummed and attested with the packages.
PRs run the candidate trial without publishing. The standalone release Compose
file contains no build directive, so the quickstart cannot compile a local image.

Use a new prerelease version for every publication; never intentionally replace
an existing release image/tag. Before retrying a failed publishing run, inspect
whether the package or Git tag already exists. A pushed image with a failed
subsequent trial is not an approved release. Do not use it.

## Install a package on an existing server

From 0.3.5-alpha.1, each release has Debian packages
for servers that run the PostgreSQL project's packages (apt.postgresql.org) on
Debian 12 or later, for example:

```sh
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify postgresql-17-pg-morlet_<version>_amd64.deb --owner merkhet-io
sudo apt install ./postgresql-17-pg-morlet_<version>_amd64.deb
```

then `CREATE EXTENSION pg_morlet;` in a database. The file names use `.` where the
package version has `~` (`0.3.5.alpha.1` for version `0.3.5~alpha.1-1`), because
GitHub release assets cannot contain `~`. (0.3.5-alpha.1 was first published with a
`SHA256SUMS` that listed the names with `~`; it was replaced with the same hashes under
the served names.) The packages are trialled
only on Debian bookworm images; other distributions are not tested. pgvector
(for probe search) is a separate package, for example `postgresql-17-pgvector`.
An existing database is upgraded with `ALTER EXTENSION pg_morlet UPDATE`.

## Install the Python SDK

From the first release with SDK wheels, pick the wheel for your platform, or the
`py3-none-any` wheel elsewhere (same results, raw transfer for long reads):

```sh
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify morletdb-<version>-py3-none-manylinux_2_34_x86_64.whl --owner merkhet-io
pip install ./morletdb-<version>-py3-none-manylinux_2_34_x86_64.whl
```

## Try a published release

Verify the release exists and copy its exact image digest from image-digest.txt:

```sh
git clone https://github.com/merkhet-io/morletdb.git
cd morletdb
git checkout v0.3.1-alpha.1
export MORLET_IMAGE=ghcr.io/merkhet-io/morletdb@sha256:REPLACE_WITH_RELEASE_DIGEST
docker compose -f compose.release.yaml pull db
docker compose -f compose.release.yaml run --rm walkthrough
docker compose -f compose.release.yaml restart db
docker compose -f compose.release.yaml up --no-build -d --wait db
docker compose -f compose.release.yaml run --rm walkthrough
docker compose -f compose.release.yaml down
```

The walkthrough must finish successfully both times, verifying the known embedded
match, cross-chunk reconstruction and identical reimport. See
[the walkthrough](recording-workflow.md) for credentials and expected behavior.
The first Python run needs network access. This is not an offline bundle.

A private repository/package needs authorized Git/GHCR access. Publication does
not automatically make either public. A maintainer must choose package/repository
visibility before announcing anonymous access.

## Validation meaning

The automated trial runs on a fresh GitHub runner without a host Rust/Python
development setup, using container dependencies and a new database volume.
It is independent of development-host caches/data, not an independent human
usability study. A first-time external user trial remains a release follow-up:
record environment, time to first match, unclear instructions and any manual fixes.
Do not claim that human trial until it occurs.

Image digests identify published bytes. Apt inputs/base tags are not fully
content-addressed, so this does not promise bit-for-bit rebuilds. The tested upgrade
paths are 0.1.0 (alpha.3), 0.2.0 (0.2.0-alpha.1) and 0.3.0 (0.3.0-alpha.1) to the
current SQL version, all run by the `upgrade` CI job. Keep
authoritative input files and back up databases before updating.
