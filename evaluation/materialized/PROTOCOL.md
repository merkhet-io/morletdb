# Materialized envelope representation next to compressed raw recordings (R1)

Status: **FROZEN** in the commit that sets this line. That is before any 2025
(evaluation) waveform or catalogue was downloaded; only GEOFON's availability
metadata was queried. Changes after the freeze are logged under "Changes after
freeze".

## Question

Search-by-example over raw recordings stored losslessly in PostgreSQL (pg_morlet 0.2.0
encoded chunks). The matching operation is fixed: envelope cross-correlation, the
best time-series matcher in the timing test. Does storing the envelopes as a separate,
precomputed (*materialized*) representation make that search cheaper without
changing its results? What does it cost in storage and generation?

Stage 1 found this in an exploratory, not pre-registered, run on the timing test's
held-out windows: identical results at about 1/40 of the latency (H3). That is a lead,
not a confirmed claim. This protocol tests it on fresh data, inside the database.

This is not an indexing study. The materialized representation is a plain array per
window, scanned; nothing here is called an index.

## Data

- **Waveforms.** IPOC (CX) HHZ at 100 Hz from GFZ GEOFON (doi:10.14470/PK615318),
  raw counts.
  - The same 10 stations as the timing test: PB01, PB02, PB03, PB06, PB08, PB09,
    PB15, PB16, PSGCX, MNMCX. GEOFON lists open 2025 HHZ data for all ten
    (availability service, 2026-10-26).
- **Events and windows.** These follow `evaluation/decision/PLAN_TIMING.md` exactly:
  - ComCat M ≥ 2.5 within 300 km of (−20.8, −69.5), hypocentral distance ≤ 250 km;
  - 4096-sample windows starting 5 s before the predicted P arrival;
  - 300 no-match windows;
  - the same data-quality exclusions;
  - the same code (`timing.py` plan/fetch/prepare), called with the year.
- **Development period: 2023.** It was already used as development data for the
  timing test. It is used here to build the harness, check correctness and choose
  the cascade's candidate count N by the rule below.
- **Evaluation period: 2025.** It has never been downloaded or inspected by this
  project. It is read once, after the freeze. (2024 was consumed by the timing test
  and stage-1 H3; it is not used.)

## Storage set-up (identical for all methods)

- PostgreSQL 17, pg_morlet 0.2.0. One encoded stream collection, with one recording
  per window (4096 samples, one chunk, `sample_quantum` 1).
  - Feature extraction runs at append time as usual; its segments table is
    reported but not used by these methods.
- **Materialized envelopes:** a plain table `(recording_id text primary key,
  representation text, envelope real[])`.
  - The envelope is `timing.envelope`: z-score, Hilbert magnitude, 50-sample
    moving average, every 10th sample, then z-score again, giving 410 values.
  - Stored as float4. Representation tag: `xc-envelope-v1`.
- **Candidate features:** a plain table `(recording_id, st real[])` holding the
  timing test's ST vector (12 bands × 4 time bins + S, 51 values; float4).
- **Gallery per query (primary, identical to the timing test):** event windows at
  the same station, other events, excluding any event within 24 h and 30 km of the
  query event (`timing.gallery_mask`). No-match windows are stored but are not
  gallery candidates. Queries are event windows with at least 10 eligible gallery
  windows.
  - Eligibility is computed once per query from recording metadata, read from the
    database. It is identical for all methods and excluded from the timed latency.

## Methods

All methods compute the query's own envelope from its raw samples, read through
`morlet_read_interval`. All score with the same function
(`timing.xcorr_distances`, maximum lag ±3 s). Ties are broken by recording ID.

| Id | Procedure |
|---|---|
| **E: exhaustive raw** | Fetch every eligible window's raw samples, decoded in the database (`morlet_decode_chunk` of its single chunk). Compute envelopes in the client, score all, return the top 10. |
| **M: materialized** | Fetch every eligible window's stored envelope, score all, return the top 10. |
| **C: cascade** | Fetch eligible ST vectors and rank them by RMS Euclidean distance (z-scored with 2023 statistics). Fetch the stored envelopes of the top N, score them, return the top 10. |

**N for C** is chosen on 2023 only: the smallest N in {20, 50, 100, 200} with mean
recall@10 against E of at least 0.95. If none qualifies, N = 200, and C is reported
as not meeting the bar.

E is the reference. The client is Python with NumPy, on the same machine as the
database. Network transfer is localhost TCP.

## Measurements (2025, each query run once warm after one untimed pass)

