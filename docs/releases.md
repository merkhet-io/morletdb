# Releases

The current package identifier is in VERSION (`0.4.0-beta.7`; releases up to 0.3.14-alpha.1 were alphas). GitHub tags use
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
- **0.4.0-beta.1, the first beta:** SQL version 0.3.12 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b1.
  The chunk format is unchanged.
  - **The API and on-disk promises apply** ([api-stability.md](api-stability.md)); [support boundaries](../SUPPORT.md).
  - **Known limits from soak S1:** integrity held for an unattended week on a 2-core box with a hard disk, but it did
    not stay steady: `morlet_latest` grows with the number of daily recordings (#527), and envelope building and
    verification fall behind there (#528).
  - **Fixes:** envelope building after compaction (#488); shipping refuses a prefix with a chunk more or fewer (#495);
    the MCP history cache follows the caller's visibility (#494); two Grizzbit encoder selection fixes (#505, #506);
    the steward no longer stops verifying on slow boxes (#532) and finds envelope grids by index (#531); the SeedLink
    collector survives a database restart (#525); WinCC OA fixes (#496, #497).
  - **New:** the MCP `labels` tool (#333); operator runbooks (#197); the experimental WinCC OA integration (#474).
- **0.4.0-beta.7:** SQL version 0.3.18 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b6. The chunk format
  is unchanged. Calibration sets (experimental) keep references to the recordings they were captured from, so a
  reused recording ID never makes earlier evidence visible under row security (#621); with row security, grant writers
  INSERT and readers SELECT on each `<collection>_calibration_refs` table after updating. Members record
  interpretation revisions (#620). Search, `explain_match` and the MCP tools report interpretation revisions (#608).
  Faster sample-to-time mapping on recordings with many time anchors (part of #546). The steward and the codec are
  unchanged.
- **0.4.0-beta.6:** SQL version 0.3.17 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b5. The chunk format
  is unchanged. New and experimental: interpretation revisions for unit and calibration corrections (part of #600)
  and diagnostic calibration sets (#601), both opt-in per collection. Fixes: time anchors survive dump and restore
  (#607). Copies refuse recordings with a corrected unit or calibration (#609). The steward, the MCP server and the
  codec are unchanged.
- **0.4.0-beta.5:** SQL version 0.3.16 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b4. The chunk format
  is unchanged. New and experimental: `explain_match` (#573), resolving a machine and a time to intervals (#580) and
  evidence bundles (part of #575). The MCP server gains an optional cap on concurrent calls, off by default (part of
  #541). The steward, the ingest tools and the codec are unchanged.
- **0.4.0-beta.4:** SQL version 0.3.15 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b3. The chunk
  format is unchanged.
  - **Fixes from the review of beta.3 (#560–#565):** the time mapping functions work on collections without an
    anchor table (SQL 0.3.15, #562); the SDK refuses every ambiguous time interval (#563); the steward keeps the
    envelope stride after episodic memory removes early windows (#560); MCP times keep their linear-clock and
    extrapolation qualifications (#565); CSV anchors report an uncertainty that covers every row (#561); the
    edge shipper ships and verifies time anchors (#564).
  - **Investigation snapshot (experimental, #572):** `Client.investigate` and the MCP tool `investigate_interval`
    read matches, query statistics, an optional context comparison and coverage in one read-only snapshot, with
    `evidence_states` and interval references. No temporal cutoff; not a diagnosis.
  - The known limits of 0.4.0-beta.1 from soak S1 (#527, #528) are addressed in beta.3; the confirmation soak S2
    (#529) is planned on this release (protocol still a draft; not yet run).
- **0.4.0-beta.3:** SQL version 0.3.14 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b2 (unchanged). The
  chunk format is unchanged.
  - **`morlet_latest_per_stream` (experimental, #527):** the newest recording with samples of each stream, so a
    collector's daily recordings no longer make "latest" read the chunks of every stored day (choosing the recording
    still reads one metadata row per stored recording). `morlet_latest` keeps its signature.
    MCP `latest` takes `stream_keys`.
  - **Steward (#528):** the candidates are the recordings furthest behind (up to five per collection), and of those
    the steward serves the one it served least recently, never-served first, ties in backlog order. In soak S1,
    recordings late in alphabetical order fell behind without limit.
- **0.4.0-beta.2:** SQL version 0.3.13 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0b2. The chunk
  format is unchanged.
  - **Time anchors (experimental, #393, #383):** sync points per recording, written by the importer from CSV
    timestamps, used by MCP `locate_time`, the UTC times other MCP tools report for anchored recordings, and the
    SDK's `samples_for_time`. MCP `coverage` times and the SQL search functions' UTC evidence still use start + rate.
  - **PostgreSQL 19.0:** Debian packages for 19 (amd64, arm64) and the extension-only image `<version>-pg19`. Updates
    on 19 are not tested; install fresh.
  - **Release image on PostgreSQL 17.12** (was 17.6).
  - **Steward:** counts space freed inside the database toward the disk reserve (#538) and bounds its own tables
    (#540; run `morlet-steward init` again after updating).
  - The known limits of 0.4.0-beta.1 from soak S1 (#527, #528) still apply.
- **0.3.14-alpha.1:** SQL version 0.3.11 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0a7.
  - **Float32 codings, chunk format version 6, on by default (#326, study B8):** float32 machine data about half the
    size; `SET morlet.float32_chunks = off` turns it off. Releases up to 0.3.13-alpha.1 cannot read version-6
    chunks: before a downgrade, re-append them with the setting off.
  - **Faster context values (#414)** and pooled spreads computed once (#335).
  - **Search exclusions (#384):** leave out the same part or the whole source recording.
  - **Steward metrics (#197):** a Prometheus endpoint; **small-memory image settings (#466).**
- **0.3.13-alpha.1:** SQL version 0.3.10 (run `ALTER EXTENSION pg_morlet UPDATE`); Python SDK 0.1.0a6. Security and
  data-safety fixes; the chunk format is unchanged.
  - **Episodic memory under row-level security (#438):** episodes, ledger, kept summaries and memory runs follow row
    security; the update applies it to existing collections.
  - **Steward approvals enforced at the table (#439):** run `steward.py init` again on existing installations.
  - **Episodic memory and envelopes:** a representative keeps its samples while it represents an episode (#440);
    envelopes build on after compaction removed the first windows (#441).
  - **Transfers:** S3 import resumes objects that were cut off (#442); chunk shipping checks the target before writing
    (#443, #444).
  - **Decision states:** a history cache and a faster grid check (#403).
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

A reviewed change to VERSION on main triggers the Release workflow. The workflow builds
one candidate image, runs import/search/inspection/replay and restart in a fresh
Compose project, and pushes that same image to
`ghcr.io/merkhet-io/morletdb:<VERSION>`. It then pulls the registry digest,
repeats the trial with a fresh volume and creates a GitHub prerelease containing
the image digest and source commit. Failed trials prevent the GitHub release.
In parallel it builds a Debian package of the extension for PostgreSQL 17,
18 and 19 (19 from 0.4.0-beta.2) on amd64 and arm64 (`deploy/Dockerfile.deb`, against the PostgreSQL
project's packages on Debian bookworm), installs each one with apt into a clean
official `postgres` image and runs the same quickstart and restart; the release
attaches the six packages (four up to 0.4.0-beta.1) with `SHA256SUMS` and a build provenance attestation.
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
