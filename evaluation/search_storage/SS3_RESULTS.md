# SS3 results: filtered envelope search — the problem is real, routing + filter-aware probes fixes recall; latency verdict narrowly missed

Protocol [SS3_PROTOCOL.md](SS3_PROTOCOL.md) (71fb9ae), harness c300ed9, development results and
frozen routing decision 85400d4, amendment SS3b c4d07dd. Development `morlet_r2`, confirmation
`morlet_bl1c` (previously used by BL1-C, never for filtering or routing). 100 queries per
database and level; every returned row checked against its filter.

## The problem (confirmation database, mean recall@10 against exact search)

| Level (eligible windows) | Exact p95 | Post-filter probes (`main`) | Filter-aware probes (prototype) |
|---|---|---|---|
| L1 station-day (4,217; 1.8%) | 68 ms | **0.515** | 0.998 (p95 724 ms: planner does not use the index) |
| L2 station, 4 days (16,868; 7.2%) | 329 ms | **0.848** | 0.997 |
| L3 station (~58.6k; 25%) | 290 ms | 0.972 | 0.986 |
| L4 4 days, all stations (67,472; 29%) | 390 ms | 0.976 | 0.994 |
| L5 unfiltered (~234.6k) | ~1.2 s | 0.976 | 0.977 |

Development showed the same pattern (post-filter 0.439 at L1, 0.841 at L2). The frozen rules
chose **A = filter-aware probes** (mean recall over levels 0.999 vs 0.851 on development) and
**T = 5,518 eligible windows** (exact p95 reaches 100 ms there).

## Router: exact when eligible ≤ T, else filter-aware probes

| | Recall ≥ 0.95 at every level | Filter / RLS violations | p95 ≤ 1.25 × best strategy at every level |
|---|---|---|---|
| SS3 (count over envelopes), development | yes (0.998–1.0) | 0 | **no** (L5: 70 vs 60 ms) |
| SS3 (count over envelopes), confirmation | yes (0.977–1.0) | 0 | **no** (L4 112 vs 108; L5 78 vs 55 ms) |
| **SS3b** (count from recordings), development | yes (0.998–1.0) | 0 | **yes** |
| **SS3b**, confirmation | yes (0.977–1.0) | 0 | **no, by 0.8 ms** (L5: 55.7 vs 54.8 ms) |

SS3b on confirmation, per level: L1 → exact, recall 1.0, p95 58 ms; L2 0.997, 343 ms; L3
0.986, 93 ms; L4 0.994, 81 ms; L5 0.977, 56 ms. Routing mistakes (approximate route with
recall < 0.9): 0, 0, 4, 1, 5 queries; the L3 and L5 ones match BL1-C's known high-energy
misses, not routing errors.

**Access control:** with row-level security restricting a role to one station, exact,
post-filter and filter-aware searches returned only that station's windows, and a hidden
recording was rejected as a query source ("source recording does not exist or is not
visible").

## Verdict: NARROW

By the frozen rule the router is **not "dependable"**: the confirmation run misses the
latency bound at the unfiltered level by 0.8 ms (1.5%). Everything that matters for
correctness holds at every level on both databases: recall 0.977–1.0, no row outside the
filter, RLS respected. The cause of the original miss was the router's own counting cost,
removed by SS3b.

**Where each path belongs (measured):**
- **Selective filters (≤ ~5,500 eligible windows): exact search.** Fast (≤ 70 ms) and exact;
  the probe paths are either inaccurate (post-filter) or slow (filter-aware, no index use).
- **Moderate filters (7–30%):** filter-aware probes: recall ≥ 0.986 where post-filter drops to
  0.85, at 80–340 ms p95 versus 290–390 ms exact.
- **Unfiltered:** any probe path (≈ 0.977 on held-out data); exact costs about 1.2 s.

**Product recommendation:** route inside `morlet_search_envelope_probes` (exact below the
threshold, filter-aware probes above it, count from the recordings table). It removes the
silent recall loss under selective filters (0.52 → 1.0 at L1, 0.85 → 0.997 at L2). Whether the
0.8 ms miss matters is your call; it does not change which path wins at any level.

## Notes

- Enabling probe search on 234,762 existing windows took about 25 minutes on the development
  database with the default `maintenance_work_mem` (64 MB; the HNSW graph did not fit); the
  documentation should advise raising it for the build.
- One machine, warm caches, one client.
