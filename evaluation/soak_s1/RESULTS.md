# S1 results: a week of live seismic data on a small x86 box (soak, #191)

Status: **unpublished draft, local only**, until the owner approves. Protocol: `PROTOCOL.md` (frozen 2026-11-10,
amendments 1 and 2). Test week: 2026-11-10T16:02:49Z .. 2026-11-17T16:02:49Z, complete (last record 16:00 UTC).
Raw outputs of the end-of-week checks: `results_s1/`.

## Verdict

**The week fails the protocol: H1 and H2 pass, H3, H4 and H5 fail.**

| Hypothesis | Verdict | In one line |
|---|---|---|
| H1, nothing lost or changed | **PASS** | 35,640,000 samples compared with GEOFON's archive, 0 differ; `morlet_verify` 396 recordings (2,236,461,010 samples, 1,027,023 chunks), 0 problems; no spool hole |
| H2, unattended | **PASS** | no manual action besides the harness's D1 start; no unexplained restart or OOM; steward *action required* at most 0.25 h, no ingest hold |
| H3, recovery | **FAIL** | D1 (as D1r) resumed in 96 s with the spool replayed and no hole; **D2 was not run** (amendment 2) |
| H4, steady | **FAIL** | all five bounds exceeded (below) |
| H5, keeps up | **FAIL** | backlog at most 27 packets and 99.0% of nominal stored, but only 97.67% of samples older than 1 h searchable at the end (bound 99%) |

The household service on the box stayed healthy (0 unhealthy snapshots); no stop rule applied.

## Numbers (from `results_s1/analysis_combined.txt`)

### H1
- `verify_fdsn --windows 60 --seed 191` over the week plus the hours after D1 and D1r: 99 recording-hours,
  35,640,000 samples compared, 35,640,000 equal, 0 different, 0 not in the archive, 0 errors. No hour after D2 exists
  (D2 was not run).
- `morlet_verify public.live --json` after the week (2026-11-17 21:04 .. 23:56 UTC): 396 recordings, 0 with problems.
- `gaps.jsonl`: 0 "spool hole on replay" lines.

### H2
- Manual actions: 0 counted against H2 (the D1 harness's manual `docker start`, amendment 1, is reported separately).
- Container restarts: collector 1 (D1r), database 0, probe 0, steward 0; no OOM kill.
- Steward status records: NORMAL 462, ATTENTION 201, ACTION REQUIRED 9; longest ACTION REQUIRED 0.25 h; no hold.

### H3
- D1 (`docker kill`, 2026-11-12 18:11 UTC) was a manual stop, not a crash (amendment 1); D1r (SIGKILL of the
  collector's process, 19:29 UTC) resumed storing after 96 s and replayed its spool with no hole.
- D2 (orderly database restart, due 2026-11-14 16:02 .. 11-15 16:02 UTC) was not carried out: **FAIL** by the
  protocol, because the recovery from a database restart was not shown in the week.

### H4 (last 24 h against first 24 h)
| Bound | First | Last | Ratio | Bound |
|---|---|---|---|---|
| `latest` p95 | 0.253 s | 9.198 s | 36.3x | 2x |
| interval statistics p95 | 0.064 s | 1.218 s | 19.1x | 2x |
| search p95 per searched sample | 5.75e-7 s | 2.84e-6 s | 4.95x | 2x |
| database process memory peak (anon + shmem) | 155.6 MiB | 196.3 MiB | 1.26x | 1.25x |
| collector peak memory (Docker) | 67.3 MiB | 142.9 MiB | 2.12x | 1.25x |

Absolute search p95: 4.5 s in the first day, 162 s in the last. Host load (5 min): median 2.92, max 5.3 on the
2-core box. The database's page cache peaked at 978-980 MiB in both windows (reported, not bounded).

### H5
- Collector backlog at most 27 packets (2026-11-16 12:45 UTC), 672 snapshots.
- Searchable at the end: 97.67% of samples older than 1 h; 4.85 h of feed unsearchable (maximum in the week 7.46 h,
  reached in its second half).
- Stored 3,564 samples/s, 99.0% of nominal; shortfall explained by logged gaps (252 "new UTC day", 36 gaps,
  2 reconnects, 2 collector restarts with replay).

## Deviations

1. **D1 harness** (amendment 1): `docker kill` stopped rather than crashed the collector; the manual start at
   18:27 UTC is reported, and D1r repeated D1 as a real crash.
2. **D2 not run** (amendment 2): nobody started it by hand in its window. H3 fails for it. The exploratory D2x after
   the week (below) cannot change any verdict.
3. **Where the checks ran:** `verify_fdsn` ran from the operator's Mac through an SSH tunnel to the box's database;
   `morlet_verify` through that tunnel stalled without a database session, so it was run inside the probe container
   on the box (`docker exec ... python3 -`), same script and arguments.
4. **The analysis was run twice, the first run kept.** `analyze.py` was run once as frozen (`analysis.txt`,
   `analysis.json`). Its H1 `morlet_verify` check showed PENDING ("no summary line"): the operator had redirected
   `morlet_verify`'s stderr, where it writes the summary line, to a separate file (`morlet_verify.err`). The analysis
   was run a second time, unchanged, with the tool's complete output (`morlet_verify.jsonl` followed by
   `morlet_verify.err`, as `morlet_verify_full.jsonl`); hashes of the inputs and the first run's outputs
   are in `hashes_before_rerun.txt`. The two runs differ only in that check (PENDING to PASS, 0 problems) and
   therefore in H1's verdict; every other number is identical (compared field by field).

## Exploratory: D2x, an orderly database restart after the week (amendment 2)

Not part of the week's verdict. `d2x.sh` ran `docker restart soak-s1-db-1` at 2026-11-17T23:57:59Z (returned
23:58:32Z), after the week's analysis.

