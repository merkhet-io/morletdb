# S2: confirmation soak of the beta, with scheduled disturbances (#529)

Status: **DRAFT**, not frozen. It is frozen (section "Frozen", added then) before the test week starts; changes after
that are dated amendments at the end of this file. The verdicts are reported separately from S1 (#191).

## Question

S1's question on the beta release: can MorletDB, with the steward beside it, store a continuous live feed for a week
on a small edge box without a person? That means:

- losing and changing nothing it acknowledged;
- recovering by itself from a crash and from a restart;
- staying as fast at the end as at the start.

## What changes from S1, and why

S2 is S1's protocol (`evaluation/soak_s1/PROTOCOL.md`, frozen 2026-11-10, with shakedown changes 1–7) with these
changes, and nothing else:

1. **The release:** the first release with #556 (SQL 0.3.14, `morlet_latest_per_stream`) and #557 (the steward
   serves the recording furthest behind first), expected as `0.4.0-beta.3`, with the steward and the collector of the
   freezing commit (S1: 0.3.10-alpha.1). The fixes are named for the S1 failures they address (#527, #528).
   The SeedLink collector reconnects after a database restart (#525). The steward has its fixes since S1:
   - verification on slow boxes (#532);
   - envelope grids by index (#531);
   - space freed inside the database (#538);
   - bounded tables of its own (#540).
2. **Both disturbances are scheduled.** S1's D2 was missed because it had to be started by hand (S1 amendment 2).
   - At the start of the test week, `disturb.sh schedule` draws each time once. It is drawn uniformly inside its
     window (at least 30 minutes from either edge), from the start time and seed 529, so nobody chooses it.
   - The drawn times are written to `results/schedule.json`.
   - A cron entry (`disturb.sh due`, every 5 minutes) runs each one when its time comes, once.
   - A disturbance whose window has closed is logged as missed and not run.
3. **D1 is a real crash from the start:** SIGKILL to the collector's process on the host. This is S1's D1r: Docker
   treats it as an unexpected exit, so the restart policy restarts the collector. S1's first D1 used `docker kill`,
   which Docker records as a manual stop (S1 amendment 1).
4. **Resumption is measured from the database's stored counts**, not from the collector's per-process counter, which
   resets when the collector restarts (S1 amendment 2).
   - The count is the sum of the recordings' stored ends in `public.live`, read with `psql` in the database container.
   - `base` is the count once the disturbance has returned. For D2, it is the first count the restarted database
     answers.
   - Storing has resumed at the first 10-second poll whose count is above `base`.
   - Rows stored before `base` is read are counted in it, so the measured time can only be later than the real one.
5. **The analysis and the probes are stricter** (from review, before freezing):
   - H1 is PENDING, not PASS, while any archive window could not be fetched, and while `gaps.jsonl` is missing.
   - H2 is PENDING while a steward status snapshot is unreadable.
   - H3 fails on an explicit `resume_s: null` (the poll saw no resumption), and on a disturbance that was attempted
     but never logged (`disturb.sh` records each attempt before running it and never repeats it).
   - H5's searchable share clips both stored and searchable samples at one hour before each recording's end (the
     probe records both); S1 subtracted one nominal hour from the stored samples only.
   - The host probe records stopped containers of the household's project as down. It also writes one JSON value
     when the steward's status cannot be read.
6. **The probe stops loading the box it measures, and two bounds measure what they name** (#528, from S1's data,
   before any S2 data exists):
   - `morlet_coverage` reads the whole archive. In S1, run every 15-minute round, it took 7 s on day 0 and about
     10 minutes per round by day 6. The harness itself was then keeping the 1-CPU database busy two thirds of the time.
     S2 measures coverage every 24th round (6 hours), and H5's end value comes from one `probe.py --coverage-only`
     run after the week.
   - The probe's `latest` uses `morlet_latest_per_stream` (network, station, location, channel), so it reads the stream's newest
     recording, as an application on 0.3.14 would (#527).
   - H4's collector bound uses its process memory (anon + shmem), as the database's already does (S1 shakedown change
     7). In S1 the collector's anon memory stayed at 51–56 MiB all week, while Docker's figure grew with the spool's
     page cache (137 to 223 MiB). Docker's figure and the page cache are reported, not bounded.
7. **Its own directory** on the soak file system (`/srv/morlet-soak/s2`), Compose project `soak-s2`, and port 54349.
   S1's data stays where it is, untouched.

## Setup

S1's setup, unchanged except as above:

- **Box:** Celeron J3060 (2 cores), 3.7 GB RAM, internal SATA hard disk, Debian 12 (kernel 6.1.0-54 since
  2026-11-21), Docker. The box also runs a household document service; the soak must not disturb it (stop rules).
- **Storage:** the dedicated 40 GB ext4 file system (loop file) of S1. S2 uses `/srv/morlet-soak/s2`; its
  `results` and `spool` are created by the box's user before `docker compose up` (Compose would create missing bind
  sources as root). S1's data
  takes 22% of it, which leaves about 30 GB; S1's week took less than that.
- **Software** (`compose.yaml`, project `soak-s2`), with S1's limits:
  - the database, 1 CPU and 1 GB;
  - the steward, 0.5 CPU and 256 MB, `--load-per-cpu 1.5` (S1 shakedown changes 1 and 3);
  - the collector, 0.5 CPU and 512 MB;
  - the probe, 0.25 CPU;
  - the host probe and `disturb.sh due`, from cron.
- **Feed and reference:** S1's 12 stations × 3 channels at 100 Hz from GEOFON (3,600 samples/s), and GEOFON's FDSN
  archive for the comparison.

## Phases

1. **Shakedown (24 h, not evaluated).**
   - The full setup runs, with a test schedule: `disturb.sh schedule-test <start>` puts D1 one hour and D2 two hours
     after the shakedown's start, in its own `results`. This tests the scheduled D1 and D2 and their resumption
     measurement on this box.
   - Configuration may change. Every change is recorded below before freezing.
2. **Freeze.** This file gets a "Frozen" section with the image digest, the commit, the test-week start and the drawn
   schedule. It is committed before the test week, together with `analyze.py`.
3. **Test week (7 × 24 h), on an empty database.**
   - The shakedown's data is kept aside in `shakedown/`.
   - `disturb.sh schedule <start>` runs once, right after `docker compose up` returns.
   - Nobody touches the box. D1 (between start + 48 h and start + 72 h) and D2 (between start + 96 h and
     start + 120 h) run by themselves.

## Shakedown changes (recorded before freezing)

None yet.

## Hypotheses (all must hold; each is reported with its numbers)

S1's hypotheses and bounds, unchanged, with H3 extended to the schedule:

- **H1, nothing lost or changed.**
  - `verify_fdsn.py --windows 60 --seed 529` over the test week, plus the hour after D1 and the hour after D2 on every
    channel of one station drawn with seed 529: **0 samples differ** from the archive. Stored samples the archive
    lacks are reported.
  - `morlet_verify` over every recording at the end: **0 problems**.
  - The collector's log has **no "spool hole on replay"** line.
- **H2, unattended.**
  - No manual action in the test week.
  - No container restart besides D1 (the collector) and D2 (the database), and no OOM kill.
  - The steward's status is never *action required* for more than 1 hour, and it never sets the ingest hold.
- **H3, recovery.**
  - D1 and D2 each ran within 10 minutes of their drawn time. A missed one fails.
  - After each, storing resumes within 5 minutes, measured from the database's stored counts (change 4).
  - After D1, every sample spooled before it is stored: the replay lines in `gaps.jsonl`, and no hole.
- **H4, steady.** The last 24 hours of the test week compared with the first 24 hours:
  - the p95 latency of `latest` and of interval statistics is at most 2× as high;
  - for similarity search, the p95 of latency divided by the searched history is at most 2× as high;
  - the database container's and the collector's peak process memory (anon + shmem) are each at most 1.25× as
    high (change 6).
- **H5, keeps up.**
  - The collector's backlog never exceeds 10,000 packets.
  - At the end, at least 99% of the stored samples older than one hour are searchable, measured once by
    `probe.py --coverage-only` within 30 minutes after the week (change 6).
  - Stored samples are at least 97% of the nominal 3,600 samples/s, and every shortfall is explained.

**From S1, stated before the week:** S1 failed H4 (all five bounds) and H5's searchable share (97.67%). The
diagnosis on S1's data (#528) found these causes:
- the steward starving recordings late in `recording_id` order (H5; fixed in #557);
- `morlet_latest` reading every daily recording (#527, #556);
- the probe's own coverage scan loading the box (change 6);
- the collector bound measuring page cache (change 6).

Contention on the hard disk remains. The bounds are not relaxed, and a failure is reported as it is.

## Stop rules

S1's, unchanged:

- The household service is unhealthy for more than 15 minutes in `host.jsonl`, or the owner reports it as slow: the
  soak stops, and it is reported as stopped by a host conflict.
- The soak file system reaches the steward's reserve: that is a result (H2 fails), and the soak continues.
- GEOFON's server is unreachable for more than 12 hours in total: the week is extended by the outage, at most once.
  The schedule is not redrawn.

## What is published

The same as in S1:

- the protocol, the probes and the analysis script;
- `probe.jsonl`, `host.jsonl` (the household services renamed), `gaps.jsonl`, `schedule.json`,
  `disturbances.jsonl` and `disturb.log`;
- the verification reports and RESULTS.md.

No waveforms.

## Not covered

S1's limits: power loss (#190), more than a week, other feeds and rates, and other databases. Also: fixes for #527
and #528, which a later soak would confirm.
