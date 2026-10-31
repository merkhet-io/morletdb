# BL1 Stage A: candidate generation for bounded-lag envelope search

Status: **FROZEN** in the commit that adds this file, before any candidate method is
measured. Amendments are appended at the end, each committed before what it changes is run.

## Question

Exact bounded-lag envelope search scans every eligible window (2.8 s median at 234,762
windows in R2). R2-D showed that a zero-lag shortlist misses about a quarter of the exact
top ten at that size because of the zero-lag metric, not ANN error. Can candidate
generation that respects the lag range, or simply a faster exact scan, give the same top
ten at a fraction of the latency, within a storage budget?

This is **Stage A only** (algorithm screen on development data). Streaming maintenance
(Stage B), an independent confirmation slice and the RisingWave ADR are out of scope here
and happen only if this stage qualifies, under their own protocols.

## Contract (unchanged; the fidelity oracle)

`xc-envelope-f16-v1` as specified in `evaluation/pruning/README.md` §1: full-precision
query envelope, stored float16 envelopes decoded and z-scored, d = 1 − max over |l| ≤ 30 of
(1/n)·c(l) with c(l) = Σ over the overlap of q[t+l]·g[t] (zero outside support, no
circular shift, no per-lag renormalisation), eligibility (rate, channel, unit, window,
metadata containment), source exclusion, ordering by (distance, recording_id,
start_sample), greedy overlap suppression before top 10. Every arm's final ranking is
computed by `morlet_envelope_distance` in the database. No other distance is substituted.

## Data (development only)

R2's database `morlet_r2` (CX HHZ, PB01/PB02/PB06/PSGCX, 1–14 March 2025; consumed by R2
and R2-D), gallery **tier 3 (all 234,762 windows)**, and R2's 100 queries
(`dev_queries.json`: 50 random, 50 high-energy, day 1). No new data is read in Stage A.

## Mechanism of family A (stated before measuring)

For |l| ≤ 30 define the zero-padded shifted query s_l[t] = q[t+l] if 0 ≤ t+l < n, else 0.
Then **c(l) = ⟨s_l, g⟩ exactly** (positive and negative lags as in the Rust scorer), so
the exact score is max over l of an inner product with a shifted probe. Consequences:
- Probes must use **inner product**, not cosine: ‖s_l‖ differs between probes, so cosine
  scores from different probes are not comparable, while ⟨s_l, g⟩ is the scorer's own
  lag term. Stored g is z-scored (‖g‖² = n up to float16 rounding).
- With all 61 lags probed and exact per-probe top-K, the union contains every window whose
  best lag term ranks in the top K at its own best lag; for K ≥ 10 + (windows lost to
  exclusion and suppression) it contains the exact top ten. A coarser grid or ANN probes
  are approximations; nothing else is claimed as a bound.
- Probe vectors and stored vectors are float16 (`halfvec`), so probe scores equal c(l) only
  to float16 precision; final ranking is always the exact scorer.

**Family B (coarse bounded-lag screening) is not run.** Its certified form was measured
by the pruning study (bounds prune ≤ 63%, ceiling 1.7–1.9×); an uncertified coarse screen
has no stated mechanism to do better than family A, whose lag terms are exact. The slot is
used for the stronger exact alternative below instead.

## Arms (all on tier 3, same 100 queries)

| Arm | Candidate generation | Final ranking |
|---|---|---|
| **A** | none (R2's exhaustive PL/pgSQL scan, `morlet_search_envelope_interval`) | exact |
| **X2, X8** | none: one SQL statement scoring every eligible envelope with `morlet_envelope_distance`, `ORDER BY distance, recording_id, start_sample LIMIT 200`, parallel workers per gather 2 (default) or 8; exclusion in SQL, suppression in the client. If suppression exhausts the 200 rows, the query is flagged and counted as a miss | exact |
| **P-G-K** (HNSW) | probes s_l for l in grid G, each `ORDER BY v <#> probe LIMIT K` on a new HNSW index `halfvec_ip_ops` (m 16, ef_construction 64, ef_search max(K,100), relaxed order), one statement (UNION ALL), union deduplicated | exact re-rank of the union |
| **PX-G-K** (exact probes) | same probes scored exhaustively in the client (numpy inner products on the side table's vectors), no ANN | exact re-rank in the DB |
| **EZ-match** | exact zero-lag cosine top-K′, K′ = that query's PX-G-K union size (equal candidate budget) | exact re-rank in the DB |

Grids: **G7** = {−30, −20, …, 30}, **G13** = {−30, −25, …, 30}, **G61** = all lags.
K ∈ {50, 100}. PX and EZ-match measure descriptor quality without ANN; P adds ANN and
gives the latency. PX-G61 is the validity check of the bound above, not a candidate.
EZ-match is run for G7 and G13 only.

## Measures

- Recall@10 against A (sets of (recording, start)); mean, per-query distribution, by query
  type, and by |l\*| bin (l\* = best lag of each exact top-ten item, from the client
  computation) — {0–5, 6–15, 16–30}.
- Unique candidates exactly scored per query (mean, p95); coverage of A's top ten.
- Latency p50/p95, warm, one client: A, X2, X8, P (probe statement + re-rank + fetching the
  query); PX and EZ-match are offline and have no latency.
- Bytes: raw chunks, envelope table, halfvec side table, each HNSW index; build time of the
  new index.
- Plans: `EXPLAIN` for one query per P config must show index scans on the new index for
  every probe branch; X plans must show a (parallel) sequential scan; otherwise the arm is
  reported as not exercised.

## Gate (decided now)

A **P configuration qualifies** if, on these 100 queries:
1. mean recall@10 ≥ 0.95;
2. p95 latency ≤ A's p95 / 3;
3. p95 latency below the better of X2/X8's p95 (the strongest exact baseline);
4. added structures (halfvec side table + new index) ≤ 1.5× the raw chunk table; no
   stored shifted copies.

**Verdict:**
- **CONTINUE (to Stage B)** if some P configuration qualifies.
- **EXACT KERNEL WINS** if no P qualifies but X2 or X8 meets condition 2 with recall 1.0
  (the product path is a faster exact scan; Stage B is not needed for it).
- **STOP** otherwise.
No configuration, grid or K is added after results are seen.

## Correctness controls (tests before measuring)

- ⟨s_l, g⟩ equals the reference lagged sum for random vectors at every |l| ≤ 30 (both
  signs, boundaries ±30).
- Synthetic shifted match: g = shift of q by l₀ is found by PX-G61 for every l₀, and by
  coarser grids when l₀ lies on the grid.
- PX-G61 with exact per-lag top-K returns a superset of the exact top ten on random data.
- Union deduplication, deterministic ordering with ties, exclusion and overlap suppression
  (R2's rules).
- Database check: for a few real windows, the client's max over lags matches
  `morlet_envelope_distance` within float16 tolerance.

## Not in Stage A

No tuning of HNSW, grids or K after results; no new data; no change to pg_morlet; no
Stage B; no learned embeddings; no Fourier-magnitude bounds.
