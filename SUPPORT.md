# Alpha support boundaries

MorletDB is a developer preview for importing uniformly sampled recordings,
querying an interval and inspecting related intervals. It is not a validated
diagnostic or safety system.

| Area | Alpha boundary |
|---|---|
| Active implementation | Portable Rust core, PostgreSQL 17 extension, Python tools |
| Container | Linux amd64; ARM64, Raspberry Pi and native Windows are unverified |
| Data | One uniformly sampled channel per recording; explicit rate, units and sample offsets |
| Ingestion | Local immutable Parquet; synchronous append/extraction with restartable checkpoints |
| Retrieval | Exact feature scan; 4096 eligible segments by default; larger explicit budgets are experiments |
| Compatibility | Same rate, channel, unit, window, normalization and extractor version |
| Lifecycle | Fresh installations; no ALTER EXTENSION UPDATE path or stable API/schema guarantee |
| Operations | No HA, replication, backup/restore or production capacity certification |
| Other paths | Go dashboard/server and bounded SQL layouts are legacy experiments |

No automatic diagnosis, resampling, speed invariance, gap interpolation,
background extraction or synchronized multichannel matching is provided.
Search distances are not confidence scores. The small real-data evaluation
does not establish an advantage over basic statistical features.

Preserve raw sources and backups outside disposable previews. Do not attach a
new alpha image to an existing valuable database volume expecting a migration.
A package version and the SQL extension's internal 0.1.0 version are distinct;
replacing a shared library does not update installed SQL definitions.

Ask usage questions or report reproducible problems through GitHub issues.
Include release/commit, OS/architecture, PostgreSQL version, commands and sanitized
logs. Use [private security reporting](SECURITY.md) for vulnerabilities.
Support is best effort; no SLA or long-term branch maintenance is promised.

Releases carry exact prerelease image tags and record an image digest. There is
no moving latest tag. Repository and registry visibility are managed separately;
a private repository or package requires access even when code is Apache-2.0.
