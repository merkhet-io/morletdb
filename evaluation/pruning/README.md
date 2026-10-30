# Certified pruning for exact envelope search: headroom and feasibility

**Decision: PAUSE before building summaries.** The cause is **loose bounds**, made worse
by **fixed per-query cost**.

- **Within one station** (the common workload), perfect pruning could at best make
  search 1.57× faster.
- **Across all candidates,** the tightest bounds that cover every allowed time shift
  prune 63% of candidates even with an oracle threshold. A compact 64-byte summary
  prunes 55%. That gives at most 1.7–1.9× with free bounds, below the 2× gate.

No summary, schema or API was built. This is the early stop in step 0 of the
experiment brief.

This work is engineering development on data already used by R1b (CX 2022). No
confirmation slice was downloaded, because the development ceiling already fails the
gate.

## 1. The exact contract (`xc-envelope-f16-v1`, `main` after #47)

**Representation**
- z-score the window: subtract the first sample, centre, divide by the population SD;
- take the |Hilbert| magnitude via the full FFT;
- smooth with a 50-sample moving average (numpy `"same"` alignment);
- keep every 10th value, giving n = ⌈window/10⌉ (410 at 4096 samples);
- z-score again.

Stored envelopes are IEEE binary16, decoded and z-scored again. The query envelope is
full precision.

**Distance:** d = 1 − max over |l| ≤ L of (1/n)·c(l), where:
- c(l) = Σ over the overlap of q[t+l]·g[t], with no per-lag renormalisation;
- L = ⌊max_lag_fraction·n⌋, default 0.075, so 30 at n = 410;
- the Rust summation order is ascending t for each lag.

**Eligibility:**
- the same rate, channel, unit and window size, plus metadata containment;
- the "exceeds max_candidates" error is raised before scoring;
- source exclusion applies.

**Order and suppression:**
- results are ordered by (distance, recording_id, start_sample);
- overlapping windows of the same recording are suppressed greedily in rank order,
  **before** top_k.

So a safe pruning threshold must come from k already-scored hits that don't overlap
each other. The k-th best score so far is not safe.

## 2. Headroom (`headroom.py`, `results/headroom.json`)

The timings are taken inside the database, 100 queries per part, on the 1,826 CX 2022
windows of 4096 samples. Same-station search is the R1b workload.

| Workload | Candidates/query | Search | Scoring | Everything else | Ceiling with perfect pruning |
|---|---|---|---|---|---|
| Same station | 182 | 6.5 ms | 2.35 ms (12.9 µs each) | 4.1 ms | **1.57×** |
| Unfiltered | 1,825 | 27.2 ms | 20.7 ms (11.3 µs each) | 6.5 ms | 4.2× |

"Everything else" is reading the query window and computing its envelope (about
1.1 ms), the candidate count (1.2 ms), the scan and sort (about 1.4 µs per candidate)
and PL/pgSQL.

**Same station:** the 2× gate is out of reach for any summary. The remaining question
was whether a bound can prune enough on large candidate sets.

## 3. Bounds that hold for every allowed shift

Both bounds bound **max over all |l| ≤ L of c(l)** at once. They don't bound one lag at
a time.

### Spectral

Zero-pad q and g to N ≥ n + L (here N = 512).
- **No wraparound:** for every |l| ≤ L, the circular cross-correlation at l equals c(l).
  For l ≥ 0, q's index t + l never wraps past N. For l < 0, the wrapped indices land in
  q's zero padding.
- **The bound:** c(l) = (1/N)·Σ_k Q_k·conj(G_k)·e^{2πikl/N}, so for all such l:

      c(l) ≤ (1/N)·Σ_k |Q_k||G_k|.

- **Compact form (32 values):**
  - keep |G_k| for the 31 lowest rfft bins;
  - for the rest, use the weighted Cauchy–Schwarz inequality, √(Σ w_k|Q_k|²)·√(Σ w_k|G_k|²);
  - the weights w_k are 1 for DC and Nyquist and 2 otherwise.

### Keogh-style

Let U[t] and Lo[t] be the maximum and minimum of q over indices t − L … t + L, with
q = 0 outside 0…n−1.
- **Why it holds:** for any |l| ≤ L, q[t+l] lies between Lo[t] and U[t]. So
  q[t+l]·g[t] ≤ max(U[t]·g[t], Lo[t]·g[t]), and therefore

      c(l) ≤ Σ_t max(U[t]·g[t], Lo[t]·g[t]).