1. **Storage.** Physical bytes (`pg_total_relation_size`) of:
   - the raw chunk table;
   - the envelope table;
   - the ST table.

   Derived storage is reported as a fraction of the raw chunk table. The original
   float64 payload is reported for reference.
2. **Generation and update cost.** For envelopes and ST, measured over all
   evaluation windows:
   - CPU time per window to compute;
   - time to insert into the table.

   Update cost for one new window is append plus derive plus insert, timed for 200
   windows.
3. **Data access per query:**
   - bytes fetched to the client (payload sizes of returned values);
   - samples decoded in the database;
   - rows read.
4. **Latency:** p50 and p95 of client wall time per query, by method. Hardware,
   OS and PostgreSQL settings are recorded. Only warm cache: data fits in memory,
   and the OS page cache cannot be dropped without root on this machine.
5. **Ranking agreement with E:**
   - recall@10 (overlap of the top-10 sets / 10);
   - fraction of queries with an identical top-10 set;
   - fraction with identical order.
6. **Task relevance**, reported separately from agreement:
   - median |Δd| (km), the hypocentral distance difference between query and
     retrieved events, over the top 10;
   - averaged per event; bootstrap CI over events (2000 resamples, seed 7);
   - computed for E, M and C.

## Decision rules (2025 results)

- **Continue** (materialized envelopes become a product feature to build), if all
  of these hold:
  - M has recall@10 against E ≥ 0.99;
  - M's p95 latency ≤ 0.25 × E's;
  - derived storage (envelopes) ≤ 25% of the raw chunk table;
  - M's relevance is non-inferior to E's: the upper CI bound of M − E median |Δd|
    is ≤ +1 km.
- **Narrow** if M passes agreement and latency but fails the storage bound. Then
  study smaller or quantized envelopes. Do the same if only C passes, at a lower
  storage cost.
- **Pause** if M's p95 latency gain is below 2×, or its agreement is below 0.95.
- C is reported against the same thresholds. It does not change the decision for M.

Thresholds are researcher-chosen and were written before the development run.
They were **not changed after seeing development results**, including the one
result that fails a bound (envelope storage, below).

## Evaluation result (2025)

**Narrow.** M matched E exactly and cut p95 latency to 0.10×, but the envelopes
take 27.9% of the raw chunk table, above the 25% bound. See
[results/eval-2025/RESULTS.md](results/eval-2025/RESULTS.md).

## Development run (2023, before the freeze)

Run: `data/r1-runs/dev` (`load-2023.json`, `run-2023.json`), this harness,
pg_morlet 0.2.0, Apple M5 Max. The 2023 windows were already development data for
the timing test. These numbers inform the evaluation design (N), not the decision.

- 1,413 windows; 1,213 queries from 259 events; mean gallery 149 windows per
  query.
- **M vs E:**
  - recall@10 1.000, with identical sets and identical order in every query;
  - p50 / p95 latency 100 / 161 ms vs 924 / 1527 ms;
  - bytes fetched per query 0.28 MB vs 4.9 MB.
- **Envelope table: 27.7% of the raw chunk table**, above the 25% bound.
  Unless 2025 differs, the decision rule will give "narrow". The bound is kept.
- **Cascade:** recall@10 against E is 0.52 / 0.77 / 0.92 / 0.994 for
  N = 20 / 50 / 100 / 200.
  - The rule selects **N = 200**, which exceeds most galleries, so C200 is about
    M plus an ST pass.
  - At these gallery sizes the cascade has no latency benefit over M.
- **Generation**, per window:
  - 10.8 ms to read raw back through `morlet_read_interval`;
  - 1.2 ms to compute the envelope;
  - 0.8 ms to compute ST;
  - 3.0 ms to insert.
- **Update** (append, derive, insert in one transaction): p50 26 ms.

## Controls and limitations

- **Controlled:** normalization (z-score per window, identical code), window
  boundaries (identical windows), gallery filters (one SQL eligibility query
  shared by all methods), scoring function and tie-breaking.
- **Limitations:**
  - one window per recording, not continuous recordings;
  - fetching the query's own raw window (`morlet_read_interval`) costs the same in
    every method and is included in latency;
  - catalogue-placed windows;
  - catalogue location errors limit relevance;
  - the client-side scoring cost is included in latency;
  - a single machine;
  - no concurrency;
  - no true disk-cold measurements.

## Changes after freeze

1. Added `fetch_windows.py`. `timing.py`'s CLI accepts only 2023 and 2024, so this
   driver calls its unchanged `catalog`, `plan_windows`, `fetch` and `prepare`
   functions for 2025, reusing the timing run's station coordinates. No method,
   threshold or rule changed. Added before any 2025 download.
