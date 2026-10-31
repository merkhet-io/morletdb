# SS3: dependable metadata-filtered envelope search

Status: **FROZEN** in the commit that adds this file, before any SS3 measurement.

## Problem

`morlet_search_envelope_probes` applies eligibility (rate, channel, unit, window, metadata
containment) after probing: its 13 × 50 candidates come from the whole gallery, so a
selective filter discards most of them and recall falls. Exact search
(`morlet_search_envelope_interval`, parallel since #64) is exact for any filter and costs
about 5 µs per eligible window. Investigations usually filter (one sensor, one period).

## Strategies (all in-database functions, same scorer, eligibility, exclusion, ordering and suppression)

- **E (exact):** `morlet_search_envelope_interval(..., max_candidates => 1000000)`.
- **PP (post-filter):** `morlet_search_envelope_probes` as on `main`.
- **PI (filter-aware probes):** a prototype copy of that function in which the eligible
  recordings are computed first and each probe scan is restricted to them
  (`WHERE recording_id = ANY(eligible)`), with pgvector's iterative index scan
  (`hnsw.iterative_scan = relaxed_order`, default `max_scan_tuples` 20,000), set for the call
  only. Mechanism: the index keeps scanning until it has 50 eligible candidates per probe.
  Eligibility is still re-checked in the re-rank.
- **R (router):** count eligible windows first (`count(*)` over the envelope table joined
  with the eligible recordings); if the count is at most **T**, run E, otherwise run the
  approximate strategy **A** = whichever of PP and PI has the higher mean recall over all
  development levels (ties: lower p95). Counting cost is included in R's latency.

**T is set from development data by a rule fixed now:** the eligible-window count at which
E's p95 reaches 100 ms, by linear interpolation of E's p95 against the levels' mean
eligible counts (0 if even the smallest level exceeds 100 ms; the largest level's count if
none does). Rationale: exact search is preferred whenever it is interactive.

## Filter levels (per query; the query's own station is s)

| Level | `metadata_filter` | Eligible windows (both databases) |
|---|---|---|
| L1 | `{"station": s, "day": 1}` | 4,217 (1.8%) |
| L2 | `{"station": s, "tiers": [2]}` (days 1–4) | 16,868 (7.2%) |
| L3 | `{"station": s}` | ~58,700 (25%) |
| L4 | `{"tiers": [2]}` (all stations, days 1–4) | 67,472 (29%) |
| L5 | `{}` | ~234,700 (100%) |

L3 and L4 have similar sizes but filter by sensor versus by time.

## Data

- **Development:** R2's database (`morlet_r2`) and its 100 queries. Used to choose A and T.
- **Confirmation:** BL1-C's database (`morlet_bl1c`) and its 100 queries. Its data was read
  once before (BL1-C), but never for anything that informed filtering or routing; it is
  reported as previously used, not as unread.

## Measures (per database, level and strategy)

- Recall@10 against E (mean, minimum, 5th percentile); every returned row must satisfy the
  filter (checked per row; any violation is a failure regardless of recall).
- p50 / p95 latency (warm: one untimed pass, then one timed pass), single client.
- For PI: plan check that each probe uses the HNSW index with the iterative scan; candidates
  returned per probe (to see `max_scan_tuples` exhaustion).
- For R: the chosen path per query; routing mistakes (approximate routes with recall < 0.9),
  and the counting cost.
- **Access control:** with row-level security on the recordings table restricting a role to
  one station, every strategy run as that role with `{}` returns only that station's windows.

## Decision (pre-registered; evaluated on the confirmation database with A and T frozen)

**The router is dependable** if at every level:
1. mean recall@10 ≥ 0.95;
2. no row violates the filter or the RLS policy;
3. p95 ≤ 1.25 × min(E's p95, A's p95) at that level.
Reported regardless: PP vs PI recall by level (does filter-aware probing fix the
selective-filter problem by itself?).

If the router is dependable, the product change is: route inside
`morlet_search_envelope_probes` (and use filter-aware probes if A = PI), in a separate PR with
its own tests and upgrade checks.
