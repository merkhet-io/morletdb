# S1: per-chunk summaries — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at 5c6d053 (2026-11-03), with one deviation recorded there.
Run on 2026-11-03, 01:48–02:13 (`run_all.sh`), with build `d698be0` (the frozen commit plus the runner
script), library SHA-256 `656578ff…`, on PostgreSQL 17.11 on an Apple M5 Max.

Raw results: `results/{load,gate,time,ingest,finish}.json` and logs.

## Verdict

**Summaries make long-interval statistics 15–140× faster with identical results, for 4.5% slower ingestion
and 1.3% more chunk storage.**

- **Gate:** passed. Results with and without summaries were bitwise identical.
- **Primary rule:** at H 60 s, the speed-up at p95 is 57.9×, against a threshold of 10×, so the verdict is
  "summaries faster".
- **Ingest rule:** ingestion was 4.5% slower, within the 10% limit. The Python SDK therefore enables
  summaries in `create_collection`, and the docs recommend them.
- **New capability:** intervals beyond the previous 16,777,216-sample limit work with summaries. An hour of
  64 kHz data (230.4 M samples) takes 34 ms (p50).

## Correctness

| Check | H (64 kHz, 230.4 M samples) | L (100 Hz, 128 recordings) |
|---|---|---|
| Statistics and overviews (buckets 1, 10, 100, 1,000) on 300 random intervals: plain vs summarized, bitwise | 1,500 / 1,500 identical | 1,500 / 1,500 identical |
| Summaries equal to `morlet_encoded_chunk_summary(encoded)` | 56,250 / 56,250 (written by appends) | 117,554 / 117,554 (written by the builder) |

On H, 20 intervals of 16.8–230 M samples, beyond the old limit, were also checked against numpy over the
same samples:
- count, min and max were exact on all 20;
- the largest relative differences were 6.4 × 10⁻¹⁵ for the mean and 3.0 × 10⁻¹⁵ for RMS, against a
  1 × 10⁻¹² limit.

## Latency of `morlet_interval_stats`, ms (p50 / p95, 100 timings each)

| Archive | Interval | Samples | Plain | Summarized | Speed-up at p95 |
|---|---|---|---|---|---|
| H | 1 s | 64,000 | 2.4 / 2.8 | 0.8 / 1.0 | 2.7× |
| H | 10 s | 640,000 | 16.9 / 17.6 | 0.9 / 1.2 | 15.1× |
| H | **60 s** | 3,840,000 | 98.2 / 100.0 | 1.3 / 1.7 | **57.9×** |
| H | 16,777,216 samples (the old limit, about 262 s) | 16,777,216 | 413–419 / 418–429 | 2.6–2.8 / 3.0–3.3 | 131–141× |
| H | 10 min | 38,400,000 | not possible | 5.2 / 6.0 | — |
| H | 1 h | 230,400,000 | not possible | 33.4 / 35.6 | — |
| L | 1 h | 360,000 | 7.9 / 8.5 | 0.9 / 1.3 | 6.4× |
| L | 6 h | 2,160,000 | 43.0 / 48.2 | 1.0 / 1.3 | 36.1× |
| L | 24 h | 8,640,000 | 170.3 / 173.6 | 1.7 / 3.4 | 50.6× |

The 16,777,216-sample row combines the 10 min and 1 h timing runs. In each, plain was timed at the old
limit next to summarized at the same length; the ranges cover both runs.

Without summaries the time grows with the samples, about 0.025 ms per thousand at 64 kHz. With summaries it
grows with the number of chunks, about 0.6 µs per chunk, plus the two edge chunks. Short intervals of one or
two chunks gain little: at 1 s the speed-up is 2.7×.

## Overviews (`morlet_interval_overview`, 1,000 buckets, ms, p50 / p95)

| Interval | Plain | Summarized |
|---|---|---|
| 16,777,216 samples | 414.6 / 423.7 | 4.9 / 5.3 |
| 1 h at 64 kHz (230.4 M samples) | not possible | 35.6 / 36.9 |

## Costs

- **Ingest**, H's first 600 s (38.4 M samples), 5 alternating rounds, one connection, pipelined:
  - plain: 2.157–2.260 M samples/s, median 2.201;
  - summarized: 2.086–2.168 M samples/s, median 2.101;
  - change: **−4.5%**.
- **Storage:** the H chunk table grew from 317.1 MB to 321.2 MB, **+1.3%**. Each summary is 66 bytes plus a
  little row overhead, per chunk of 4,096 samples.
- **Builder**, for chunks stored before enabling:
  - H: 56,250 chunks in 11.2 s, i.e. 5,015 chunks/s or 20.5 M samples/s, in batches of 100,000;
  - L: 117,554 chunks in 19.1 s, i.e. 6,149 chunks/s.

## What follows

- **Docs:** summaries ship in pg_morlet SQL 0.3.3 as opt-in, recommended for archives with long intervals.
- **Python SDK:** `create_collection` enables them on SQL 0.3.3 servers (`summaries=False` turns this off).
  It also gains `overview()`.
- **Possible follow-ups:** the MCP `trend` and `describe_interval` tools could use overviews instead of
  reading samples. That is a separate change.
