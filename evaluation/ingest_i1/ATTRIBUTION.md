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

## Step 1: the importer's pacing (implemented)

The importer's default became 256 chunks per transaction (was 16), and `import_files(..., conn=...)` accepts a
caller-owned connection, which B3's adapter can reuse for all acquisitions (variant `reuse_connection`, off in
every frozen arm). Script [`pacing.py`](pacing.py), results
[`results/pacing_20261105T074353.json`](results/pacing_20261105T074353.json); development set, fresh server per
load, full sample count checked after each load, and the chunk digest was identical across all arms.

| Arm | D2 wall s (two loads) | D2 samples/s | D1 wall s (two loads) | D1 samples/s |
|---|---|---:|---|---:|
| old: 16 per transaction, connection per acquisition | 16.29, 16.31 | 6.22 M | 16.28, 16.30 | 6.23 M |
| 256 per transaction | 13.80, 13.76 | 7.36 M | | |
| **new: 256 per transaction, one connection** | 12.22, 12.61 | **8.17 M (+31%)** | 12.35, 12.36 | **8.21 M (+32%)** |
| searchable (features), old / new, one load each | 61.9 / 58.2 | 1.64 / 1.74 M (+6%) | | |

With the old pacing the client waited 12.0 s at commits, so its own work was at most 4.3 s of the 16.3 s
load. With the new pacing the load (12.4 s) is within 0.7 s of the server-only append path (11.7 s, above):
**ingestion is now bound by the server**, and the server path is what steps 2–3 shrink. The importer's
`commit_wait_s` only counts waiting at commits; with large transactions the client waits inside sends
instead (backpressure), so it is not a measure of client cost there.

## Step 2: batched raw appends (implemented, SQL 0.3.4)

`morlet_append_chunks_raw(collection, recording_id, start_sample, samples bytea, chunk_size)` takes a run of
consecutive chunks as little-endian float64 bytes (up to 262,144 samples). When the run starts at the
recording's next sample offset, one Rust call (`morlet_chunk_batch`) decodes the bytes, cuts the chunks,
advances the checkpoint, encodes and summarizes, and one statement per table stores the result; any other run
(a retry, an overlap, a gap, a float8[] collection, a recording that is not visible) goes through
`morlet_append_chunk_raw` chunk by chunk, so retries, conflicts and errors behave as before. The importer
batches automatically (`rpc_chunks=0`: up to 64 chunks per call when the server has the function). The table's
CHECK constraint is unchanged and still decodes every chunk.

Script [`batch.py`](batch.py), results [`results/batch_20261105T084529.json`](results/batch_20261105T084529.json);
image `morletdb:i1-dev` (this branch), development set, both arms with step 1's pacing, alternating, B3's full
correctness check after every load (all passed). **Machine:** a browser renderer used about 1.3 cores during
this run (1-minute load average 4.2–5.4, against about 2 in the earlier runs); the arms alternated, so their
comparison holds, but absolute rates are likely somewhat low. The first `step1` load (16.5 s) is an outlier
against its repetition (12.3 s) and step 1's own runs (12.2–12.6 s).

| Arm | Calls | D2 wall s (two loads) | D2 samples/s | D1 wall s (two loads) | D1 samples/s |
|---|---:|---|---:|---|---:|
| step1: one call per chunk | 25,102 | 16.51, 12.28 | 7.20 M | 12.99, 12.34 | 8.02 M |
| **batch: up to 64 chunks per call** | 877 | 10.18, 10.24 | **9.94 M** | 10.31, 10.48 | **9.76 M** |
| searchable (features), one load each | | 59.5 / 52.5 | 1.70 / 1.93 M (+13%) | | |

Against step 1's quiet-machine rate (8.17 M D2, 8.21 M D1) batching adds about 22% (D2) and 19% (D1); against
the original B3 path (6.22 M) the development-set load is now about 1.6× faster. **Stored data is identical**:
chunk, segment and checkpoint digests are equal across both arms, in base and searchable loads, and the base
chunks equal step 1's loads on the released image.

The server backend now runs at 61–77% of one core during a load, so the remaining time is no longer only the
server's. Next: where the 10.2 s go now (client and server), then parallel encoding. No comparison with QuestDB
is stated from development-set runs; that needs a frozen same-session stage on the benchmark set.

## After step 2: where a batched load spends its time

