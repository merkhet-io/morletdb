# SS1 results: a single halfvec table is exact but slows exact search — not adopted as designed

Run once per database on 2026-10-31 as frozen in [SS1_PROTOCOL.md](SS1_PROTOCOL.md) (1942b83);
harness 69b4765 plus two fixes to the harness's own setup (server-side cursor inside a
transaction; a separate connection for COPY), both before any measurement completed.
Development `morlet_r2` (234,762 windows) and `morlet_bl1c` (234,622); 100 queries each.

| Pre-registered condition | morlet_r2 | morlet_bl1c | Met |
|---|---|---|---|
| 1. stored float16 bits identical, every window | 0 mismatching windows | 0 | yes |
| 2. distances bitwise equal (20 queries × all windows + 80 × top 200) | 0 of 4,695,240 + 16,000 | 0 of 4,692,440 + 16,000 | yes |
| 3. exact top 10 identical to reference (order, distances across containers) | 100 / 100 | 100 / 100 | yes |
| 4. probe recall change ≤ 0.005 | 0.998 → 0.998 | 0.976 → 0.977 | yes |
| 5. exact-scan cost per window +≤ 10% | **+23%** (p50 953 → 1,174 ms) | **+23%** (918 → 1,134 ms) | **no** |
| 6. storage saved ≥ envelope table − 5% | 250 MB saved (needed ≥ 212) | 250 MB (≥ 212) | yes |

**Verdict: keep the two tables in this form.** Condition 5 fails.

- **Cause: execution, not representation.** The values, distances and rankings are identical
  by construction and in measurement. The cost is calling pgvector's `halfvec_send` per row
  (a function call, a palloc and a byte-swapped copy) before the Rust scorer, on every
  candidate of every exact scan. Probe search on the single table was also slower in this
  harness (p50 146 vs 77 ms on R2), for the same reason in its re-rank.
- **Bytes (R2):** envelopes 223.3 MB, current probe table 250.1 MB, single table 223.6 MB,
  inner-product index 274.8 MB in both designs.
- Exact parallel-scan latencies here (≈ 0.9–1.2 s) ran on a busy machine and are only
  compared within the run.

## What the diagnosis suggests (tested as SS1b, below the line in the protocol)

The index, not the table, needs `halfvec`: HNSW keeps its own copy of each vector in its
pages. An **expression index on the existing envelope table** would remove the second table
and keep the exact path reading `bytea` unchanged.
