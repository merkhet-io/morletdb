# Beta support boundaries

MorletDB stores uniformly sampled recordings losslessly, retrieves intervals and finds related intervals. It is
not a validated diagnostic or safety system. From 0.4.0-beta.1 the [API and on-disk promises](docs/api-stability.md)
apply: stored data stays readable by later releases of the same major version, and stable functions keep their
behaviour. Semantic versioning
starts at 1.0.

| Area | Beta boundary |
|---|---|
| Editions | The server edition (PostgreSQL with pg_morlet) is the beta. The embedded SQLite edition (`crates/morlet-sqlite`, `morletdb.open`) is experimental: one writer per file, no search; its file format 1 is frozen ([embedded edition](docs/embedded-edition.md)). The WinCC OA integration is experimental ([integrations/winccoa](integrations/winccoa/README.md)). Maturity of every part: [architecture](docs/architecture.md#editions-and-maturity) |
| SQL API | Stable, experimental and internal functions ([API stability](docs/api-stability.md)); experimental functions may change between minor releases |
| Servers | PostgreSQL 17 (reference), 18 and 19 (from 19.0, source builds; packages and an extension-only image for 19 from 0.4.0-beta.2); release image, Debian packages and an extension-only image ([compatibility](docs/compatibility.md)) |
| Platforms | Linux amd64 and arm64, including a Raspberry Pi 3B+; macOS for development; native Windows is unverified |
| Data | One uniformly sampled channel per recording; explicit rate, unit and sample offsets; append-only |
| Updates | `ALTER EXTENSION pg_morlet UPDATE` from every earlier release, tested in CI on populated PostgreSQL 17 databases; updates on PostgreSQL 18 and 19 are untested ([runbook 9](docs/runbooks.md#9-upgrading-the-extension-and-the-image)) |
| Operations | The steward for unattended upkeep; runbooks for backup, restore, replicas, disk and WAL ([runbooks](docs/runbooks.md)); no HA certification |
| Tested range | Only what [operations](docs/operations.md#tested-operating-range) lists; outside it, measure on your own hardware |

**Known limits of this beta**, from the unattended week on a small box ([soak S1](evaluation/soak_s1/RESULTS.md)):
nothing was lost or changed, but on a 2-core box with a hard disk the system did not stay steady. `latest` slowed
as daily recordings accumulated (#527), search slowed, and envelope building and verification fell behind
(#528). A database restart was tested only after the week. 0.4.0-beta.3 adds `morlet_latest_per_stream` for #527
and makes the steward serve the recordings furthest behind for #528; whether the system now stays steady is what
the confirmation soak S2 (#529) will test, and it has not run yet.

No automatic diagnosis, resampling, speed invariance, gap interpolation or synchronized multichannel matching is
provided. Search distances are not confidence scores.

Preserve raw sources and back up valuable databases before updating. A package version (VERSION) and the SQL
extension's version are distinct; replacing the shared library does not update installed SQL definitions until
`ALTER EXTENSION pg_morlet UPDATE` runs.

Ask usage questions or report reproducible problems through GitHub issues. Include release or commit, OS and
architecture, PostgreSQL version, commands and sanitized logs. Use [private security reporting](SECURITY.md) for
vulnerabilities. Support is best effort; no SLA or long-term branch maintenance is promised.

Releases carry exact prerelease image tags and record an image digest. There is no moving `latest` tag.
