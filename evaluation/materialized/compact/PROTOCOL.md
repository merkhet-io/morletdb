# R1b: compact materialized envelopes

Status: **FROZEN** in the commit that adds this line, before any 2022 waveform or
catalogue was downloaded. Changes after the freeze are logged under "Changes after
freeze".

## Why

R1 ([../results/eval-2025/RESULTS.md](../results/eval-2025/RESULTS.md)) found that
materialized float4 envelopes return exactly what the full raw decode returns, at
0.10× its p95 latency. They took 27.9% of the raw chunk table, against a bound of
25%. The frozen rule gave **narrow**: study smaller or quantized envelopes. This is
that study.

The envelope stays a replaceable materialized representation next to lossless raw.
It is not an index, and nothing about raw storage changes.

## Candidates (fixed before any run)

Each candidate is stored in its own table `(recording_id text primary key, envelope
bytea)`, one row per window.

| Name | Envelope | Encoding | Payload |
|---|---|---|---|
| `f4` | R1's `xc-envelope-v1` (decimation 10, 410 values) | float32 | 1640 B |
| `f16` | same | float16 | 820 B |
| `u8` | same | per-window min/max (2 × float32) + 410 × uint8, linear | 418 B |
| `u8-d20` | decimation 20 (205 values), otherwise identical; max lag 15 = 3 s | as `u8` | 213 B |

- **Decoding.** `f16` and `u8` are decoded, then z-scored again, so the matcher
  always sees zero-mean, unit-variance envelopes. `f4` is decoded as stored, which
  makes it R1's M.
- **`f4` as a reference.** It separates schema overhead (bytea, no representation
  column) from quantization.
- **Query envelope.** It is always computed from the query's raw samples at full
  precision, using the candidate's decimation.

## Data

- The same pipeline and filters as R1 and the timing test: CX HHZ, the same 10
  stations; ComCat M ≥ 2.5 within 300 km, hypocentral distance ≤ 250 km;
  4096-sample windows; no-match windows; the same quality exclusions.
- **Development: 2023.** Already loaded by R1. Used to select the candidate.
- **Confirmation: 2022.** It has not been used by any earlier experiment in this
  repository. It is fetched once, after the freeze, with
  `../fetch_windows.py 2022`.
  - GEOFON lists open 2022 HHZ for the study stations (availability service,
    2026-10-26; metadata only).
- **Already consumed:** 2024 (the timing test) and 2025 (R1). Neither is used.

## Method

- **E (reference):** identical to R1. Decode every eligible window's raw chunk in
  the database, compute envelopes in the client, score with
  `timing.xcorr_distances` (±3 s), and return the top 10.
- **Each candidate:** fetch the stored envelopes of the eligible windows, decode,
  score with the same function, and return the top 10.
- **Shared with R1:** the gallery (same station, other events, 24 h / 30 km
  exclusion, no-match windows as distractors), tie-breaking and the warm-up
  pass.

## Selection (2023 only)

The selected candidate is the one with the **smallest table** among those with
mean recall@10 against E ≥ 0.99 **and** identical top-10 order in ≥ 95% of
queries. If none qualifies, R1b stops at "pause" without a confirmation run.

## Measurements (2022, single run)

These are the same as R1, for E and all candidates:
- **Storage:** `pg_total_relation_size` of each table, as a fraction of the raw
  chunk table, plus the payload bytes per window.
- **Generation cost per window:** envelope CPU time, encode time and insert time.
- **Per query:**
  - bytes fetched, samples decoded and rows read;
  - p50, p95 and p99 client wall time;
  - the system load average before and after the timed pass.
- **Agreement with E:** recall@10, identical set and identical order.
- **Relevance:** median |Δ hypocentral distance| of the top 10, averaged per event;
  bootstrap CI of the difference from E over events (2000 resamples, seed 7).

## Decision rules (2022, selected candidate S)

- **Continue** (compact materialized envelopes become a product feature to build)
  if all of these hold:
  - S has recall@10 against E ≥ 0.99;
  - S's p95 latency ≤ 0.25 × E's;
  - S's table ≤ 25% of the raw chunk table;
  - the upper 95% CI bound of S − E relevance is ≤ +1 km.
- **Narrow** if S passes agreement and latency but fails the storage bound. Then
  materialized envelopes are opt-in only, with their cost documented.
- **Pause** if S's agreement is < 0.95 or its p95 latency gain is < 2×.
- Other candidates are reported against the same thresholds but do not change
  the decision.

The thresholds are R1's, unchanged.

## Controls and limitations

- **Controlled:** windows, normalization, gallery filter, scoring and
  tie-breaking are identical for E and all candidates. The methods run back to
  back per query, so they share machine conditions.
- **Limitations:**
  - one window per recording;
  - catalogue-placed windows;
  - catalogue location errors limit relevance;
  - client-side scoring is included in latency;
  - a single machine and no concurrency;
  - no true disk-cold measurement.
- **Background load.** In R1 heavy background load inflated absolute
  latencies. This run records the load average. Ratios against E are the
  latency result.

## Development run (2023, before the freeze)

Run `data/r1b-runs/dev` (`derive-2023.json`, `run-2023.json`); 1,213 queries from 259
events, 1,413 windows. Load average was 28 at the start of the timed pass and 6.5 at
its end.

| Candidate | Table vs raw chunks | recall@10 | Identical set | Identical order | p95 ms | Δ relevance km [CI] |
|---|---|---|---|---|---|---|
| E | — | — | — | — | 1290 | — |
| f4 | 0.277 | 1.000 | 1.000 | 1.000 | 16 | 0.000 |
| f16 | 0.128 | 0.9998 | 0.998 | 0.993 | 18 | 0.000 |
| u8 | 0.072 | 0.997 | 0.973 | **0.859** | 18 | +0.006 [−0.003, +0.017] |
| u8-d20 | 0.043 | 0.956 | 0.602 | 0.080 | 17 | +0.025 [−0.074, +0.128] |

- **Selected: `f16`.** It is the smallest table among the qualifying candidates
  (`f4`, `f16`). `u8` fails the identical-order bar.
- **`f4` stays at 27.7%.** Dropping the representation column and using `bytea`
  instead of `real[]` did not change the size. Rows of about 1.7 kB pack four to an
  8 kB page.
- **Latency.** Candidates take 16–18 ms at p95, against 161 ms for R1's M on the
  same 2023 data. The only difference is the client path: `bytea` read straight
  into NumPy, instead of `real[]` arrays. Most of R1's M latency was therefore
  client-side array conversion. This informs product design (return compact
  binary), not the decision.

## Changes after freeze

(none)
