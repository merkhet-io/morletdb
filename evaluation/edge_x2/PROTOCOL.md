# X2: MorletDB on a Raspberry Pi 3B+ — protocol (FROZEN)

Frozen in the commit that adds this file, before any measurement on the benchmark set. Two functional smoke
runs on B3's development set (listed below) checked the setup; they are not results. Deviations are appended.

## Question

X1 showed that the full workflow fits within 2 CPUs and 512 MB as a container on fast desktop cores. Does it
also run on real low-end edge hardware: a Raspberry Pi 3B+ with its own microSD card and the client on the
same device? How fast is it there, for searchable and for plain lossless storage?

## Device and software

- **Device:** Raspberry Pi 3 Model B Plus Rev 1.3: 4 × Cortex-A53 (ARMv8.0) at 1.4 GHz, 1 GB RAM
  (909 MiB visible, 76 MiB GPU), 32 GB microSD card (the only storage), Ethernet. The power supply as
  provided (no undervoltage flag in the smoke runs). **No active cooling** (no fan;
  the first smoke run had none, the second a desk fan): the Pi's soft temperature limit (60 °C, clock 1.4 → 1.2 GHz) is part of the condition measured. No other workload: the device previously ran NextcloudPi, whose
  services and timers (web server, PHP, MariaDB, Redis, mail, cron, apt timers, unattended upgrades) are
  stopped and disabled for X2.
- **OS:** Debian 11 (bullseye, past its support period) 64-bit, kernel 6.1.21-v8+, memory cgroups enabled
  (`cgroup_enable=memory cgroup_memory=1`), cgroup v2. Security packages from `archive.debian.org`.
- **Docker:** 20.10.5 (Debian package).
- **Client, on the Pi:** Python 3.9.2, numpy 2.0.2, scipy 1.13.1, psycopg 3.2.13, pyarrow 17.0.0
  (pyarrow 21's arm64 wheel stops with "Illegal instruction" on Cortex-A53, so the client uses 17).
- **Image:** `morletdb:x1`, X1's image (code of main `b25a1bd`), copied with `docker save` / `docker load`.

The device, OS and client versions are recorded in every result (`host`).

## Configuration and arms

Container limits of X1's `e512`: `--cpus 2`, `--memory 512m`, `--memory-swap 512m`, `shared_buffers=64MB`,
`max_connections=20`, `maintenance_work_mem=32MB`. The client runs outside the container, on the same Pi.

- **searchable:** X1's workload unchanged: durable ingestion with search features (`morlet-energy-v1`,
  `-lowrate-v1` at 1 Hz) and chunk summaries, envelope build, read-back of every channel against the
  manifest's SHA-256, R1/R2 reads, interval statistics, overviews, 20 similarity searches.
- **raw:** plain lossless storage: durable ingestion without features or summaries, read-back, R1/R2 reads,
  statistics and overviews (decoded from the samples); no envelopes or search.

Data: B3's benchmark set (124 Paderborn acquisitions, 101,586,735 samples, 868 channels), on the Pi's card.

## Measurements

As X1 (`docker stats` every second; cgroup `memory.peak`, `memory.events`, `anon`, `file` after every
phase; Docker's OOM flag and restart count), plus the device every second through `vcgencmd`: SoC
temperature (maximum), ARM clock (minimum and median) and the throttle flags (bitwise OR over the phase).

**Runs:** three rounds of (searchable, raw), on fresh volumes. Before each run, the 5-minute load is below 1
and the SoC below 55 °C (`run_x2.sh`).

## Pass criteria (per arm; all three runs)

1. No out-of-memory kill (Docker's flag and the cgroup's `oom_kill` both 0), no restart, no failed statement.
2. Every one of the 868 channels read back with its manifest SHA-256.
3. searchable: the search result lists identical (hash) to X1's on the benchmark set (`83bcd505…`, from the
   same image on Apple M5 cores).

## Reported

Ingestion rate (median of three, range) per arm, envelope build time, query p50/p95, memory per phase,
database size, and the device's temperature, clock and throttle flags. A throttle flag other than the
soft temperature limit (bit 3 / 19) or any undervoltage (bit 0 / 16) is reported with the run.

## Claims allowed if passed

- "Runs the full workflow on a Raspberry Pi 3B+ (1 GB RAM, microSD) within 512 MB, every sample returned bit
  for bit", with the measured rates for searchable and raw storage, stated for this device, OS and cooling.
- Search returns the same results on the Pi as on the desktop (if criterion 3 holds).
- No claim about other devices, long-term operation, power loss, or industrial use.

## Development smoke runs (development set; not results)

Development set (`B3_SET=dev`, 101,453,304 samples), config `e512`; results in `results/dev/` (copied from
the Pi). They check the setup and are not results.

| | searchable (run 1, no fan) | raw (run 1, desk fan) |
|---|---:|---:|
| Passed (0 mismatches, no OOM event) | yes | yes |
| Ingestion | 81k samples/s (1,253 s) | 266k samples/s (381 s) |
| Envelope build | 315 s | — |
| R1 / R2 p50 | 6.3 / 35.6 ms | 6.0 / 32.0 ms |
| Statistics, 128,000 samples, p50 | 6.3 ms (chunk summaries) | 26.3 ms (decoded) |
| Overview of a whole channel, p50 | 5.9 ms | 48.4 ms |
| Similarity search, top 10, p50 | 616 ms | — |
| Search lists (hash) | `12b592da…`, identical to X1's development runs on the Mac | — |
| `docker stats` peak / `anon` | 158 / 15.5 MiB | 95 / 15.3 MiB |
| Device | 62–69 °C, 1.2 GHz (soft limit, flag `0x80008`) | ≤ 42 °C, 1.4 GHz |

What the smoke runs changed:
- pyarrow 21 → 17 on the client (Illegal instruction on Cortex-A53).
- `x1.py` gained the `raw` arm (`X1_ARM`), result naming per study (`X1_STUDY`), the host record and the
  `vcgencmd` device readings. The first smoke run predates the device readings (its device values were read
  by hand). `docker stats` takes about 2.5 s per sample on the Pi, so device readings come about every 2.5 s.
- The first smoke run wrote `x1_e512_r1.json` (X1 naming); it is stored here as `x2_e512_r1.json`.
- The cooling decision: the fan available is battery powered and would not last three hours, so the
  benchmark runs without active cooling, the same condition for every run.
