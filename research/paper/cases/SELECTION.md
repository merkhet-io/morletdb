# Illustrative cases: selection rule (fixed before the figure is made)

Committed before any case figure exists. Cases are illustrations of the distribution in E1 (held-out data,
`evaluation/consolidated/results/e1_heldout.json`); they do not replace it, and no domain label (same event,
same fault) is attached to any window.

**Case A — zero-lag misses, probes find.** The held-out query with the largest recall gain of the product
probe function (PR) over the zero-lag HNSW arm with 1000 candidates (Z-1000); ties broken by the order of the
query file. Shown item: among the query's exact top ten, the intervals that PR returns and Z-1000 does not; of
these, the one with the largest absolute best lag; ties broken by exact rank.

**Case B — probes miss.** The held-out query with the lowest PR recall; ties broken by the order of the query
file. Shown item: the best-ranked exact interval that PR does not return.

For every shown interval: recording identifier, sample interval, distance D from the product scorer, best lag
ℓ* (envelope values and samples), exact rank, and which arms returned it.

**Alignment convention.** The scorer is c(ℓ) = Σ_t q[t+ℓ]·g[t] with zero padding, so at the best lag the stored
envelope g[t] is compared with the query envelope at q[t+ℓ*]. The figure plots g against q shifted by ℓ*
(zero-padded), both z-scored exactly as the scorer does, and checks that 1 − max_ℓ c(ℓ)/n equals the product
distance. Raw samples are shown in a separate row, unaligned and unnormalized, in counts.
