# TOAST compression on PostgreSQL 19 (#280)

**Result.** With the pin, PostgreSQL 19rc1 stores the B3 benchmark set in exactly the same bytes as PostgreSQL 17.11.
Without it (lz4, PostgreSQL 19's new default), encoded chunks would differ by 0.016%, and envelopes not at all. The
legacy float8[] layout would shrink by 19%. Published storage figures were all measured with pglz, so they stay valid.

## Run

- **Data:** the 124 Paderborn acquisitions of B3 (`evaluation/tsdb_b3/workload.py`, every 20th of 2,480 files), all
  7 channels each. That is 868 recordings and 101,586,735 samples; the samples' SHA-256 starts with `1e13dd017c66` in every arm.
  B3's storage hints (`QUANTUM`) apply.
- **Per arm:** a fresh stream collection with envelopes (window 4096, hop 2048), plus the same samples in the legacy
  layout (float8[] chunks of 32,768 samples). Every recording was read back and compared bitwise; all were equal.
- **Script:** `measure.py`. Results: `results/pg17_pglz.json`, `results/pg19_pglz.json` and `results/pg19_lz4.json`.
- **Servers:** pgrx test builds of PostgreSQL 17.11 and 19rc1 with lz4 (macOS arm64; assertions enabled), each with
  pg_morlet 0.3.6 from this change. On 19rc1, `default_toast_compression` is `lz4`.
- **Scope:** storage only. Assertion-enabled builds on a loaded desktop (load 1.9–4.5) say nothing about speed.

## Storage

Stored bytes are `sum(pg_column_size(...))`: the bytes of the column values as stored, after TOAST compression.
Relation bytes are `pg_total_relation_size`, which includes the heap, TOAST and indexes.

| Column | Arm | Rows compressed | Stored bytes | Relation bytes |
|---|---|---:|---:|---:|
| `_chunks.encoded` | 17.11, pglz | 0 of 3,490 | 137,891,115 | 146,276,352 |
| | **19rc1, pglz (pinned)** | 0 of 3,490 | **137,891,115** | 146,284,544 |
| | 19rc1, lz4 | 198 of 3,490 | 137,869,322 (−0.016%) | 146,268,160 |
| `_envelopes.envelope` | 17.11, pglz | 0 of 48,525 | 40,033,125 | 53,223,424 |
| | **19rc1, pglz (pinned)** | 0 of 48,525 | **40,033,125** | 53,223,424 |
| | 19rc1, lz4 | 0 of 48,525 | 40,033,125 | 53,223,424 |
| legacy float8[] chunks | 17.11, pglz | 1,122 of 3,490 | 630,447,518 (78% of raw) | 658,178,048 |
| | **19rc1, pglz (pinned)** | 1,122 of 3,490 | **630,447,518** | 658,178,048 |
| | 19rc1, lz4 | 3,123 of 3,490 | 508,235,842 (63% of raw, −19%) | 529,719,296 |

- **Encoded chunks are already compressed** (1.36 B per sample here), so pglz never keeps a compressed copy:
  it needs a 25% saving. lz4 keeps any saving at all, which comes to a few bytes on 198 chunks.
  - With the pin, the stored bytes on 19rc1 equal those on 17.11 exactly.
  - The relation sizes differ by one 8 kB page, which is free-space map and visibility noise.
- **Envelopes** (1 + 2·410 bytes for a window of 4,096) stay below the TOAST threshold (about 2 kB), so the
  compression method never applies to them.
- **Legacy float8[] chunks** are where the method decides the size. lz4 compresses 3,123 of the 3,490 chunks, pglz
  1,122, and lz4 stores 19% less.
  - Without the pin, an upgrade to 19 would change the size of these collections for every new write.
  - Raw is 8 bytes per sample, 812,693,880 bytes.

## What this decides

- **The pin stays pglz.** It keeps every collection's storage the same across 17, 18 and 19, and keeps the published
  figures (B2, B3, B4) valid.
- For encoded chunks, the only layout new collections use, lz4 would make no difference worth having.
- **Legacy float8[] collections** would be 19% smaller with lz4. pg_morlet does not switch them: that would change
  storage without a measurement on that collection, and would break comparability with every figure published
  before.
  - An owner who wants the saving can run `ALTER TABLE <collection>_chunks ALTER COLUMN samples SET COMPRESSION lz4`.
  - It applies to new writes; existing rows keep their bytes until rewritten.
  - Do this after updating to 0.3.6, because the update, like `morlet_pin_toast_compression`, sets every bulk
    column back to pglz.