| Check | Result |
|---|---|
| storing resumes within 5 min | **not shown**: `d2x.sh` logged `resume_s: null` (see below) |
| no other container restarts, no OOM | **FAIL**: the collector restarted once (restart count 1 to 2); no OOM; probe and steward untouched |
| no "spool hole on replay" | PASS: after D2x, 1 "collector restart: spool replayed", 0 holes |
| archive comparison of the hour from D2x | PASS: after the soak was stopped, 3 channels of GE.PSZ, 1,080,000 samples from 00:00 UTC, 0 differ; with `--min-minutes 0` also the spool-replay recordings from 23:59, 0 differ |

**What happened.** When the database shut down, the collector's write failed with `psycopg.OperationalError: the
connection is closed` (after `AdminShutdown`), and the collector exited; Docker's restart policy started it again at
23:58:35Z, 3 s after the restart returned. The new process replayed its spool and was storing again: its
`status.json` at 00:13:20Z showed 2,748,779 samples stored since it started.

**Why `resume_s` is null.** `d2x.sh` (like `disturb.sh` for D2) waits for the collector's `stored` to exceed its value
when the restart returned; `stored` counts from the collector process's start. Because the collector itself
restarted, the counter began again at 0 and could not exceed the earlier 1.59 billion within 15 minutes. The frozen D2
rule would have failed the same way. Storing did resume (above), but the time to resumption was not measured, so the
5-minute check is reported as not shown rather than passed.

**Archive comparison.** `verify_fdsn.py --windows 1 --seed 191 --at 2026-11-17T23:57:59Z` ran at 01:30Z
(`results_s1/verify_fdsn_d2x.json`): 1 recording-hour, 360,000 samples, 0 different, but that hour is the seeded
random window (GE.RUE..HHE, 2026-11-13 18:06 UTC), not the D2x hour. The script skips every recording whose stored
data ends less than 30 minutes before the run (`--settle-min 30`), and the D2x hour lies in recordings still being
written (the collector's new UTC-day recordings), or in spans shorter than its `--min-minutes 10` (the one-minute
recordings the restarted collector replayed its spool into at 23:59). The comparison was repeated with the same command at
06:30Z, 32 minutes after the soak was stopped (`verify_fdsn_d2x_after_stop.json`): 4 recording-hours, 1,440,000
samples, 0 different, 0 not in the archive: the random window again, and the seeded station GE.PSZ's three channels
from the start of their 2026-11-18 recordings (00:00:01-00:00:04 UTC) for one hour. That run still skips the one-minute
replay recordings (`--min-minutes 10`). An additional run with `--min-minutes 0`, reported separately
(`verify_fdsn_d2x_min0.json`), adds GE.PSZ's three replay recordings from 23:59:12-23:59:16 UTC (14,352 samples): 7
recordings, 1,454,352 samples, 0 different. The seconds between D2x (23:57:59) and the start of the replay
recordings lie in the collector's pre-restart recordings, which ended before 23:57:59, and were not compared.

**Finding.** The collector does not survive an orderly database restart: it exits on a closed connection and relies on
the container's restart policy, which a database restart alone does not exercise as the protocol expected. No data
was lost (spool replayed, no hole). Reconnecting inside the collector, and measuring resumption from the database's
stored counts rather than a per-process counter, are follow-ups.

## Stopping the soak

The soak was stopped at 2026-11-18T05:57:56Z with one `docker stop` of all four containers (owner's approval,
`results_s1/operator_notes.jsonl`). Docker stops them concurrently, so the database shut down while the collector was
still flushing; the collector exited on `AdminShutdown`, and the samples still in its spool at that moment were not
stored. This is after the week and D2x and changes no verdict. The database was then started alone for the archive
comparison, and the host probe's cron line disabled.

## What the week shows

- **Integrity held** through a week of unattended live ingest, a collector crash and a collector restart caused by
  the harness: nothing acknowledged was lost or changed, by the archive and by MorletDB's own checks.
- **Performance did not stay steady** on this box. Read latencies grew by an order of magnitude over the week, search
  per searched sample about 5x, and envelope building fell behind (H5's searchable share). The box ran at a median
  load of 2.9 on 2 cores with a hard disk; the protocol's question was whether this box holds steady, and it did not.
  The causes are not established by this run.
- **Recovery from a database restart was not shown in the week**; the exploratory D2x shows the collector depends on
  its restart policy to recover.
