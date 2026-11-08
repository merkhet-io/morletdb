# L1-beta results: the operating envelope of MorletDB 0.3.9

Protocol: [PROTOCOL.md](PROTOCOL.md), section "L1-beta, frozen", at `d796c75`, before any reported measurement.
Run once, 2026-11-08 21:10–21:34, on the owner's Apple M5 Max (Docker Desktop VM: 12 CPUs, 33.6 GB), start
condition met (load average 1.2, no foreign process above half a core; `results/l1.start`). Release image
`ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1` (SQL 0.3.7), a fresh container per point and repeat, 32 points × 3
repeats = 96 runs (`results/*.json`, `results/summary.json`, log `results/l1.log`). Medians of three; ranges in
`summary.json`.

## The envelope in one table

| Dimension | Tested | Works (all samples bit-exact) | Limit or knee |
|---|---|---|---|
| D1 sample rate | 1/60 Hz … 1 MHz | 1/60 Hz to 64 kHz store and read alike (6.8–7.2 M samples/s in, 20–24 M out) | **1 MHz refused**: rates are limited to (0, 200 kHz]. **Bug at 1/60 Hz:** time-to-sample conversion off by one sample (below) |
| D2 recording length | 10⁴ … 10⁹ samples | 10⁹ samples in one recording: 7.2 M samples/s in, 24 M out, reads of 4,096 samples 0.6–0.7 ms anywhere, commit latency flat (22.5 ms per 262,144 samples, first tenth = last tenth), 11.1 bits per sample | **Statistics over 10⁶ samples 8.9× slower** on a 10⁹-sample recording (5.4 ms against 0.6), at start and end alike. **Overview of a whole 10⁹ recording 117 ms** without roll-ups (linear in length). Below about 10⁵ samples per recording, fixed overhead dominates (10⁴: 65 bits per sample, 0.4 M samples/s) |
| D4 values | 8 kinds | all finite kinds exact; bits per sample: constant 0.8, ±1e9 1.2, integer counts and ADC values 11.8, 3-decimal values 12.6, denormals 17.1, full-entropy floats 67.7 | **NaN and ±Inf refused** ("samples must be finite and within ±1e9"), as documented. Full-entropy floats take 6% more than raw float64 (#326) |
| D5 ingest | chunks 1 … 65,536; 1–16 writers; gaps, resends | 4,096-sample chunks: 6.7 M samples/s; 4 writers 15.1 M, 16 writers 21.0 M; gaps, altered resends, overlaps and out-of-order ranges **refused with a clear message**, identical replays accepted, stored samples unchanged in every case | **Chunks of 64 samples** 5.5× slower to ingest and 30 bits per sample; **chunks of 1 sample** 266× slower (25,000 samples/s) and 1,332 bits per sample. **65,536 refused** (chunks are 1..32,768) |
| D7 resources (10⁸-sample workload) | 2 GB, 512 MB, 256 MB; 1 CPU; full disk | **256 MB holds:** no out-of-memory kill, ingest 7.1 M samples/s, read-back 16.9 M (−31%); 1 CPU: ingest 5.3 M (−28%), read-back −16%, single reads unchanged | **Full disk: PostgreSQL stops.** At 400 MB the write-ahead log could not be written (PANIC), the client saw the error, and the server did not come back while the disk stayed full (below) |

No acknowledged write was lost or changed in any run where the server stayed up; every read-back matched its
generator by SHA-256.

## Findings

1. **Time conversion is off by one at rates like 1/60 Hz (a bug).** At 1/60 Hz, 1,001 of 1,002 derived times
   differed from the exact reference: the sample at exactly 60 s is sample 1, `morlet_time_to_samples` returned 2.
   The cause is `float8::numeric`, which rounds the rate to 15 significant digits (1/60 → 0.0166666666666667,
   slightly above 1/60), so `ceil` overshoots at exact sample instants. 1/3 Hz, 1 Hz, 44.1 kHz and 64 kHz were
   exact; 1/7 Hz is affected too. The same conversion is used in time filters of search, hit start times,
   `before_only` and acquisition alignment. Fix: convert with `::text::numeric` (exact, shortest round trip).
   Issue for the beta.
2. **Statistics slow down with recording length.** `morlet_interval_stats` over 10⁶ samples took 5.2–5.8 ms on a
   10⁹-sample recording against 0.5–0.7 ms on 10⁸ and 10⁶, at every position: not the data read but something that
   grows with the recording (245,000 chunks). An engineering fix to find; issue.
3. **A full disk takes PostgreSQL down.** With the data directory on a 400 MB volume, an append failed visibly
   ("could not write to file pg_wal/…: No space left on device"), PostgreSQL stopped (PANIC) and could not restart
   while the volume stayed full, so whether the stored samples were intact could not be read back. This is
   PostgreSQL's behaviour on a full WAL disk, not MorletDB's; MorletDB's answer is to stop before it: on the
   Raspberry Pi the steward refused new recordings before the disk was full and nothing was lost (edge study). L1
   ran without the steward. Recovery after freeing space is not tested here; issue.
4. **The rate limit is 200 kHz.** 1 MHz recordings (acoustic emission, ultrasound) are refused at
   `morlet_start_recording`. A design limit to document; raising it is a decision, not a fix.
5. **Small chunks are expensive.** Chunks of 64 samples cost 5.5× in ingest and 2.6× in storage; single-sample
   chunks are not a sensible way to store waveforms (266× slower, 1,332 bits per sample). Clients should batch to
   about 4,096 samples; the importers do.

## What was not a knee

- Sample rates from 1/60 Hz to 64 kHz behave the same.
- Reads of 4,096 samples stay at 0.55–0.7 ms from a 10⁶- to a 10⁹-sample recording, at start, middle and end.
- Commit latency does not grow with the recording: 22.5 ms per batch in the first and the last tenth of 10⁹.
- Memory: the 10⁸-sample workload ran within 256 MB without an out-of-memory kill; reading back was 31% slower.
- Concurrency: 16 writers reached 21 M samples/s (3.1× one writer), every recording exact.

## Scope

One machine, synthetic data, the client on the same host, one repeat set. No search structures (D6), no
roll-ups, no steward. Points from the draft not run in L1-beta (D3, D6, D8, D9, crossovers, real data) are for 1.0.
