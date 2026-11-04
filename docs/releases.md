# Alpha releases

The current package identifier is in VERSION (`0.3.5-alpha.1`). GitHub tags use
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
GitHub release assets cannot contain `~`; in 0.3.5-alpha.1 only, `SHA256SUMS` lists
the names with `~`, so compare the hashes by hand (`sha256sum *.deb`). The packages are trialled
only on Debian bookworm images; other distributions are not tested. pgvector
(for probe search) is a separate package, for example `postgresql-17-pgvector`.
An existing database is upgraded with `ALTER EXTENSION pg_morlet UPDATE`.

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
