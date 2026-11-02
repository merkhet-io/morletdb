# What the sparse lag grid can lose: a bound and how loose it is

A short note (verification track, B1). It derives a bound that needs only the query, compares it with
the loss measured on development data, and draws the conclusion. The bound holds but is one to two orders
of magnitude looser than what the grid actually loses here. It explains the choice of spacing but cannot
certify individual results.

Data: development data only (`morlet_r2`, 234,762 windows; E1's 100 development queries and their exact top
ten, 1,000 items). Script `grid_bound.py`, results `results.json`. No held-out data was read.

## Setting

Notation follows the paper (Section 3):
- `q` is the query envelope and `g` a stored envelope, both z-scored, with `n = 410` values;
- `s_ℓ` is the query shifted by `ℓ` and zero-padded at the edge;
- `c(ℓ) = ⟨s_ℓ, g⟩ / n` is the score at lag `ℓ`, and the distance is `D = 1 − max_{|ℓ|≤L} c(ℓ)` with `L = 30`;
- the probe grid is `Λ = {−30, −25, …, 30}` (13 lags, spacing `h = 5`), so every lag lies within
  `⌊h/2⌋ = 2` of a grid lag.

The grid enters through approximation (a) in the paper. A window is found by the probes only if one of
its grid scores `c(λ)` ranks it in a probe's top `c`. The question here is how far `max_λ c(λ)` can fall
below `max_ℓ c(ℓ)`.

## The bound

For any lag `ℓ` and grid lag `λ`, by Cauchy–Schwarz,

    |c(ℓ) − c(λ)| = |⟨s_ℓ − s_λ, g⟩| / n ≤ ‖s_ℓ − s_λ‖ · ‖g‖ / n.

With `ℓ*` a window's best lag and `λ(ℓ)` the grid lag that minimizes `‖s_ℓ − s_λ‖` among those within two
lags of `ℓ`:

    max_λ c(λ) ≥ c(λ(ℓ*)) ≥ c(ℓ*) − b(ℓ*),     b(ℓ) = ‖s_ℓ − s_λ(ℓ)‖ · G / n,

and, without knowing `ℓ*`,

    max_ℓ c(ℓ) − max_λ c(λ) ≤ Δ(q) = max_{|ℓ|≤L} b(ℓ).

`Δ(q)` depends only on the query and on `G`, an upper bound on `‖g‖`, so it can be computed at query time
for every lag pair.

Assumptions and details:
- **‖g‖.** Stored envelopes are float16. The scorer decodes them to float64 and z-scores them again with
  population statistics, so `Σ g² = n` up to float64 rounding and `‖g‖ = √n`. The float16 rounding changes
  which `g` is scored, not its norm. Measured: the largest norm in the gallery is √n + 1.4e-13. Constant
  envelopes decode to zeros (norm 0), and the bound holds trivially for them.
- **Zero padding.** `s_ℓ` loses up to `|ℓ|` query values at the edge. `‖s_ℓ − s_λ‖` is computed on the padded
  vectors as the scorer uses them, so the edges are included exactly, not approximated.
- **Float16 probes and the index.** The bound concerns exact grid scores, approximation (a) only. Float16
  probe vectors and HNSW are approximations (c) and (b) and are not covered. Re-ranking uses the
  full-precision query and stored envelope, so returned distances are exact whatever the candidates were.
- **Arithmetic.** The bound is a statement about real numbers. The scorer's float64 rounding (at most 1.6e-15
  in E3's checks) is negligible against the values below.

## Relation to the query's autocorrelation

For a z-scored query, away from the edges, `‖s_ℓ − s_λ‖² ≈ 2n(1 − a(d))` with `d = |ℓ − λ|` and `a` the
query's own biased autocorrelation at offset `d`. The zero-padded boundary terms are at most `|ℓ| + |λ|`
values. With `‖g‖ = √n`,

    b(ℓ) ≈ √(2 (1 − a(d))),        Δ(q) ≈ √(2 (1 − a(⌊h/2⌋)))   (worst lag, smoothest-case query).

