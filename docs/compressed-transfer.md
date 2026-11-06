# Compressed transfer: exact reads decoded in the client

Status: released in 0.3.7-alpha.1 (SQL 0.3.5) and measured in B3.15: long reads (R2) 2.6× faster than QuestDB at
p50 and p95, and 2.4× faster than the same release with raw transfer
([results](../evaluation/tsdb_b3/RESULTS.md#amendment-b315-the-released-037-alpha1-with-compressed-transfer-against-questdb)).
Short reads are below the threshold and stay raw, but were 1.1–1.4× slower in that arm; the cause is not yet established.
Date: 2026-11-06.

## Why

Long exact reads are the one read measure where MorletDB has no statement against QuestDB. In B3.14 a read of
128,000 samples was 1.03× faster at p50 and 1.17× at p95; B3's rule needs at least 10% at both. Parallel decode
made the median 17% faster than the release, but did not move the tail.

The attribution on the development set ([`evaluation/reads_r2/ATTRIBUTION.md`](../evaluation/reads_r2/ATTRIBUTION.md))
says where the rest goes. Of a 5.6 ms read, about 2 ms is decoding in the server and about 3.1 ms is moving
1,024,000 bytes of raw float64 from the server to the client. The stored chunks of the same interval are about
1.3 bytes per sample, so the same read sent still encoded is about 6× fewer bytes.

The idea: the server returns the interval's encoded chunks, unchanged, and the client decodes them with the same
Rust codec. The server stops decoding long reads, and the network carries the compressed bytes.

## Goal and scope

- Long exact reads through the Python SDK return the same float64 samples, bit for bit, in less time, with the
  same visibility rules and the same failure on incomplete or corrupt data.
- The existing functions (`morlet_read_interval`, `morlet_read_interval_raw`, `morlet_read_acquisition_raw`)
  stay as they are. Compressed transfer is an additional path that the SDK uses when it can, and it falls back to
  `morlet_read_interval_raw` whenever it cannot.
- Not in scope: a new chunk format, changes to how chunks are stored, lossy transfer, other client languages
  (the C interface makes them possible later), and similarity search or statistics (they already return small
  results).

## Design

### 1. SQL: one function returning an interval's encoded chunks

```sql
morlet_read_interval_encoded(collection regclass, source_recording_id text,
                             interval_start bigint, interval_end bigint,
                             max_chunk_version int DEFAULT 3) RETURNS bytea
```

`STABLE STRICT SECURITY INVOKER`, beside `morlet_read_interval_raw` in `streams.sql`, with the same checks in the
same order:

1. The recording is looked up under row-level security; a missing or hidden recording fails with the same
   "not visible" error, and `interval_end` beyond the checkpoint's end fails as it does today.
2. The chunk lookup is the same statement (`start_sample < end AND start_sample > start - 32768 AND
   start_sample + sample_count > start`), on `<c>_chunks` itself, so the policy `morlet_follows_recording` applies
   and a hidden chunk shows up as missing coverage, never as data.
3. **Coverage is checked without decoding.** The sum of each chunk's `sample_count`, clipped to the interval, must
   equal `interval_end - interval_start`; otherwise `morlet_raise_incomplete` fails exactly as the raw read does,
   including the message naming a compacted episode.
4. **Version check, without failing.** If any chunk's first byte (`get_byte(encoded, 0)`) is above
   `max_chunk_version`, the server decodes the interval itself, as the raw read does, and returns the samples in a
   raw frame (below). A client never receives a chunk version it cannot decode, and no statement fails, which
   matters because the SDK never leaves a failed statement in the caller's transaction.
5. Legacy collections that store `float8[]` (`relations.encoded = false`) are not read this way: the SDK already
   looks up the storage layout once per collection (`_raw_reads`) and uses the existing read for them.

It returns one `bytea` (one value, one round trip, as the raw read does), framed in Rust by a small function
`morlet_frame_chunks(encoded bytea[], starts bigint[], interval_start, interval_end)`:

```
magic "MTX1" | interval_start i64 | interval_end i64 | n u32 |
n × ( start_sample i64 | byte_length u32 | chunk bytes, unchanged ) | crc32c u32 of the frame
```

A raw frame has the magic `"MTR1"` and carries the interval's float64 samples instead of chunks, for the version
case above. All integers little-endian. The chunk bytes are the stored bytes; each still carries its own CRC-32C, which the
client verifies before any sample is used, as the server does today. The frame's own CRC covers the framing. The
server does no decoding at all.

**Edge chunks are sent whole**, and the client trims them to the interval. So a read can carry up to 32,767
samples on each side beyond the requested interval, from the same recording. Row-level security in MorletDB
works per recording, not per sample, and anyone with `SELECT` on `<c>_chunks` can already read these bytes, so
this exposes nothing a reader could not see. It is stated in `docs/access-control.md` all the same. Trimming in
the server instead would mean decoding and re-encoding the edge chunks, which costs more than it saves.

### 2. Client: a C interface over `morlet-core`

A new workspace crate `crates/morlet-ffi` (`crate-type = ["cdylib"]`, depending only on `morlet-core`) exports
one function:

```c
int32_t morlet_decode_frame(const uint8_t *frame, size_t frame_len,
                            int64_t interval_start, int64_t interval_end,
                            double *out, size_t out_len, uint32_t threads,
                            int64_t *error_sample);
uint32_t morlet_ffi_abi_version(void);        /* 1 */
uint32_t morlet_ffi_max_chunk_version(void);  /* 3 */
```

It accepts both frame kinds. It checks the frame (magic, CRC, ascending and non-overlapping chunks, lengths within the frame), verifies and
decodes every chunk with `rawcodec::decode`, trims the edge chunks, checks that the trimmed samples cover the
interval exactly, and writes them straight into the caller's buffer (a NumPy array, so there is no extra copy).
With `threads > 1` and at least 8 chunks, it decodes on scoped threads, with the rules the server already uses
(`on_threads`). It returns 0 on success or a negative error code (-1 arguments, -2 corrupt frame, -3 corrupt chunk with its start
sample in `*error_sample`, -4 coverage mismatch, -5 internal error). The frame format and its checks live in
`morlet_core::transfer`, shared by the server (which builds frames) and the library. It never panics across the boundary (`catch_unwind`), and it allocates nothing beyond one
chunk's samples per thread.

The Python SDK loads it with `ctypes` from the standard library, so the SDK gains no new Python dependency.

### 3. The SDK path and fallback

`Client.read` (and `read_ref`) does this:

1. If the native library loads and reports a supported chunk version, call `morlet_read_interval_encoded` with
   that version, binary and prepared, allocate the output array, and call `morlet_decode_frame`.
2. If the library is missing, the server has no such function (an older extension, checked once with
   `to_regprocedure`, as the SDK already does), or the collection stores `float8[]` chunks, use the existing read
   as today. No path probes by failing.
3. A decode error in the client is raised as the same `DatabaseError` a server-side checksum failure gives today,
   with the chunk's start sample. It is never retried as a raw read, because corrupt stored data must not be
   hidden.

An environment variable (`MORLETDB_TRANSFER=raw|encoded|auto`, default `auto`) forces either path, for tests and
benchmarks.

### 4. Distribution

The SDK is pure Python today. With the native library it needs platform wheels. The proposal is that CI builds
`morlet-ffi` for Linux x86-64 and arm64 and macOS arm64, puts it in the wheel, and keeps a pure-Python
`py3-none-any` wheel that always uses the raw read. Nothing breaks where no native wheel exists; it is only
slower.

## Correctness and verification

- **Golden chunks:** the 16 frozen chunks in `testdata/rawcodec/` are decoded through the C interface and compared
  bit for bit with their `.f64` files (a Python test using `ctypes`, and a Rust test of the exported function).
- **Differential:** for every collection in the pg tests, encoded and raw reads of random intervals (including
  intervals inside one chunk, across chunk edges, and the whole recording) must return identical bits.
- **Failure parity:** a hidden chunk, a compacted episode, an interval beyond the checkpoint and a corrupted chunk
  each fail through the encoded path with the same error class as through the raw path. A chunk version above the
  client's maximum returns a raw frame with the same samples.
- **Fuzzing:** a `cargo fuzz` target for `morlet_decode_frame` on arbitrary bytes. It must never crash, never write
  past `out_len`, and never return 0 for a frame whose samples differ from a trusted decode.
- **Docs:** `docs/chunk-format.md` gains the frame format and the rule that a client must refuse chunk versions it
  does not know; `docs/compatibility.md` gains the encoded read in its read-back contract.

The chunk format becomes a wire format for the SDK, but versioning is already the rule ("a future version 4 must
keep decoding versions 2 and 3"), and the version check above keeps old clients on the raw read.

## What we expect, and how it will be measured

These are estimates from the attribution, not results. For a 128,000-sample read: the round trip stays about
0.3 ms; server work drops from about 2.5 ms to well under 1 ms (lookup and framing, no decoding); transfer drops
from about 3.1 ms to about 0.5 ms for about 170 kB; client decoding adds about 1 to 2 ms, depending on threads.
That would put the read around 3 ms instead of 5.6 ms. Short reads of one or two chunks gain little and could
lose slightly, because whole edge chunks are sent. The SDK therefore uses the encoded read only from a threshold
(starting at 8 chunks, as for parallel decode), and that threshold is tuned on development data.

Measurement plan:

1. **Development (exploratory):** extend `r2_attribution.py` with the encoded read, on the development set,
   for R1, R2 and R3, at 1 and 4 client threads.
2. **Release:** a release containing parallel decode and compressed transfer, since the website takes benchmark
   figures only from released builds.
3. **B3.15 (frozen):** an amendment written and frozen before any load. It compares the release against QuestDB in
   one session, with fresh-process reads, through the SDK's read path, with B3's statement rule, and with build
   against previous release for every read measure. Hypothesis: R2 at least 10% faster than QuestDB at both p50 and
   p95. The benchmark reads through the SDK, the documented client path, not a benchmark-only shortcut, so the
   result says something about what users get.

## Build order

1. `morlet-ffi` crate with `morlet_decode_frame`, Rust tests, golden test, fuzz target.
2. `morlet_frame_chunks` and `morlet_read_interval_encoded` (in SQL 0.3.5's update script), pg tests for coverage,
   visibility, version check and failure parity.
3. SDK integration with fallback and the environment switch; Python tests (golden, differential against raw).
4. Development measurement (step 1 of the plan); tune the chunk threshold.
5. Docs: chunk format, access control, compatibility, SDK.
6. CI wheels for the three platforms.

## Decisions for the owner

1. **Native wheels for the SDK.** This is the first compiled part of the Python SDK, so releases need per-platform
   wheels built in CI. The alternative, a pure-Python decoder, would be too slow for varint streams to pay off and
   would need its own fuzzing as a second implementation.
2. **Edge chunks sent whole.** This is acceptable under per-recording visibility, as argued above. It would need
   revisiting if visibility ever became finer than a recording.
3. **SQL version.** The new functions go into SQL 0.3.5, which is not released yet, through its generated update
   script from 0.3.4; the chunk format does not change.

The owner agreed to all three on 2026-11-06, and to storing chunk summaries in every new collection from the same
SQL version, so that interval statistics are no longer merely on par with QuestDB by default.
