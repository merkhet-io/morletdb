# X1 results: MorletDB inside edge-sized container limits

Protocol frozen in [PROTOCOL.md](PROTOCOL.md) (commit `5a04fe9`) before any measurement on the benchmark set;
run script committed before the run. Nine runs on 2026-11-03 (16:56–17:28), alternating `ref`, `e1g`,
`e512`, each on a fresh volume. Image `morletdb:x1` (`sha256:99c017441843…`, linux/arm64, 147.6 MiB), code
of main `b25a1bd`. Data: B3's benchmark set, 124 acquisitions, 101,586,735 samples, 868 channels.
Raw results: [`results/`](results/).

## Pass criteria (frozen; all three runs per configuration)

| # | Criterion | ref (8 CPU / 16 GB) | e1g (2 CPU / 1 GB) | e512 (2 CPU / 512 MB) |
|---|---|---|---|---|
| 1 | no OOM kill (Docker flag and cgroup `oom_kill` 0), no restart, no failed statement | 3 / 3 | 3 / 3 | 3 / 3 |
| 2 | all 868 channels read back with their manifest SHA-256 | 3 / 3 | 3 / 3 | 3 / 3 |
| 3 | search result lists identical to `ref`'s | `83bcd505…` | identical | identical |

**All three configurations pass.** The full workflow ran within 2 CPUs and 512 MB of memory (container
limits, linux/arm64): durable ingestion with search features and chunk summaries, envelope build, exact
read-back of every sample, reads, statistics, overviews and similarity search.

## Measurements (median of three [range])

| Measure | ref | e1g | e512 |
|---|---:|---:|---:|
| Ingestion (durable, features + summaries) | 1.62 M [1.19–1.63] samples/s | 1.63 M [1.61–1.63] | 1.60 M [1.60–1.63] |
| Ratio to `ref` | 1 | 1.01 | 0.99 |
| Ack per acquisition, p50 | 504 ms | 501 ms | 511 ms |
| Envelope build (868 recordings) | 14.1 s [13.5–15.2] | 13.1 s [12.6–13.1] | 13.1 s [12.6–13.8] |
| R1 4,096 samples, p50 / p95 | 0.62 / 1.04 ms | 0.61 / 1.05 | 0.62 / 1.04 |
| R2 128,000 samples | 6.07 / 8.58 | 5.55 / 7.95 | 5.63 / 7.97 |
| Interval statistics, 128,000 samples | 0.54 / 0.61 | 0.52 / 0.56 | 0.53 / 0.58 |
| Overview of a whole channel (1,000 buckets) | 0.47 / 0.50 | 0.45 / 0.48 | 0.47 / 0.51 |
| Similarity search, top 10 | 29.0 / 29.5 | 29.0 / 30.2 | 29.4 / 29.8 |
| Database size | 410.0 MiB [391.1–411.0] | 410.5 | 411.0 |

**Memory** (highest over all phases, range over the three runs):

| Measure | ref | e1g | e512 |
|---|---:|---:|---:|
| `docker stats` peak (usage minus inactive page cache) | 188–193 MiB | 205–210 MiB | 115–134 MiB |
| Process memory (`anon`) | 15.7–15.8 MiB | 15.7–15.8 MiB | 15.5 MiB |
| Page cache at the end (`file`, includes `shared_buffers`) | 1,001–1,037 MiB | 961 MiB | 456–476 MiB |
| cgroup `memory.peak` | 1,052–1,104 MiB | 1,024 MiB (= limit) | 512.5–512.7 MiB (= limit) |
| Limit reached and page cache reclaimed (`memory.events` `max`) | 0 | 306–313 times | 7,616–7,983 times |
| Out-of-memory events (`oom`, `oom_kill`) | 0 | 0 | 0 |

## Reading

- **Memory is not the constraint.** PostgreSQL's own process memory stayed near 16 MiB in every
  configuration. Everything above that is page cache and shared buffers, which the kernel reclaimed at the
  limit thousands of times under 512 MB without a single out-of-memory event and without slowing anything
  measured.
- **Two cores are not the constraint either,** for one ingest connection: it uses about one core (CPU
  90–93%), so all rates are within 1% of `ref`. Faster cores do not help a single connection here; slower
  cores would slow it roughly in proportion, which X1 does not measure.
- **`ref` run 1 was slower** (1.19 M samples/s, and its run took about 15 minutes instead of about 5). Its
  database was 19 MiB smaller (391 vs 410 MiB), consistent with B3.3's open observation that slower loads
  leave less dead checkpoint TOAST; not investigated here. The medians are unaffected.

## Claim supported

"The full workflow (durable ingestion with search features and summaries, envelopes, exact reads,
statistics, search) runs within 2 CPUs and 512 MB of memory as a linux/arm64 container, with every sample
returned bit for bit, at the same speed as without limits." Measured on Apple M5 Max cores with an internal
SSD; no statement about specific devices, slower cores or storage, long-term operation, or power loss.