Script [`attribute_batch.py`](attribute_batch.py), results
[`results/attribution_batch_20261105T094904.json`](results/attribution_batch_20261105T094904.json); image
`morletdb:i1-dev` (step 2), development set, D2, machine quiet (the browser tab of step 2's run was closed;
1-minute load average 2.0 before the session, my own previous run winding down at the start). The load took
**10.25 s (9.90 M samples/s, 877 calls)**, as in step 2; every run of 64 chunks was then appended again inside
the server, and the pieces timed alone (medians of three; the replay's chunks were byte-identical).

| Piece | Seconds | Share of the load |
|---|---:|---:|
| Server-only batched append path | 8.04 | 78% |
| of which encoding (`morlet_core::rawcodec::encode`, inside `morlet_chunk_batch`) | 5.50 | **54%** |
| of which the rest of `morlet_chunk_batch` (bytes to values, cutting, checkpoint) | 0.51 | 5% |
| of which the CHECK constraint (full decode of each chunk) | 1.38 | 13% |
| of which the row inserts (heap, keys, foreign key, WAL) | 0.69 | 7% |
| of which PL/pgSQL and statement overhead (remainder) | ≈ 0 | |
| Client and network (load minus server-only path) | 2.21 | 22% |

Step 2 removed the per-statement overhead entirely. **Encoding is now more than half the load and runs on one
core**: parallel encoding within a batch (step 3) is the next lever, then the CHECK constraint's decode (an
owner decision, #233), then the client.

## Step 3: encoding on worker threads (implemented, SQL 0.3.4)

`morlet_chunk_batch` encodes the chunks of a batch on up to `morlet.encode_threads` threads (1..64; default up
to 4, at most the CPUs available; 1 encodes in the backend's own thread). The workers run only `morlet_core`
code, with every signal blocked; validation, checkpoint and errors stay in the backend's thread, in chunk order,
so output and error messages do not depend on the thread count (`#[pg_test]`s with 1, 4 and the default, grid
and off-grid chunks; first invalid chunk; invalid setting). A local stress run of 200 full batches on 8 threads
showed no problem in the server log.

Script [`threads.py`](threads.py), results [`results/threads_20261105T095950.json`](results/threads_20261105T095950.json);
image `morletdb:i1-dev3`, development set, step 2's client settings, arms alternating, B3's full correctness
check after every load (all passed); chunk, segment and checkpoint digests equal step 2's in base and searchable
loads. 1-minute load average 4.2–4.6 during the session, from the loads themselves (the top processes before
the run were WindowServer 31% and the Docker VM 16%).

| `morlet.encode_threads` | D2 wall s (two loads) | D2 samples/s | D1 wall s (two loads) | D1 samples/s | server CPU (D2) |
|---:|---|---:|---|---:|---:|
| 1 (as step 2) | 10.61, 10.56 | 9.58 M | 10.64, 10.69 | 9.51 M | 70% |
| 2 | 8.38, 8.22 | 12.22 M | | | 81% |
| **4 (default)** | 7.55, 7.79 | **13.23 M (+38%)** | 7.83, 8.88 | **12.19 M (+28%)** | 99% |
| 8 | 7.36, 7.42 | 13.72 M | | | 104% |
| searchable, 1 / 4 (one load each) | 50.9 / 48.1 | 1.99 / 2.11 M (+6%) | | | |

More than 4 threads adds little: encoding then takes about a quarter of its former 5.5 s, and the load is bound
by the serial rest (the CHECK constraint's decode 1.4 s, inserts 0.7 s, the rest of the batch 0.5 s) and the
client (2.2 s). Searchable loads gain little because feature extraction, not encoding, dominates them.

**Development-set summary of I1 so far (D2, single connection):** 6.22 M (before) → 8.17 M (step 1, importer
pacing) → 9.94 M (step 2, batched appends) → 13.2 M samples/s (step 3, 4 encoding threads), about 2.1×, with
stored data byte-identical throughout.

## Step 4: `morlet.verify_appends` and the CHECK constraint (implemented, SQL 0.3.4)

The chunk tables' CHECK constraint decoded every inserted chunk in full (1.4 s of a load, serial). Now the batched
append path remembers the exact bytes it just encoded (with their sample counts), and the constraint skips its
decode for an insert of exactly those bytes; every other insert or update (a direct `INSERT`, a corrupted or
re-labelled copy, the per-chunk path) is still decoded in full. `morlet.verify_appends` (on/off, default on)
decides what the append path guarantees in exchange: **on** decodes each chunk again on the encoding threads and
compares it bit for bit with its input samples, so every stored chunk is proven lossless, which the constraint
never checked (it proved only that a chunk decodes to the right number of samples); **off** relies on the codec's
tests. Chunks are identical either way (`#[pg_test]`s: identical chunks on/off; corrupted copy rejected; copy
under another sample count rejected; invalid setting rejected; smoke test of an identical copy, decoded and
accepted).

Script [`verify.py`](verify.py), results [`results/verify_20261105T124730.json`](results/verify_20261105T124730.json);
development set, step 2's client settings, default encoding threads, arms alternating, B3's full correctness check
after every load (all passed), chunk digests identical across all 15 loads. 1-minute load average 2.6 at the
start; a LaTeX build ran during part of the session.

| Arm | D2 wall s (three loads) | D2 samples/s | D1 wall s (two loads) | D1 samples/s |
|---|---|---:|---|---:|
| CHECK decodes every chunk (`morletdb:b310`) | 7.52, 7.42, 7.43 | 13.66 M | 7.63, 7.64 | 13.28 M |
| **`verify_appends = on`** (default) | 7.13, 7.09, 7.17 | **14.22 M (+4%)** | 7.29, 7.28 | **13.93 M (+5%)** |
| `verify_appends = off` | 6.98, 6.95, 6.98 | 14.54 M (+6%) | 7.18, 7.10 | 14.21 M (+7%) |

The default therefore gives a stronger guarantee than before (lossless, not only decodable) and is still faster,
because the round trip runs on the encoding threads while the constraint's decode ran in the backend's thread.
