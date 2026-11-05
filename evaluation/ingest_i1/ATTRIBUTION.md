# I1 step 0: where single-connection ingestion time goes

Issue [#233](https://github.com/merkhet-io/morletdb/issues/233). **Exploratory, development set only** (B3's
`B3_SET=dev`, 124 acquisitions, 101,453,304 samples; disjoint from the benchmark set, almost the same size).
Not a frozen measurement and not a comparison: it decides what to build. Image
`ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1` (SQL 0.3.3), D2, B3's `morletdb-033` client path (raw append,
pipelined, one chunk per call, 16 chunks per transaction), same container limits as B3 (8 CPU, 16 GB),
2026-11-05, 1-minute load average 1.7–2.3 before the runs.

Scripts: [`attribute.py`](attribute.py) (server pieces), [`client_sweep.py`](client_sweep.py) (importer
pacing). Raw results: [`results/`](results/).

## The load, end to end

One D2 load through the B3 client path took **16.1–16.8 s (6.06–6.30 M samples/s)**, with the server backend
at 75–79% of one core. B3.8 measured 6.30 M on the benchmark set, so the development set behaves the same.

## Server side: every chunk appended again inside the server (no client, no network)

Every chunk of the load was appended once more through `morlet_append_chunk_raw` in one `DO` block; each
piece of that path was then timed on its own as one statement over all 25,102 chunks. Medians of three.
The replay produced byte-identical chunks.

| Piece | Seconds | Share | µs / chunk |
|---|---:|---:|---:|
| **Full append path (server only)** | **11.70** | 100% | 466 |
| `morlet_encode_chunk` (codec) | 5.43 | 46% | 216 |
| PL/pgSQL residual: interpretation, five dynamic `EXECUTE format(...)` parsed and planned per chunk, row lock, replay lookup | 2.32 | 20% | 93 |
| `morlet_valid_encoded_chunk` (the CHECK constraint decodes every chunk again) | 1.39 | 12% | 55 |
| `morlet_float8_from_le_bytes` (bytes → `float8[]` before encoding) | 0.96 | 8% | 38 |
| chunk row insert: heap, primary key, foreign key, WAL | 0.73 | 6% | 29 |
| `morlet_stream_relations` (catalog lookup per chunk) | 0.33 | 3% | 13 |
| `morlet_chunk_step` (checkpoint, no features) | 0.28 | 2% | 11 |
| checkpoint `UPDATE` per chunk | 0.26 | 2% | 10 |

The residual is the full path minus the measured pieces, so it also carries their measurement error.

## Client side: the importer's pacing

The importer drains its pipeline at every commit, so the server waits while the client prepares the next
transaction, and it opens one connection per acquisition. Varying only chunks per transaction (two fresh
loads each; every load checked for the full sample count):

| Chunks per transaction | Wall s (two loads) | Samples/s (median) |
|---:|---|---:|
| 16 (B3's arm) | 16.17, 16.10 | 6.29 M |
| 64 | 14.23, 14.35 | 7.10 M |
| 256 (≈ one transaction per acquisition) | 13.37, 13.46 | **7.56 M (+20%)** |

Opening a connection takes 6.3 ms (p50), about 0.8 s over 124 acquisitions. At 256 chunks per transaction the
load is 1.7 s above the server-only path; the connections explain about half of that.

## What this changes in the plan

1. **Importer first (no engine change):** one transaction per acquisition (or larger `commit_chunks`) and one
   connection for the whole import. About +20%, from 16.1 s to about 12.6 s.
2. **Native append path:** removes the PL/pgSQL residual (2.3 s) and the per-chunk relation lookup (0.3 s), and
   encodes directly from the bytes instead of building a `float8[]` first (up to 1.0 s). The full decode in the
   CHECK constraint (1.4 s) needs a design that keeps the guarantee for direct `INSERT`s. Together up to about
   5 s of the 11.7 s server path.
3. **Parallel encoding within a batch:** the codec is the largest single cost (5.4 s, 46%).

From these pieces (an estimate, not a measurement): steps 1–2 bring the server path to about 7 s, and the load
would then be bounded by whichever of client and server is slower. The client's own per-chunk cost is the next
thing to measure. QuestDB needed 10.5 s for the benchmark set (B3.8).
