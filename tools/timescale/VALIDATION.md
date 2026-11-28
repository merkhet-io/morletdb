# Adapter development validation — 2026-11-28

This report covers the optional adapter and demo, separate from the frozen TC1
coexistence experiment. Tests use synthetic data and the pinned release pair;
they do not validate unreleased MorletDB binaries or production capacity.

- Native PostgreSQL 17.12 / TimescaleDB 2.31.0 / MorletDB 0.4.0-beta.3:
  **14 tests passed**, no skipped tests, local Linux arm64 Docker guest.
- Grafana OSS 11.2.2: all five dashboard queries returned rows through its actual
  PostgreSQL datasource using the restricted reader role.
- Playwright 1.56 Chromium: loaded the dashboard, clicked the actual waveform
  data link, and checked the viewer selection and interval API. The clicked
  recording was `capture/B`, interval `[4096,8192)`, HTTP 200.
- Seed retries produced no duplicate metrics. Worker tick completed. After
  metric retention: zero detailed metrics, two materialized aggregate rows,
  original waveform still verified and dashboard marked the metrics expired.
- Workflow syntax passed actionlint. The amd64/arm64 CI matrix is configured;
  remote CI execution has not yet been observed.

Development caught a missing integer-clock registration for the continuous
aggregate, an SQL-format placeholder escape, and Grafana double-encoding URLs.
The final dashboard uses the raw URL formatter; the browser test compares the
clicked recording and interval to the evidence ledger. Harness errors (connection
password serialization, viewport lazy rendering, and a test-only column name)
were corrected before the passing runs. These are development measurements,
not preregistered confirmation results.

Remaining gates: extension/schema upgrades, cross-host recovery including roles,
replica failover, sustained load, metadata-ledger lifecycle, and a broader current
release matrix. Mixed-tenant RLS aggregates remain unsupported; use one database
per trust domain. Interval summaries are bucketed by interval start, not split at
minute boundaries. No Influx protocol compatibility is introduced. No core SQL,
waveform format, or extension migration changes are included.
