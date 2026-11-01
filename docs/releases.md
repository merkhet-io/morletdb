# Alpha releases

The current package identifier is in VERSION (`0.3.0-alpha.1`). GitHub tags use
a leading v; GHCR image tags do not.
- **Up to 0.1.0-alpha.3:** releases report SQL extension version 0.1.0, with no
  upgrade scripts between them.
- **0.2.0-alpha.1:** reports SQL version 0.2.0, with an update script from
  alpha.3's 0.1.0 objects only (see durable-ingestion.md, Upgrading).
- **0.3.0-alpha.1:** reports SQL version 0.3.0, with an update script from
  0.2.0-alpha.1's 0.2.0 objects; the chained update from alpha.3's 0.1.0 is also
  tested.
- **Unreleased (main):** SQL version 0.3.1, with an update script from
  0.3.0-alpha.1's 0.3.0 objects.

Releases target PostgreSQL 17 on Linux amd64. Building from source also supports PostgreSQL 18
([compatibility](compatibility.md)).

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
PRs run the candidate trial without publishing. The standalone release Compose
file contains no build directive, so the quickstart cannot compile a local image.

Use a new prerelease version for every publication; never intentionally replace
an existing release image/tag. Before retrying a failed publishing run, inspect
whether the package or Git tag already exists. A pushed image with a failed
subsequent trial is not an approved release. Do not use it.

## Try a published release

Verify the release exists and copy its exact image digest from image-digest.txt:

```sh
git clone https://github.com/merkhet-io/morletdb.git
cd morletdb
git checkout v0.3.0-alpha.1
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