The development envelopes have a mean autocorrelation of 0.990, 0.973 and 0.951 at offsets 1, 2 and 3
(`research/paper/analysis/grid_spacing.json`). That predicts a bound of about 0.14, 0.23 and 0.31 for
spacings 3, 5 and 7. These numbers can be computed for any new signal from a sample of its envelopes,
without running a retrieval experiment. They are what the bound guarantees, not what to expect: see below.

## Comparison with the measured loss (development data)

1,000 items: the exact top ten of each of E1's 100 development queries. For each item, `c(ℓ)` was computed
at all 61 lags against its stored envelope.

| Quantity | min | p5 | median | p95 | max |
|---|---|---|---|---|---|
| Observed loss `c(ℓ*) − max_λ c(λ)` | 0 | 0 | 0.0008 | 0.017 | 0.071 |
| Kept fraction `max_λ c(λ) / c(ℓ*)` | 0.913 | 0.981 | 0.999 | 1 | 1 |
| Item bound `b(ℓ*)`, items off the grid | 0.071 | 0.090 | 0.207 | 0.397 | 0.582 |
| Query bound `Δ(q)`, 100 queries | 0.118 | 0.160 | 0.268 | 0.439 | 0.582 |
| Bound / observed loss, items off the grid | 4.9 | 13.7 | 65 | 683 | 12,283 |

- **The bound held for every item.**
- **On-grid items lose nothing.** 393 items have their best lag on a grid point, so their loss is exactly 0.
- **The bound is loose.** For the other 607 items it exceeds the observed loss by a factor of 65 at the
  median and never by less than 4.9. The query bound's median of 0.27 matches the autocorrelation estimate
  for spacing 5 (0.23 for the mean query).
- **The kept fractions reproduce the paper's figures** (median 0.999, 5th percentile 0.98). The paper's
  minimum of 0.84 comes from its 1,000 items per database including held-out data; the development minimum
  here is 0.91.

The slack comes from Cauchy–Schwarz itself. The bound assumes `g` points along `s_ℓ − s_λ`, the
difference of two nearby shifts of a smooth query. A stored envelope that matches the query well is
nearly orthogonal to that difference, so the inner product is far smaller than the product of the norms.

## Consequences

1. **For choosing a spacing:** `√(2(1 − a(⌊h/2⌋)))` is a safe, signal-specific upper bound on correlation
   loss that needs only a sample of envelopes. Here it is about 0.23 for spacing 5 against an observed 95th
   percentile of 0.017. It can rule a spacing in when the bound is already acceptable, but being this loose
   it will often rule out spacings that work. A measurement on the signal (as in the paper's ablation)
   remains the deciding evidence.
2. **A per-query exactness certificate** (exact top-`c` per probe; certified when `max_λ τ_λ + Δ(q)` is below
   the tenth score) is not useful with this bound. Here, consecutive top-ten scores differ by 0.002 at the median
   (95th percentile 0.013), and the whole top ten spans 0.027 at the median; the bound is 0.12–0.58. It would certify almost no query, so it was not implemented (as agreed, B2 is skipped).
   A tighter bound would need information about `g`, for example its own autocorrelation stored per window.
   That changes storage and is out of scope.
3. **What it does not say:** nothing about recall. Recall depends on the rank inside a probe (approximation
   b) and on HNSW (c). An item can keep 99.9% of its correlation and still fall outside a probe's 50
   candidates when scores are close, as the single held-out PX-13 miss did.

Whether this belongs in the paper as a proposition (the bound plus the autocorrelation estimate, with the
measured slack) is for the owner to decide. It would replace the empirical "why spacing five sufficed"
paragraph's informal reasoning with a stated guarantee, and keep the same conclusion: the guarantee is
weak and the evidence is empirical.
