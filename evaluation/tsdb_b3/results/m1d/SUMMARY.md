# Study M1d: GreptimeDB 1.3.0's smallest memory cap — results

Run 2026-11-13 08:17–08:31 under amendment M1d (frozen in `5a01225` before any load) and its deviation (`7b5046f`,
written before any further load). D2 load of B3's benchmark set (101.6 M samples), swap off, 8 CPUs.

| System | 256 MB | 512 MB | 1 GB | 16 GB | Smallest passing cap |
|---|---|---|---|---|---|
| GreptimeDB 1.3.0 | killed out of memory after 5 s | killed out of memory after 69 s | **pass** (222 s, every read exact) | pass | **1 GB** |
| MorletDB 0.3.12-alpha.1 | run 1: load done, a server process killed out of memory during the correctness reads; run 2: **pass** | pass | — | pass | **256 MB** (one of two runs failed) |

- GreptimeDB needs 4× MorletDB's cap (caps are powers of two, so coarse). Working-set peak while loading: GreptimeDB
  744 MiB at 16 GB and 590 MiB at 1 GB; MorletDB 180 MiB at 16 GB and 172 MiB at 256 MB.
- At its passing cap GreptimeDB ingested 0.76 M samples/s (0.76 M at 16 GB), statistics p50 2.69 ms; MorletDB at
  256 MB 14.3 M samples/s, statistics p50 0.46 ms. GreptimeDB's storage step settled in every passing cell.
- **MorletDB at 256 MB was not reliable in this session:** the first run lost a server process to the OOM killer
  during whole-recording reads, which 0.3.9-alpha.1 passed in M1b and M1c. The repeat passed. To be filed as an
  issue; until it is understood, 256 MB is reported as MorletDB's smallest cap with this caveat.
- Machine: one-minute load average 3–6.7 during the cells (Docker's VM; a langfuse stack and two idle PostgreSQL
  containers kept running), recorded in `machine/`.
