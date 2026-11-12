# Steward: MorletDB keeps itself healthy on edge machines (design, #287)

Status: **accepted 2026-11-07**, with the recommended answers to the open decisions (end of this page).

Implemented in `tools/steward`:

- **P1:** automatic upkeep.
- **P2:** the ingest hold.
- **P3:** plans, approval and apply.
- **P4:** packaging: the sidecar image, the compose stack and the systemd unit (`deploy/steward`).

Still to do: dropping derived data by policy under disk pressure, automatic retention proposals, and the soak.

## Who it is for

On an edge machine nobody is a DBA:

- The person nearest the box is a machine operator who sees a screen.
- Next is a controls engineer who needs a ticket to restart it.
- The owner of the data may be elsewhere.

MorletDB's upkeep jobs already exist as bounded, resumable SQL calls, but nothing runs them and nothing watches the
disk. The steward runs them, rations the disk, and says plainly when a person is needed. It never deletes lossless
raw data on its own, and it never updates itself.

## Rules (from the issue, made testable)

1. **Rebuildable work is automatic.** Envelopes, chunk summaries, roll-ups, context values, verification and
   `ANALYZE`: the output can be rebuilt, so the steward runs them without asking.
2. **Anything that removes or changes stored samples needs an approved plan.** That covers memory levels 2–4,
   retention, emergency removal, extension updates and configuration changes. The steward writes the plan with its
   evidence; a person approves it; only that plan, unchanged, is applied, and by the person's role, not the
   steward's.
3. **The disk is rationed in a fixed order.** Warn in time remaining, pause builders, drop rebuildable derived data
   if a policy allows it, then refuse new ingest with a reason. Raw samples are never dropped silently.
4. **Every action leaves a record**, in the database, that survives restarts. It is the change-management evidence
   (for example for IEC 62443 change control).
5. **When in doubt, stop.** Repeated failures open a circuit breaker for that job. If the disk state cannot be read,
   all automatic work pauses.

Rule 2 is enforced by **privileges, not by the steward's code**. The steward's role cannot delete chunks or call
destructive functions successfully, so a bug in the steward cannot remove raw data.

## Shape

### Process

- **One process, `morlet-steward`,** in Python like the MCP server and the memory policy engine (`tools/memory`).
- **Shipped two ways:** as a sidecar container next to the release image (sharing the data volume read-only, for disk
  free space), and as a systemd unit for package installs.
- **No in-database background worker,** so installing stays `CREATE EXTENSION` with no `shared_preload_libraries`.
- **One loop, one budget.** Each tick (default 30 s) the steward:
  1. reads health (disk, load, backlogs);
  2. picks at most one job by priority;
  3. runs one bounded batch of it under a time budget (default 2 s of database time);
  4. records the action, then sleeps.

  Jobs never run concurrently, so they never compete with each other. Ingest always has priority.

### Roles

| Role | Holds | Can |
|---|---|---|
| `morlet_steward` | the steward's connection | run builders, verify, `ANALYZE`; write the audit, state and plan tables; set the ingest hold. Cannot delete or update chunk rows, cannot apply plans |
| `morlet_approver` | a person | approve or reject plans, apply approved plans, reset circuit breakers |
| writer roles | collectors | append; blocked by the ingest hold |
| readers, MCP | HMI, agents | read the status view; the MCP server stays read-only |

### Tables (schema `morlet_steward`, created and migrated by the steward)

| Table | Holds |
|---|---|
| `actions` | one row per action: job, collection, arguments, start, end, outcome (`done`, `nothing_to_do`, `failed`, `interrupted`), result or error |
| `state` | cursors (verify position per collection), last successful verify and backup, circuit breakers, disk history samples |
| `plans` | kind, payload, evidence, SHA-256 of the payload, created by and at, approved or rejected by and at, applied at, outcome |
| view `status` | the one-line state and its items, for people and HMIs |

## Jobs

