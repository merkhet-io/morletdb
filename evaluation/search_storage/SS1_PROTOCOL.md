# SS1: one stored envelope representation for exact and probe search

Status: **FROZEN** in the commit that adds this file, before any SS1 measurement.

## Hypothesis and mechanism

With probe search enabled, every window is stored twice: as `bytea` (version byte + the
float16 values the exact scorer decodes) and as `halfvec` (float16 of the decoded,
re-z-scored values, for the index). The two differ in 1.7% of values by one float16 step
(inventory). If the `halfvec` column instead holds **exactly the stored float16 bits**, then:
- the exact scorer can read it (through pgvector's binary send format, parsed in Rust,
  then the same decode z-score) and return **bitwise-identical distances**, because its
  inputs are the same float16 values;
- the probe index works on those values instead of their re-z-scored roundings, which can
  move probe recall only marginally;
- one table per window size can be dropped: about 951 B per window, 0.60× (R2) or 0.43×
  (BL1-C) of the raw chunk table.

The representation (`xc-envelope-f16-v1`) is unchanged; only its container changes, so no
new representation version is introduced. If any distance or exact ranking changes, the
hypothesis is false and the change is not made.

## Prototype (smallest viable)

- `morlet_envelope_distance_send(query float8[], halfvec_send bytea, max_lag int)`: same
  semantics as `morlet_envelope_distance`, parsing pgvector's `halfvec_send` layout
  (int16 dim, int16 unused, big-endian float16 values). It takes `bytea`, so pg_morlet still
  has no compile-time dependency on pgvector.
- A probe table built from the envelope payload bits (`halfvec` of the float16 values
  exactly), with the same inner-product HNSW index settings.
- Scratch databases only; no product SQL is changed in this stage.

## Data

Development: R2's database (`morlet_r2`, 234,762 windows) and its 100 queries.
Equivalence checks also on BL1-C's database (`morlet_bl1c`, 234,622 windows, its 100
queries). Both are consumed; SS1 is an equivalence and cost study, not a new confirmation.

## Measures

1. **Value identity:** for every window, the parsed `halfvec_send` float16 values equal the
   `bytea` payload bit for bit (all windows, both databases).
2. **Distance identity:** for all 100 queries × their exact top 200 candidates, and for 20
   queries × every window, `morlet_envelope_distance_send` equals `morlet_envelope_distance`
   bitwise.
3. **Exact search identity:** the exact top 10 computed from the single table equals today's
   lists (order and distances) for all 100 queries per database.
4. **Probe recall:** probe search (13 × 50, same settings as the product) over the new probe
   table, recall@10 against exact, both databases; compared with the current probe table.
5. **Latency:** exact scan cost per window reading `halfvec_send` vs `bytea` (p50/p95 over the
   100 queries, same plan shape); probe search p50/p95.
6. **Bytes:** relation sizes before and after (envelope table, probe table, index); bytes per
   window; relative to raw.
7. **Build cost:** time to build the probe table and index from envelopes.

## Decision (pre-registered)

**Adopt the single representation in the product** if:
- measures 1–3 are exact (no exception);
- probe recall changes by no more than 0.005 on either database;
- exact-scan cost per window grows by no more than 10%;
- and storage falls by at least the envelope table's size minus 5%.
Otherwise report which condition failed and keep the two tables.

## Not in SS1

Quantized or compact keys (SS2) and filtered retrieval (SS3) come later under their own
protocols. No change to raw storage or to the search contract.
