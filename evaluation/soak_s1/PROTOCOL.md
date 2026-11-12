# S1: a week of live seismic data on a small x86 box, without a person (soak, #191)

Status: **FROZEN 2026-11-10** (section "Frozen" below), before the test week started. Changes after that are dated
amendments at the end of this file.

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
2. **Steward bug: no envelopes for a new collection** (2026-11-08, after 35 minutes, with change 1 in place): the
   steward learnt which window and stride to build only from existing envelope rows, so a collection without any
   never got its first. Fixed in the steward (it also reads the grid the recordings declare), with a regression test;
   the soak runs the fixed steward. Found by the shakedown, not by the existing tests, which built one envelope by
   hand in their setup.
3. **Load gate 1.5 per CPU** (2026-11-09, after 33 hours with change 1): the soak's load stayed at about 2.0, mostly
   processes waiting on the hard disk, so the steward paused about 90 times an hour at a gate of 2.0. Envelopes fell
   behind steadily (unsearchable backlog 1.0 h after 4 hours, 3.2 h after 33 hours) and the steward's verification
   never ran (its status: "last verified never"). H5 would have failed from the gate alone. Changed to
   `--load-per-cpu 1.5` (3.0); the database container keeps its 1-CPU cap and low disk priority. The rest of the
   shakedown runs with this gate and must show a bounded backlog before freezing.
4. **H4 for search normalised by the searched history** (2026-11-09): the probe's search covers the channel's whole
   stored history, so its work grows with the archive (the slowest check went from 0.1 s to 2.2 s while the archive
   grew from 0 to 1.2 GB). A fixed 2x bound would test data growth, not drift. H4 now bounds search latency per
   searched sample; absolute latencies are reported. `latest` and `stats` do a fixed amount of work and keep the
   plain bound.
5. **Steward status recorded** (2026-11-09): `host.jsonl` had no record of the steward's status, which H2 needs. The
   host probe now records `steward.py status` (status, findings, any ingest hold) and each container's health.
6. **Release and fresh start** (2026-11-09): the first 33 hours ran 0.3.8-alpha.1. From change 3 on, the shakedown runs
   the current release 0.3.10-alpha.1 on a fresh database; the test week starts on another fresh database with the
   same image. The first 33 hours' data and results are kept aside (`shakedown-1/`), not evaluated.
7. **H4 bounds the database's process memory, not its page cache** (2026-11-10, after 19 hours of the second
   shakedown). `analyze.py` run on the shakedown showed the database container's memory as Docker reports it (usage
   minus inactive file cache) growing from 155 to 649 MiB. Its cgroup's `memory.stat` at that point: anon 11 MB, shmem
   143 MB (shared buffers), active_file 484 MB, inactive_file 349 MB. The growth is the kernel's page cache of a
   growing archive, which the 1 GB limit lets it fill and reclaim; a 1.25x bound on it would test the archive's size,
   not a leak. `host_probe.sh` now records each container's `memory.stat` anon, shmem and file, and H4 bounds the
   database's anon + shmem; its page cache and Docker's figure are reported, not bounded. The collector's bound is
   unchanged (Docker's figure; it holds little cache: 54-58 MiB).
   *Watched, not changed:* in the same run, with 6-hour windows instead of the protocol's 24 hours (a cold, empty
   database in the first), `latest` p95 rose 1.82x (0.155 to 0.282 s) and interval statistics p95 3.6x (8 to 29 ms,
   at most 61 ms), varying through the day with the hard disk's load. The protocol's 24-hour windows each span a full
   daily recording; the bounds stay.

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
  - the p95 latency of `latest` and of interval statistics is at most 2× as high;
  - for similarity search, the p95 of latency divided by the searched history (the stored samples of the probed
    station and channel at the time of the query) is at most 2× as high; absolute latencies are reported;
  - the database container's peak process memory (`memory.stat` anon + shmem, shakedown change 7) is at most 1.25× as
    high, and the collector's peak memory (as Docker reports it) as well; the database's page cache is reported.
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

## Frozen (2026-11-10)

- **Image:** `ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1@sha256:03ed3cc2abc82a27fa8456fa70a0cdd93cbf8b2d9f845d47b414024c38fa2c38`.
- **Commit:** the commit that adds this section, on branch `soak/seedlink-collector`; the box runs that checkout
  (`compose.yaml`, `probe.py`, `host_probe.sh` from cron every 15 minutes, the steward and collector built from it).
  Shakedown changes 1-7 are in force.
- **Analysis:** `analyze.py` of this commit, run once at the end:
  `python3 analyze.py $SOAK_DIR/results --start <start> --verify-fdsn <reports> --morlet-verify <output>
  --manual-actions <n> --json analysis.json`, with
  `verify_fdsn.py --windows 60 --seed 191 --from <start> --to <end> --out ...` (and `--at` the hour after D1 and
  after D2 on every channel of one station drawn with seed 191) and
  `tools/verify/morlet_verify.py public.live --json`, both after the week.
- **Disturbances:** `disturb.sh` of this commit, run by hand once each from a shell on the box (it refuses outside
  its day and a second run): D1 between start + 48 h and start + 72 h, D2 between start + 96 h and start + 120 h,
  each at a time the owner or the operator chooses in that window. No scheduler is installed for them.
- **Fresh start:** the second shakedown's database, spool and results are kept aside in `shakedown-2/`, not
  evaluated; the test week starts on an empty database.
- **Test-week start:** 2026-11-10T16:02:49Z (epoch 1794326569), when `docker compose up` of commit `b7abed4`
  returned on an empty database (collector started 16:02:34Z). The week ends 2026-11-17T16:02:49Z. D1 window:
  2026-11-12T16:02Z .. 2026-11-13T16:02Z; D2 window: 2026-11-14T16:02Z .. 2026-11-15T16:02Z.


## Amendment 1 (2026-11-12, after D1, before any analysis)

- **What happened:** D1 ran at 2026-11-12T18:11:00Z (epoch 1794507060) as `docker kill -s KILL soak-s1-collector-1`.
  Docker records `docker kill` as a manual stop, so the `unless-stopped` policy did not restart the collector: the
  harness stopped it instead of crashing it. disturb.sh logged D1 with `resume_s` null. The collector stayed down
  until a manual `docker start` at 18:27:00Z (epoch 1794508020), logged with its reason in
  `results/operator_notes.jsonl`; it stored again 94 s later and replayed its spool (`gaps.jsonl`).
- **Change:** D1 is repeated once as a real crash, **D1r**: `disturb.sh D1r <start>` sends SIGKILL to the
  collector's process on the host (`kill -s KILL <State.Pid>`; the process runs as the box's user), which Docker
  treats as an unexpected exit, so the restart policy applies. D1r runs in the D1 window (start + 48 h .. start + 72 h),
  once, and only after a D1 line exists. Nothing else changes: same image, database, compose file, probes and D2.
- **Analysis:** `analyze.py` evaluates H3 for D1 with D1r when D1r exists; the original D1 line, its 16-minute
  outage and the manual start are reported (`d1_harness`), not hidden. The manual start counts as one manual action
  caused by the harness and is reported separately; it is not counted against H2. The collector start it caused
  is explained by it. The outage's shortfall counts as D1's for H5. H1 covers the outage like any other hour.
