# Pilot Stage 2 results: recovery passes; target ingest holds for 15 min and for 111 min before an unexplained stall; 2× overload does not

Plan: [STAGE2_PLAN.md](STAGE2_PLAN.md) (frozen 5ad1f12, deviations logged at its end). `main` at
e31e1a3 plus `morlet_collection_status` (#74). One Apple M5 Max, **shared with an unrelated
CPU-heavy job** (load average 3–20 during the runs); correctness results are unaffected,
timings are indicative.

## Part A — diagnostics

`morlet_collection_status` (PR #74) reports raw, envelope and probe progress, backlogs,
row-security state and bytes per structure; the tests below read it.

## Part B — recovery and lifecycle (`morlet_pilot_rec`, 12 recordings, 50,604 windows): all pass

| Test | Result |
|---|---|
| B1 full backup / restore | `pg_dump -Fc` 3.1 s, 459 MB; restore 17.6 s; raw chunks and envelopes byte-identical, exact search identical, probe recall 1.0 |
| B1 raw-only backup / rebuild | dump of recordings + chunks + segments 1.4 s, 146 MB; restore 3.3 s; rebuild envelopes 12.5 s, probe index 10.6 s; byte-identical, identical search |
| B2 aborted appends | 20/20 transactions aborted (10 mid-statement, 10 before `COMMIT`): no partial chunk, envelope or probe row; checkpoint = stored samples; every replay succeeded |
| B3 aborted build / enable | aborted `morlet_build_envelopes` left 0 rows from that call and resumed to completion; aborted `morlet_enable_envelope_probes` left no table; retry 11.6 s |
| B4 workers | external worker SIGKILLed and restarted, a second worker on the same recordings, and the in-database batched builder concurrently: 0 duplicates, byte-identical. **Finding:** the external worker writes envelopes only — `probe_backlog` 12,715 until filled |
| B5 deletion (2 of 13 recordings) | cascades left 0 orphan chunks, envelopes or probe rows; no deleted recording in exact or probe results; probe recall 1.0. **Finding:** `VACUUM` of the probe table took 113 s and did not shrink the 62 MB index |
| B6 online index rebuild | `REINDEX INDEX CONCURRENTLY` during 4 searching clients: 10.1 s, 0 errors; search p95 23.6 → 31.1 ms during, 23.1 ms after; index storage peaked at 2× during the rebuild |
| B7 failed upgrade | in the upgrade test (CI): a blocked update rolls back completely from 0.3.0 and from 0.2.0; old version stays, reads identical; retry succeeds |

**Verdict B: continue.**

## Part C — load (`morlet_pilot_load`)

**C1 fill:** 374 recordings, 1.92 G samples, **938,768 windows** in 570 s (3.4 M samples/s with
envelopes, 4 sessions); probe index 275 s (`maintenance_work_mem` 4 GB); database 4.8 GB (raw
1.78 GB, envelopes 0.93 GB, probe table 0.85 GB, probe index 1.17 GB).

**C2 target load, 15 min — passed** (load average mean 11.7, max 19.6):

| Measure | Result |
|---|---|
| Ingest, 16 ch × 25.6 kHz (409,600 samples/s) with envelopes + probe rows in the append transaction | 409,527 samples/s; append transaction (4 channels) p50 117 ms, p95 251 ms, max 859 ms; signal lag peak 21.7 s, 0.1 s at the end |
| Filtered search (one station-day, exact path) | p50 78 ms, p95 167 ms (716 queries) |
| Unfiltered probe search | p50 130 ms, p95 337 ms; recall 0.998 (144 spot checks in the same snapshot) |
| Unfiltered exact search (~960 k windows), once a minute | p50 6.0 s, p95 9.7 s |
| Errors | 0 |
| Database growth / WAL | 0.76 GB / **13.8 GB** |

**C3 overload — failed its criterion** (load average mean 8.7): at 2× (819,200 samples/s) the
writers sustained 500–574 k samples/s (minute averages) and the signal lag grew to 218 s; the
abort rule (lag > 60 s and growing for 5 min) stopped the run after 5.2 min, before the 1×
recovery phase. Capacity on this shared machine with 4 writers and synchronous maintenance is
therefore about **1.3–1.4× the target**, not 2×.

**C2p, pilot configuration, 15 min — passed** (Amendment 1: `shared_buffers` 8 GB, `max_wal_size`
16 GB, `checkpoint_timeout` 15 min, `wal_compression` on, changed together; load average mean 7.3,
max 9.7, against 11.7 in C2, which confounds the latency comparison):

| Measure | C2 (defaults) | C2p (pilot configuration) |
|---|---|---|
| Ingest | 409,527 samples/s; lag peak 21.7 s | 409,527 samples/s; lag peak 2.0 s |
| Append transaction p50 / p95 / max | 117 / 251 / 859 ms | 54 / 101 / 357 ms |
| Filtered search p95 | 167 ms | 81 ms |
| Unfiltered probe search p95, recall | 337 ms, 0.998 | 100 ms, 0.994 (146 checks) |
| Unfiltered exact search p95 | 9.7 s | 7.8 s |
| Database growth / WAL | 0.76 GB / 13.8 GB | 0.65 GB / **1.91 GB** |
| Errors | 0 | 0 |

**C4, pilot configuration, planned 2 h — stopped at 112.5 min; not completed.** For the
110.9 minutes before an unexplained pause of all client processes (deviation 4), the target was met:

| Measure (11:13:25–13:04:18, from `c4_reanalysis.json`) | Result |
|---|---|
| Ingest | 409,585 samples/s; writer lag max 24.4 s in the first hour (background job running), 3.4 s in the second |
| Append transaction p50 / p95 / p99 | 98 / 190 / 245 ms |
| Filtered search p50 / p95 (4,393) | 72 / 118 ms |
| Unfiltered probe search p50 / p95 (4,465), recall | 84 / 138 ms, 0.993 (890 checks) |
| Unfiltered exact search p50 / p95 (102) | 11.3 / 14.5 s |
| Database growth / WAL | 4.68 GB / 11.96 GB |
| Errors | 0 |
| Load average | mean 11.7, max 29.0 (background job stopped at about 12:15) |

Then no writer, client or monitor process recorded anything for 89–106 s; the writers' lag reached
90.8 s, and the harness's lag rule (stricter than the plan's wording) stopped the run. The stored
`c4.json` reports 378,980 samples/s because it divides by the scheduled 7,200 s; over the elapsed
6,752 s the rate was 404,135 samples/s including the pause.

**Verdict C: narrow.** With the pilot configuration, the target rate held for 111 minutes with
interactive search and bounded lag, but the planned 2-hour run did not complete, the cause of the
pause is unknown, and overload is not absorbed (headroom about 1.35×; synchronous maintenance has
no backlog to drain). WAL with the pilot configuration was about 2.6× the database growth.

## Findings that change operations

1. **WAL volume:** 13.8 GB of WAL for 0.76 GB of growth in 15 minutes with default settings;
   1.91 GB for 0.65 GB with the pilot configuration (four settings changed together, so no single
   one is credited), and 11.96 GB for 4.68 GB over 111 minutes. Plausibly, fewer checkpoints force
   fewer full-page images of HNSW pages, but this was not isolated.
2. **After deletions, rebuild the probe index online** instead of vacuuming it (10 s vs 113 s,
   and only the rebuild reclaims space).
3. **External workers must also fill probe rows**; `morlet_collection_status` shows the
   backlog.
4. **Unfiltered exact search over ~1 M windows takes seconds**; interactive use should rely on
   probe search or filters.

## Pilot operating range (tested)

16 channels × 25.6 kHz with synchronous maintenance, an archive of about 1 M windows growing by
about 2.5 GB/h, 4 analysts: 15 minutes with default settings, and 111 minutes with the pilot
configuration, on shared hardware. Longer runs, rates above about 1.35× this, cold caches and other
machines are untested. By the plan's criteria MorletDB is **not yet pilot-ready**: the 2-hour
stability run did not complete. A measured stability duration is not a production release.

## Next steps (by finding)

1. Repeat C4 for the full 2 hours on a quiet machine, with the lag rule implemented as worded
   (above 60 s and still growing after 5 minutes) and the rate computed over elapsed time; record
   system-wide activity (for example `vm_stat`, `iostat`) so that a pause like the one in C4 can be
   attributed.
2. Overload behaviour: either accept 1.35× headroom as the pilot limit, or measure background
   (worker) maintenance, which decouples appends from index work at the cost of visibility delay.
3. Make the external worker write probe rows (or add a maintenance function for them).

## Reproduce

```sh
.venv-seismic/bin/python -I evaluation/pilot/recovery.py data/cx-scale data/cx-confirm-2021 \
    target/release/examples/envelope_worker evaluation/pilot/results
.venv-seismic/bin/python -I evaluation/pilot/load.py fill data/cx-scale data/cx-confirm-2021 evaluation/pilot/results
.venv-seismic/bin/python -I evaluation/pilot/load.py run data/cx-confirm-2021 evaluation/pilot/results c2 15 1
.venv-seismic/bin/python -I evaluation/pilot/load.py run data/cx-confirm-2021 evaluation/pilot/results c3 10 2 10 1
# Amendment 1: ALTER SYSTEM SET shared_buffers = '8GB', max_wal_size = '16GB',
#   checkpoint_timeout = '15min', wal_compression = on; restart; then
.venv-seismic/bin/python -I evaluation/pilot/load.py run data/cx-confirm-2021 evaluation/pilot/results c2p 15 1
.venv-seismic/bin/python -I evaluation/pilot/load.py run data/cx-confirm-2021 evaluation/pilot/results c4 120 1
python3 evaluation/pilot/c4_reanalysis.py evaluation/pilot/results
# afterwards: ALTER SYSTEM RESET ALL; restart
```

Raw logs: `results/c2_logs.tgz`, `c2p_logs.tgz`, `c3_logs.tgz`, `c4_logs.tgz`.

Per-transaction and per-query logs are in `results/c2_logs.tgz` and `results/c3_logs.tgz`.
