# S1: a week of live seismic data on a small x86 box, without a person (soak, #191)

Status: **DRAFT until the shakedown ends.** It is frozen, with the image digest and commit filled in, in a commit made
before the test week starts. Changes after that are dated amendments at the end of this file.

## Question

Can MorletDB, with the steward beside it, store a continuous live feed for a week on a small edge box without a
person: losing and changing nothing it acknowledged, recovering by itself from a crash and a restart, and staying as
fast at the end as at the start?

## Setup

- **Box:** an Intel Celeron J3060 (2 cores, 1.6–2.48 GHz), 3.7 GB RAM, internal SATA hard disk, Debian 12
  (kernel 6.1.0-30), Docker 29. A typical fanless industrial PC or edge gateway. The box also runs a household
  document service; the soak must not disturb it (stop rules).
- **Storage:** a dedicated 40 GB ext4 file system (loop file) for the database, the spool and the results, so the
  soak can never fill the host's disk.
- **Software** (`compose.yaml`, project `soak-s1`):
  - the release image `ghcr.io/merkhet-io/morletdb:<tag>@<digest>` (*recorded at freezing*) with data checksums,
    `max_wal_size=256MB`, `wal_compression=on`, `shared_buffers=128MB`; limited to 1 CPU and 1 GB, low disk priority;
  - the steward sidecar (`deploy/steward`, defaults: reserve the larger of 5% and 2 GB), 0.5 CPU, 256 MB;
  - the SeedLink collector (`examples/collectors/seedlink`), 0.5 CPU, 512 MB, spool on the soak file system;
  - the probe (`probe.py`, every 15 minutes), 0.25 CPU; the host probe (`host_probe.sh`, cron every 15 minutes).
- **Feed:** GEOFON's public SeedLink server `geofon.gfz.de:18000`, 12 stations × 3 broadband channels (HH?, 100 Hz):
  GE.WLF, GE.STU, GE.RUE, GE.IBBN, GE.PSZ, GE.MORC (Europe) and CX.PB01, PB02, PB03, PB06, PB08, PB09 (Chile):
  3,600 samples/s, about 311 million samples a day. Stations were chosen on 2026-11-08 as those delivering all three
  channels at 100 Hz in a 45 s test; nothing else.
- **Reference:** GEOFON's FDSN archive (`verify_fdsn.py`), which holds the same data the live feed sends.

## Phases

1. **Shakedown (24 h, not evaluated).** The full setup runs; configuration may change, and every change is recorded
   here before freezing. It also checks that the probes, the steward's envelope building and the archive comparison
   work on this box.
2. **Freeze.** This file with the digest, commit and test-week start, committed before the test week. The analysis
   script (`analyze.py`) is committed at the same time.
3. **Test week (7 × 24 h).** Nobody touches the box, except the two planned disturbances:
   - **D1, day 3:** `docker kill -s KILL soak-s1-collector-1` (a crash; the restart policy restarts it);
   - **D2, day 5:** `docker restart soak-s1-db-1` (an orderly database restart).
   Their exact times are logged in `results/disturbances.jsonl`.

If the test week passes, it may continue towards 30 days without a restart; the extension is reported separately.

## Shakedown changes (recorded before freezing)

1. **Load gate** (2026-11-08, after 20 minutes): with the steward's default (pause upkeep above 0.7 × CPUs = 1.4),
   the soak's own load of 1.4–1.6 paused it almost always; no search envelopes were built, so nothing became
   searchable. Changed to `--load-per-cpu 1.0` (2.0). The database container's 1-CPU cap still keeps upkeep off the
   host's second core.

## Hypotheses (all must hold; each is reported with its numbers)

- **H1, nothing lost or changed.**
  - `verify_fdsn.py --windows 60 --seed 191` over the test week, plus the hour after each disturbance on every channel
    of one seeded station: **0 samples differ** from the archive. Stored samples the archive lacks are reported.
  - `morlet_verify` over every recording at the end: **0 problems**.
  - The collector's log has **no "spool hole on replay"** line.
- **H2, unattended.**
  - No manual action in the test week besides D1 and D2.
  - No container restart besides D1 and D2 (restart counts in `host.jsonl`), and no OOM kill.
  - The steward's status is never *action required* for more than 1 hour, and it never sets the ingest hold.
- **H3, recovery.** After D1 and after D2, storing resumes within 5 minutes (stored sample counts in `probe.jsonl` and
  the collector's `status.json`), and every sample spooled before D1 is stored (replay lines in `gaps.jsonl`).
- **H4, steady.** Comparing the last 24 hours of the test week with the first 24 hours:
  - the p95 latency of each probe check (latest, interval statistics, similarity search) is at most 2× as high;
  - the database container's peak memory is at most 1.25× as high, and the collector's as well.
- **H5, keeps up.**
  - The collector's backlog never exceeds 10,000 packets.
  - At the end, at least 99% of the stored samples older than one hour are searchable (`morlet_coverage`).
  - Stored samples are at least 97% of the nominal 3,600 samples/s over the week. Every shortfall is explained:
    by a logged gap (station or server outage, reconnect) or by D1/D2. Unexplained shortfall fails H5.

## Stop rules

- The household service on the box is unhealthy for more than 15 minutes in `host.jsonl`, or the owner reports it as
  slow: the soak stops, and is reported as stopped by a host conflict, not as a MorletDB result.
- The soak file system reaches the steward's reserve: that is a result (H2 fails), and the soak continues so the
  hold and its release are observed.
- GEOFON's server is unreachable for more than 12 hours in total: the test week is extended by the outage, at most once.

## What is published

The protocol, the probes, the analysis script, `probe.jsonl`, `host.jsonl` (the household services' names and status
only), `gaps.jsonl`, the verification report and RESULTS.md. No waveforms (the archive has them).

## Not covered

Power loss (#190, on the Raspberry Pi), several weeks (an extension at most), other feeds or rates, comparison with
other databases.
