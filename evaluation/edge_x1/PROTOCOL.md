# X1: MorletDB inside edge-sized resource limits — protocol (FROZEN)

Frozen in the commit that adds this file, before any measurement on the benchmark set. A functional smoke
run on B3's development set (listed below) checked the script; it is not a result. Deviations are appended.

## Question

Can one MorletDB server do the full recording workflow inside the resources of a small edge device, a
2-core computer with 1 GB or 512 MB of memory, without running out of memory and with every sample
returned bit for bit? The workflow is: durable ingestion with search features and chunk summaries, envelope
build, exact reads, interval statistics, overviews and similarity search. How fast is it there, compared with
the unconstrained container?

This is a **container-limit proxy**: the cores are Apple M5 Max cores (arm64), much faster than those of a
Raspberry Pi or an industrial gateway. A pass supports "runs within 2 CPUs and 512 MB / 1 GB (linux/arm64
container)", not a statement about any particular device or its throughput.

## Configurations (Docker Desktop, linux/arm64, one container alone, no swap: `--memory-swap` = `--memory`)

| Config | CPUs | Memory | PostgreSQL settings |
|---|---|---|---|
| `ref` | 8 | 16 GB | image defaults (B3's limits) |
| `e1g` | 2 | 1 GB | image defaults (`shared_buffers` 128 MB, `max_connections` 100) |
| `e512` | 2 | 512 MB | `shared_buffers=64MB`, `max_connections=20`, `maintenance_work_mem=32MB` |

**Image:** `morletdb:x1`, built with `deploy/Dockerfile` from the commit that freezes this protocol. Its code
is main at `b25a1bd` (B3.3 merged: the faster extractor); this commit adds only evaluation files. Image id,
size and architecture are recorded in every result.

## Data and workload (`x1.py`)

B3's benchmark set: 124 Paderborn acquisitions, 101,586,735 samples in 868 channels (64 kHz, 4 kHz, 1 Hz).
Per run, on a fresh volume:

1. **Ingest** every acquisition with the importer core (one connection, pipelined, raw appends, 4,096-sample
   chunks, **durable commits**: `synchronous_commit=on`), with search features at ingest (`morlet-energy-v1`;
   `-lowrate-v1` at 1 Hz) and chunk summaries enabled. Files are parsed outside the timed part.
2. **Envelopes** built for every recording (window 4,096, stride 2,048).
3. **Read-back:** every channel read whole with `morlet_read_interval_raw` and compared with the manifest's
   SHA-256.
4. **Queries**, each after one warm-up: B3's R1 (4,096 samples) and R2 (128,000) intervals, 20 × 20 timed;
   `morlet_interval_stats` on R2's intervals, 20 × 20; `morlet_interval_overview` of each R2 recording whole
   (1,000 buckets), 20 × 20; 20 similarity searches (`morlet_search_similar`, a 4,096-sample window of
   `vibration_1` chosen with seed 20261103, filter `{"group": "high"}`, top 10), 2 timed repetitions each.
   The search result lists (recording, start, distance bits) are hashed.

Memory, per phase:
- `docker stats` every second (peak and median). Docker reports cgroup usage minus inactive page cache.
- At the end of every phase, from the container's cgroup v2 files:
  - `memory.peak`. This counts page cache, which the kernel reclaims at the limit, so it reaches the limit by
    design and is not process memory.
  - `memory.events`: `max` counts the times the limit was hit and memory was reclaimed; `oom` / `oom_kill`
    count out-of-memory events.
  - `anon` and `file` from `memory.stat` (process memory and page cache; `shared_buffers` is shared memory and
    is counted under `file`).
- Docker's OOM-killed flag and restart count.

**Runs:** three per configuration on the benchmark set, alternating `ref`, `e1g`, `e512`; the 5-minute load
below 5 before each run; AC power, `caffeinate`, no other study running.

## Pass criteria (per configuration; all three runs)

1. No out-of-memory kill (Docker's flag and the cgroup's `oom_kill` counter both 0), no container restart, no
   failed statement.
2. Every one of the 868 channels read back with its manifest SHA-256.
3. Search result lists identical (hash) to `ref`'s.

## Reported

Ingestion rate (median of three, range), envelope build time, query p50/p95, peak memory per phase and cgroup
peak, database size, image size. Rates under limits are compared with `ref` as ratios; there is no
comparison with other systems.

## Claims allowed if passed

- "Runs the full workflow within 2 CPUs and 1 GB" (and "512 MB" if `e512` passes), with the image architecture
  and the measured rates, stated as a container limit on fast cores.
- No claim about specific devices, other datasets, long-term operation, or throughput on slower cores.

## Development smoke run (development set; not results)

One run per configuration on B3's development set (`B3_SET=dev`: 101,453,304 samples, disjoint from the
benchmark set), image `morletdb:x1` built from main `b25a1bd`
(`sha256:7600b0463b40…`). Results in `results/dev/`; they check the script and are not results.

| | ref | e1g | e512 (run 2, after the memory fields were added) |
|---|---:|---:|---:|
| Passed (all checks, 0 mismatches) | yes | yes | yes |
| Ingestion, samples/s | 1.13 M | 1.10 M | 1.20 M (run 1: 0.98 M) |
| Envelope build | 18.1 s | 16.1 s | 18.1 s (run 1) |
| `docker stats` peak, ingest / queries | 181 / 187 MiB | 181 / 192 MiB | 116 / 151 MiB |
| cgroup `memory.peak` | 1,048 MiB | 1,024 MiB (= limit) | 512.6 MiB (= limit) |
| e512: `anon` / page cache at the end; `memory.events` | | | 15.6 / 478.9 MiB; `max` 6,883, `oom` 0 |
| Search lists (hash) | `12b592da…` | identical | identical |

What the smoke run changed in the script:
- Results of the development set go to `results/dev/`.
- `memory.peak` reached each limit by design (page cache), so `memory.events`, `anon` and `file` were added,
  and the pass flag now also requires the cgroup's `oom_kill` counter to be 0 and no restart.

What it showed for the design:
- One connection keeps ingestion below 1 core (CPU ≈ 92% of one core). The 2-core limit therefore binds
  little; a statement about rates under limits is about memory and I/O, not CPU.
- Timing differences between configurations on single runs are within run-to-run noise
  (e512 run 1 vs run 2: 0.98 vs 1.20 M samples/s). Rates are compared only as medians of three.