- **Compact form (16 blocks × 2 values):** in each block, Σ U·g⁺ ≤ max(U)·Σg⁺ and
  Σ Lo·g⁻ ≤ max(−Lo)·Σ|g⁻|. Both hold whatever the sign of the coefficients.

### Combined

The minimum of the two upper bounds is also an upper bound.

### Not done, because the experiment stopped

- **Quantisation allowances:** a stored float16 summary needs rounding toward the safe
  side. Where a coefficient can be negative, as in the Keogh block bound, that means an
  explicit margin rather than simple rounding up.
- **Conservative floating-point comparison:** the pruning decision itself needs a
  margin.

## 4. Feasibility (`feasibility.py`, `results/feasibility.json`)

**Setup:**
- every window is a query once;
- top 10, source excluded;
- the threshold is the **true 10th-best distance**, which favours the bound (a real
  search starts with a worse threshold);
- a candidate counts as prunable only when its lower bound exceeds the threshold, so
  ties are kept;
- each bound was asserted to hold for every candidate in every query.

**Mean fraction of candidates prunable:**

| Bound | Same station | Unfiltered |
|---|---|---|
| Spectral, full spectrum | 0.21 | 0.46 |
| Keogh, full envelope | 0.38 | 0.45 |
| **Combined, full information** | 0.46 | **0.63** |
| Spectral, 32 values | 0.18 | 0.40 |
| Keogh, 32 values | 0.28 | 0.34 |
| **Combined, 32 + 32 values (128 bytes in binary16)** | 0.37 | **0.55** |

**Why the bounds are loose:** the combined full-information bound overstates the best
correlation by a median factor of **1.83**.
- The spectral bound ignores phase, and the Keogh bound smears the query over ±30 values.
- Normalised seismic envelopes are smooth bumps whose spectra and running ranges look
  alike, which is exactly the information these bounds keep.

**What this means for latency,** using the measured costs:

| | Same station | Unfiltered (1,825) | Model at 4,095 candidates |
|---|---|---|---|
| Combined, full information, free bound | 1.20× | 1.92× | 2.09× |
| Combined, 32 values, free bound | 1.16× | 1.73× | 1.84× |
| Combined, 32 values, bound at 1 µs per candidate | 1.12× | 1.55× | – |

The 4,095-candidate column extrapolates the measured costs linearly (3.87 ms fixed,
1.43 µs non-scoring and 11.3 µs scoring per candidate). It is a model, not a
measurement.

Only the full-information bound at the largest allowed gallery reaches 2×, and only
with an oracle threshold and a free bound. Storing full spectra as well as envelopes
would also break the storage gate: summary ≤ 10% of compressed raw, while the
envelopes alone are about 13% (12.9% in R1b).

## 5. What would change the answer

- **Much larger candidate sets** than the 4,096 default, for example a station-month
  of overlapping windows on a continuous recording. In the model, the speed-up
  approaches (c_s + c_f)/((1 − p)·c_s + c_f). With p = 0.55 that limit is about
  1.96× even with infinitely many candidates, an oracle threshold and a free bound.
- **A tighter bound that still covers every shift,** for example a per-block
  spectral bound with phase. Any candidate must first beat the 63% full-information
  figure above on this data.
- **Cheaper exact scoring** (about 11 µs per candidate today) is the larger lever.
  Bitwise identity rules out reordering the sums. A new representation version could
  allow FFT-based correlation or f32.

## Reproduce

```sh
# morlet_speed_after: the R1b 2022 windows in a fresh 0.3.1 database
# (evaluation/materialized/compact/sql_check.py loads them; any database with envcheck_2022 works)
.venv-seismic/bin/python -I evaluation/pruning/headroom.py morlet_speed_after evaluation/pruning/results/headroom.json
.venv-seismic/bin/python -I evaluation/pruning/feasibility.py morlet_speed_after evaluation/pruning/results/feasibility.json
```

Run on an Apple M5 Max with PostgreSQL 17.11 (pgrx) and the pg_morlet release build of
`main` at 1311bba. macOS Accelerate prints spurious matmul warnings; the bound
assertions passed and all values are finite.
