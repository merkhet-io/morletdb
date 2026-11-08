# Long exact reads (B3's R2): where the time goes, and parallel decoding

Exploratory, development set (B3's `B3_SET=dev`), one D2 load each through the I1 write path; not a frozen
measurement. Why: in B3.8, QuestDB read 128,000-sample intervals 13% faster at p50 (MorletDB 9% faster at p95;
no statement by B3's rule).

## Where an R2 read spends its time

Script [`../tsdb_b3/r2_attribution.py`](../tsdb_b3/r2_attribution.py), results [`r2_attribution_20261105T132518.json`](../tsdb_b3/results/dev/r2_attribution_20261105T132518.json);
image with SQL 0.3.4 (`morlet.verify_appends` branch build), 20 intervals × 50 passes, medians:

| Part | ms |
|---|---:|
| Round trip only (`SELECT 1`) | 0.34 |
| Server work without transfer (`octet_length` of the read, from the client) | 2.48 |
| of which decoding inside the server (all reads in one statement) | 1.99 |
| **Full read as B3 measures it** (binary bytea, `numpy.frombuffer`) | **5.61** |
| A stored 1,024,000-byte value of the same size, selected as binary | 4.00 |

Moving 1 MB of raw float64 from the container to the macOS client (Docker Desktop's network path) costs about
3.1 ms, more than half of the read; decoding about 2 ms.

## Option 1: decode long reads on worker threads (implemented)

Interval reads (`morlet_decode_interval`, `morlet_decode_interval_raw`, so `morlet_read_interval` and
`morlet_read_interval_raw`) of at least 8 chunks decode their chunks on up to `morlet.decode_threads` threads
(1..64, default up to 4; 1 = the backend's own thread), with the same worker rules as the encoder (only
`morlet_core` code, all signals blocked, errors raised in chunk order). Results are identical
(`#[pg_test]`s: 1, 4 and 64 threads, against the source samples; a corrupt chunk in a long read still fails;
invalid setting rejected).

Script [`../tsdb_b3/r2_decode_threads.py`](../tsdb_b3/r2_decode_threads.py), results [`r2_decode_threads_20261105T133004.json`](../tsdb_b3/results/dev/r2_decode_threads_20261105T133004.json);
same load, settings alternating per pass, every read checked against the source (Spotlight indexing ran
during the session):

| | 1 thread | 4 threads |
|---|---:|---:|
| R2 inside the server | 2.65 ms | **1.57 ms (−41%)** |
| R2 from the client, p50 / p95 | 7.45 / 10.13 ms | **6.31 / 9.55 ms (−15% / −6%)** |
| R1 (one or two chunks, below the threshold), from the client | 0.705 ms | 0.704 ms |

## Option 2: compressed transfer (implemented)

[Design](../../docs/compressed-transfer.md). `morlet_read_interval_encoded` sends the interval's stored chunks
unchanged, and the client decodes them with the same Rust codec (`crates/morlet-ffi`, loaded by the Python SDK with
`ctypes`), verifying every chunk's checksum.

Script [`../tsdb_b3/r2_transfer.py`](../tsdb_b3/r2_transfer.py), results
[`r2_transfer_20261106T090112.json`](../tsdb_b3/results/dev/r2_transfer_20261106T090112.json); image `morletdb:ct`
(branch `feat/compressed-transfer`), development set, one D2 load, B3's read intervals, 50 passes each with the
transfers alternating within every pass, reads from the same client process, every read checked bit for bit against
the source. Started at a 1-minute load average of 2.9, after builds (5- and 15-minute averages 6.6 and 8.0).

| Read | Raw, p50 / p95 | Encoded, 1 client thread | Encoded, 4 client threads | Bytes per read, raw / encoded |
|---|---:|---:|---:|---:|
| R1, 4,096 samples | **0.72 / 1.08 ms** | 0.77 / 1.03 ms | 0.77 / 1.00 ms | 32,768 / 9,770 |
| R2, 128,000 samples | 5.99 / 8.77 ms | 4.43 / 6.13 ms | **2.51 / 3.15 ms** | 1,024,000 / 153,640 |
| R3, 3 channels × 64,000 | 7.55 / 12.11 ms | 6.24 / 8.72 ms | **3.79 / 4.96 ms** | |

Server work without transfer for R2 falls from 2.25 to 0.66 ms (no decoding, only lookup and framing). A long read
is 2.4× faster at p50 and 2.8× at p95; the tail shrinks most, because 6.7× fewer bytes cross Docker's network path.
Short reads of one or two chunks gain nothing and lose about 0.05 ms at p50, so the SDK reads raw below 32,768
samples.

**Not a benchmark result.** One development load, same-process reads, a machine still settling after builds, and
no other database in the session. Whether this gives a statement against QuestDB is for a frozen B3 amendment after
a release, with the benchmark's MorletDB arm reading through this path.

## Earlier note: compressed transfer (option 2)

With decoding parallel, the transfer of raw float64 dominates. Sending the encoded chunks (about 1.3 bytes per
sample here instead of 8) and decoding them in the client with the same Rust codec would cut the bytes about
6×; it needs an SQL function that returns an interval's encoded chunks under the same visibility rules and a
native decoder for the Python client (plain C interface over `morlet-core`, loaded with `ctypes`, no new
dependency).
