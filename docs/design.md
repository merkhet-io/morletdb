# Original CNC prototype design

This document records the original Go application design. See [development](development.md) for the monorepo and PostgreSQL extension direction, and [waveform search](waveform-search.md) for the newer storage/retrieval implementation.

## First workflow

A condition-monitoring engineer imports segmented CNC vibration captures, reviews known healthy references under fixed operating conditions, investigates deviations and finds related episodes. MorletDB is read-only with respect to the CNC. Acquisition adapters and machine actuation are outside this MVP.

## Components

- `apps/server/internal/signal`: validation, versioned complex Morlet extraction, energy tokens and capture-level descriptors.
- `apps/server/internal/store`: immutable local captures, atomic persistence and restart reads.
- `apps/server/internal/monitor`: explicit compatible reference cohorts, robust deviation contributions and exact nearest-neighbor retrieval.
- `apps/server/internal/httpapi`: bounded JSON API and embedded offline signal explorer.
- `apps/server/cmd/morletdb`: server, deterministic synthetic demo, JSON/CSV import and inspection.

Use existing Go primitives for storage first. Keeping raw signals makes the representation falsifiable and permits re-extraction. JSON is intentionally replaceable; it does not demonstrate compression or the eventual wave-token storage architecture. The module has no third-party dependencies.

## Numerical contract

The extracted signal is zero-mean. The complex Morlet kernel uses omega0=6, scale=6 fs/(2 pi f), ±3-scale support, zero discrete mean and unit L2 norm. Energy is squared coefficient modulus. For each frequency, only fully supported coefficients contribute. Adjacent frequency bands overlap, and each has different usable boundary extent. The lowest band has roughly 368 samples of total support at all sample rates; minimum capture length is 512. The final token block may be shorter than 128 coefficients.

Tokens approximate localized filter energy; they are neither discrete learned motif IDs nor lossless wavelet coefficients. No compression or inverse-transform claim is made. Signature version changes are required if transform, band layout, pooling or feature semantics change. Existing incompatible records are rejected instead of silently mixed.

## Cohort contract

Comparisons require identical context, sampling rate and sample count. The engineer encodes feed, material, fixture and load in the operation ID for now. Exact RPM matching is conservative and will require carefully defined buckets or order tracking for real production. Tool changes, sensor remounting and maintenance should start new reviewed reference cohorts. Reference labels alone do not constitute review.

The feature floor and alarm threshold are heuristic constants, not statistically calibrated confidence levels. Five reference captures are a software minimum, not a claim of sufficient training data. Repeated captures from the same cycle are not independent validation. The score catches amplitude and distribution changes; it does not distinguish chatter, imbalance, tool wear, a sensor fault or a changed process.

## Verification included

Tests cover tone localization, quadratic amplitude scaling, DC invariance, silence, invalid values, token/global energy consistency, compatible search, reference leakage rejection, persistence/reopen, concurrent writes, corrupt files, HTTP authentication and validation, CSV import, and held-out synthetic healthy versus synthetic chatter behavior. The benchmark measures transform cost for a 2048-sample capture. Synthetic demonstrations are not CNC field validation.

## Next stages

1. Collect real synchronized CNC vibration captures with tool, speed, feed, load, material and reviewed outcomes. Establish train/test splits by run and machine.
2. Compare raw-window RMS/kurtosis, FFT band features, direct wavelet features and learned embeddings. Measure retrieval relevance, false alarms, missed events, latency and bytes stored.
3. Add configurable physical bands, spindle order tracking, envelope analysis, clipping/dropout checks, sensor calibration and real acquisition adapters.
4. Search time-local token patterns and transients, preserving cross-channel timing; introduce coarse-to-fine candidate pruning with measured recall.
5. Replace JSON scans with immutable columnar segments, metadata/time/band indexes and raw-object tiers. Add versioned migrations, quotas, retention, integrity checksums and recovery tooling.
6. Validate baseline calibration, drift and human review workflows before operational alarms. Add production authentication, authorization, monitoring and TLS deployment guidance as usage expands.

No real CNC measurements, hardware-in-the-loop tests or field accuracy claims are included in this initial repository.
