# R2: lag-tolerant envelope search at growing gallery size, exact scan against an ANN prefilter

Status: **FROZEN** in the commit that adds this file, before any R2 data is downloaded.

This is an engineering benchmark with a frozen plan and one pre-registered decision. It
uses **new data** (CX 2025), never read by any earlier study.

## Questions

1. How does pg_morlet's exact lag-tolerant envelope search (`morlet_search_envelope_interval`,
   an exhaustive scan scored in Rust) scale from about 4 thousand to about 230 thousand
   stored windows on one machine?
2. Can an ordinary pgvector HNSW index on the same envelopes serve as a **prefilter**:
   zero-lag cosine top-K′ candidates, then exact lag-tolerant re-ranking of those
   candidates? At what recall and latency?

## Data (fixed now)

- **Source:** GEOFON FDSN, network CX, channel HHZ, stations PB01, PB02, PB06, PSGCX,
  2025-03-01T00:00 to 2025-03-15T00:00 (14 days). Fetched with
  `fetch.py`, written to `data/cx-scale/` with SHA-256 sums. About 0.4 GB miniSEED.
- **Recordings:** one per station, UTC day and contiguous segment (gaps start a new
  recording). Rate 100 Hz, unit `counts`, quantum 1. Metadata: station, day (1–14), and
  `tiers`, the gallery tiers the recording belongs to.
- **Windows:** `morlet_build_envelopes` with window 4,096 and stride 2,048 (defaults).
- **Gallery tiers (nested):**

| Tier | Recordings | Expected windows |
|---|---|---|
| 0 | PB01, day 1 | ~4.2 k |
| 1 | all stations, day 1 | ~17 k |
| 2 | days 1–4 | ~67 k |
| 3 | days 1–14 | ~236 k |

## Queries (fixed now)

100 windows of day 1, chosen once from the stored envelope grid (start multiple of 2,048):
- **50 random:** uniform over all day-1 windows, numpy `default_rng(2025)`.
- **50 energetic:** the 50 day-1 windows with the highest raw RMS, greedily skipping any
  that overlaps an already chosen one (so events, not only noise, are queried).
- Every query is in every tier.

## Arms

All arms return top 10 with the source interval excluded and overlapping windows of one
recording suppressed greedily in rank order (pg_morlet's semantics), lag fraction 0.075.

- **A (reference):** `morlet_search_envelope_interval(collection, rid, start, start+4096,
  '{"tiers":[t]}', 10, 0.075, true, true, 1000000)`.
- **H-K′ (prefilter + exact re-rank), K′ ∈ {100, 1000}:**
  - Side table `(recording_id, start_sample, tiers int[], v halfvec(410))`, `v` = the
    stored f16 envelope (decoded in the client); HNSW `halfvec_cosine_ops`, m 16,
    ef_construction 64.
  - Stage 1: `ORDER BY v <=> q LIMIT K′` with `tiers @> ARRAY[t]`,
    `hnsw.ef_search = max(K′, 100)`, `hnsw.iterative_scan = relaxed_order`; `q` = the
    query's stored f16 envelope.
  - Stage 2 in the database: `morlet_envelope_distance(morlet_envelope(query samples),
    envelope, 30)` for the K′ candidates, then exclusion and overlap suppression
    exactly as A, in the client.
- **Z (zero-lag only):** stage 1 of H-1000 ranked by cosine, then exclusion and
  suppression, top 10. Reported for reference.

## Measures

- **Recall@10** of each arm against A, as sets of (recording, start).
- **Latency:** one client, warm: every tier's queries are run once untimed, then timed
  once; p50 and p95 per arm and tier, client-side wall time including the round trip.
- **Build and storage:** raw append time, envelope build time, HNSW build time;
  `pg_total_relation_size` of raw chunks, envelopes, side table, HNSW index.

## Decision (pre-registered)

**Prefilter supported** if, at tier 3, some K′ gives recall@10 ≥ 0.95 **and** p95 ≤ 0.1×
A's p95. Otherwise the paper reports that a zero-lag ANN index is not a sufficient
prefilter for lag-tolerant search at this scale, with the measured recall.

Reported without deciding: A's latency per tier and its slope in windows; the tier at
which A's p95 exceeds 100 ms; recall per query type (random vs energetic).

## Not in R2

No change to pg_morlet, no tuning of HNSW parameters after the first run, no cold-cache or
concurrent-client runs (stated as a limitation), one machine (Apple M5 Max, macOS,
PostgreSQL 17.11 via pgrx, pgvector 0.8.0).

## Changes after freeze

1. **Load restarted after the envelope build** (no query had run). The first load stopped
   at its final statement (`int[] @> smallint[]`, a missing cast also present in the
   benchmark's stage-1 query). Raw appends (481,214,308 samples, 128 recordings, 451 s)
   and envelopes (234,762 windows, 275 s) were complete and are kept with their measured
   times. The side table and HNSW index were dropped and rebuilt by `load.py
   --resume-side-table`, so their build times are measured on a clean build. Both
   queries now cast to `int[]`; nothing else changed.
2. **Side-table values are decoded in SQL** (`morlet_envelope_decode(envelope)::halfvec`)
   rather than in the client as written above; the values are the same float16 numbers.
