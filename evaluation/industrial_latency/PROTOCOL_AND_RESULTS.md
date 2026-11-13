# IU1-E: search latency under edge-sized container limits (rules written 2026-11-11, before measuring)

Question: how long does search take when the database container is limited to 2 CPUs and 512 MB, as in the
project's own study EX1 (`evaluation/edge_x1`, config `e512`)? Same host, same data (Bosch collection of IU1 and
the tool-life collection of IU2), same queries in both configurations.

| Config | CPUs | Memory | PostgreSQL settings |
|---|---|---|---|
| `ref` | no limit (16 logical) | no limit (7.2 GB VM) | as in IU1: `shared_buffers=1GB`, `work_mem=64MB`, 4 parallel workers per query |
| `e512` | 2 | 512 MB, no swap | as EX1: `shared_buffers=64MB`, `max_connections=20`, `maintenance_work_mem=32MB`, other settings image defaults |

One client, queries drawn with seed 20261111, every task run twice per configuration; the second round is
reported (warm cache), p50 and p95 in ms. Harness `edge.py`.

- **E1** search in the good history of one operation (as T2): 200 query windows from M02 and M03, top 40.
- **E2** search over all 85,042 windows of axis x: 50 queries exact, 50 with probes.
- **E3** window statistics (`morlet_interval_stats`, 4,096 samples): 200 calls.
- **E4** one decision per run as in T2: 8 windows on each of 3 axes, 24 searches in a row, for 30 runs; time per run.
- **E5** tool-life collection: search of a 4,096-sample window (25 kHz) against one baseline cycle, 100 queries.

Also recorded: whether the container was killed for memory (`OOMKilled`, cgroup `memory.events`).

This limits CPUs and memory on laptop cores. It is not edge hardware: single cores stay fast (the project's Pi run
was about 20 times slower than its limited run on desktop cores).

## Results (2026-11-11, second round of each configuration, p50 / p95 in ms)

| Task | `ref` | `e512` (2 CPUs, 512 MB) |
|---|---|---|
| E1 search in the good history of one operation (about 2,000 windows) | 32 / 43 | 32 / 42 |
| E2 exact search over 85,042 windows | 167 / 212 | 282 / 366 |
| E2 probe search over 85,042 windows | 131 / 173 | 147 / 197 |
| E3 statistics of one window | 1.0 / 1.5 | 1.1 / 1.6 |
| E4 one decision per run, 24 searches in a row | 721 / 953 | 726 / 878 |
| E5 tool-life collection, search against one baseline cycle (about 900 windows) | 11 / 16 | 11 / 17 |

No out-of-memory event in `e512` (`OOMKilled` false, cgroup `oom` 0, `oom_kill` 0, `max` 0), no restart; peak
404 MB, of which 375 MB page cache, 5 MB process memory and 74 MB shared memory at the end.

Reading: with 2 CPUs and 512 MB the small, filtered searches that a per-part decision needs take the same time
as without limits. Only the search over the whole archive slows down (1.7 times), because it uses fewer parallel
workers. Probe search over the whole archive is hardly affected and is then faster than exact search.

Limits of this measurement: laptop cores (one core of this machine is far faster than an edge CPU), an NVMe disk
behind the container, one client, warm cache, two rounds. In the first round after the restart E1 took 42 / 97 ms
and E2 exact 269 / 424 ms. In `ref`, the first round of probe search took 463 / 3,321 ms because the index had
been pushed out of memory by the tool-life import.