| Job | Runs | Done when |
|---|---|---|
| envelopes | `morlet_build_envelopes` per recording with unbuilt windows, small batches | it returns 0 |
| chunk summaries | `morlet_build_chunk_summaries`, batches of chunks | returns 0 |
| roll-ups | `morlet_build_rollups` | returns 0 |
| context values | `morlet_build_context_values` for segments without values | returns 0 |
| verify | `morlet_verify` with a stored cursor, `max_recordings` per batch; a full pass per collection every 7 days by default. A *missing* chunk summary is builder backlog, not a problem; every other finding is damage | a pass completes; problems become status items |
| analyze | `ANALYZE` of tables that changed by more than 10% since the last one | — |
| housekeeping | mark memory runs left `running` by a dead process as `failed` (prerequisite in #221); mark `actions` left `started` as `interrupted` | — |

**Gating:**

- Jobs run only while the 1-minute load average is below 0.7 × CPUs (default) and the disk is not at
- **Minimum share for integrity checks:** a due verification gets one batch at least every 15 minutes
  (`--verify-share-every`), before the builders and even while the load gate is closed. Only disk pressure stops
  it. On a busy edge device the load rarely falls below the gate and the builders always have new data, so
  without this share verification never ran (soak S1, 2026-11-11). A batch has the usual budget (about 2 s),
  so the share costs well under 1% of the time.
  *pause* level.
- Each batch is limited by time, not size: the steward shrinks the batch when one runs over budget.

**Circuit breaker:** after 3 consecutive failures, that job stops. The status shows *Action required* with the
error. An approver resets it.

## Disk

- **Free space:** read by the steward from the filesystem holding the data directory (`statvfs`), because PostgreSQL
  cannot report it.
- **Growth rate:** database and WAL size, sampled every hour into `state`, over the last 7 days.
- **Time remaining:** free space minus a reserve (default 5% or 2 GB, whichever is larger) divided by the growth rate.

| Time remaining | State | The steward |
|---|---|---|
| > 14 days | Normal | nothing |
| ≤ 14 days | Attention | says when the disk will be full and what to do (add storage, approve a retention plan) |
| ≤ 3 days | Action required | also pauses builders and probe work |
| ≤ 1 day | Action required | drops rebuildable derived data (memory level 1, envelopes) only if the policy allows it, and writes the plan it would need for more |
| reserve reached | Action required | sets the **ingest hold**: appends fail with a typed error, and the collector logs and buffers |

- **Unknown disk state** (statvfs fails, a size query fails): automatic work pauses and the status says so.
- **Released hold:** when space is freed, the hold is released and work resumes, with no person needed.
- **Emergency removal of raw chunks** exists only as a policy written beforehand. It applies only to recordings with
  a checksum-verified copy elsewhere, and still goes through a plan.

**Ingest hold** (decided: (a)): new appends must stop before PostgreSQL itself fails on a full disk. Two ways:

- **(a) A flag the append functions read.** One small table lookup per append, about 10 µs. It stops existing
  sessions at once, with a clear error.
- **(b) Revoking `INSERT` from writer roles.** No extension change, but the collector sees a generic permission error.

Recommendation: (a).

## Status for people

One state for the box (*Normal*, *Attention*, *Action required*), and per item, in ISA-101 / ISA-18.2 style:

- **cause**, in time remaining where it applies ("disk full in 9 days at the current rate");
- **consequence** ("recording stops");
- **one action** ("approve retention plan 12, or add a disk");
- **who** takes it (operator, controls engineer, data owner).

Self-fixed items are logged in `actions`, not alarmed. The status always includes:

- days of recordings that exist only on this box;
- the last successful verify;
- the last backup, as reported by the backup job (#192).

The status is exposed as:

- the view `morlet_steward.status`, which an HMI polls;
- `morlet-steward check` (exit code for container and systemd health checks);
- a read-only MCP tool;
- later, the metrics endpoint (#197).

## Plans and approval

1. **The steward writes a plan.** Example: memory level 3 for the episodes the policy chose, with the evidence (the
   policy's dry run, bytes freed, recordings affected, whether a verified copy exists elsewhere). It stores the
   payload's SHA-256.
2. **A person with `morlet_approver` approves it:** `morlet-steward approve 12`, or the SQL function
   `morlet_steward.approve(12)`. Approval records who and when, and is refused when the payload changed or the
   plan's evidence is older than its validity (default 24 h).
3. **The person, or a job running as `morlet_approver`, applies it:** `morlet_steward.apply(12)`. One transaction
   applies exactly the stored payload; the existing dry-run/apply pair of `morlet_set_level` is the model. It
   commits fully or not at all.

## Crash and power-cut safety

- **Automatic jobs are idempotent and resumable**, so a batch interrupted anywhere is simply run again.
- **Every action is recorded before it starts and after it ends,** in separate transactions. On restart, `started`
  rows become `interrupted` and the job continues.
- **Plan application is one transaction,** so it cannot be half-applied.
- **The ingest hold is a committed row,** so it survives restarts until released.

## Phases

1. **P1, automatic upkeep.**
   - Builds: loop, roles, `actions`/`state`, builders, verify cursor, analyze, gating, circuit breaker, status view
     with disk time remaining (warnings only), `check`.
   - Tests: policy decisions as pure functions (unit); a database integration test that leaves backlogs and asserts
     they return to 0 with an `actions` row for every call.
2. **P2, disk pressure.**
   - Builds: pause, level-1 drop by policy, ingest hold (needs the open decision).
   - Tests: fill a small loop-mounted filesystem in CI, check the order of steps and that no stored sample is lost,
     then free space and check that everything resumes.
3. **P3, plans.**
   - Builds: `plans`, approve, apply, the approver role, upgrade and retention plans.
   - Test: no destructive action without an approved, unchanged plan, attempted through every path the steward's role
     has.
4. **P4, packaging and soak.**
   - Builds: sidecar image, systemd unit, data checksums and small-disk WAL settings in the shipped image.
   - Soak: the #191 soak on the Raspberry Pi. The home plug study D1 can be that soak's live workload.

## Decisions (2026-11-07)

1. **Language:** Python, the same as the MCP server and policy engine, and available on the Pi.
2. **Ingest hold:** (a), a flag the append functions read.
3. **Approval surface for P3:** CLI and SQL first; a screen for operators later.
4. **Defaults:**
   - tick 30 s, 2 s budget per batch;
   - load below 0.7 × CPUs;
   - a verify pass every 7 days;
   - reserve 5% or 2 GB;
   - warnings at 14 days, a pause at 3, derived data dropped (by policy) at 1.
