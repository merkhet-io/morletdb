# Design proposal: clock drift within a recording (time anchors)

Status: **proposal, decisions taken 2026-11-19** for [#393](https://github.com/merkhet-io/morletdb/issues/393) and
[#383](https://github.com/merkhet-io/morletdb/issues/383); section 8 records them. Step 1 of section 8's order
(the anchor table, batches, revisions, the mapping with segments and breaks, row security) has shipped in SQL 0.3.13;
Step 2 (the importer writes anchors from a time column, with block detection) and the first part of step 3 (MCP `locate_time`, the event importer and the SDK's `samples_for_time` and `time_ranges` use the anchors) follow it. Still open: the UTC times of search results and reads (MCP `at()`, search evidence), the collectors, `morlet_check_time_axis`, and step 4. Mapping calls are linear in a recording's anchors (#546). The SDK and file formats are
unchanged so far. SQL is illustrative: names, types and limits are subject to
implementation review. The first part of #393 has shipped, and section 1 describes it. Behaviour claims about
existing code cite `extensions/pg_morlet/sql/streams.sql` as `streams.sql:line`, and other files by their path
from the repository root. No result in this document is measured. Section 7 says what would be measured, and how.

Related: [context model](context-model.md) (which lists clock drift as not implemented),
[context workflow](context-workflow.md), [WinCC OA design](winccoa.md),
[#208](https://github.com/merkhet-io/morletdb/issues/208) (offsets between channels) and
[#383](https://github.com/merkhet-io/morletdb/issues/383) (block-stamped and irregular timestamps).

## 1. The problem

A recording is one uniformly sampled channel. Its wall-clock time comes from one start time and the declared
rate. The start time is `start_ns` in the recording metadata, or else `start`, `starttime` or `start_time`
(`streams.sql:1351-1364`). Sample *n* is at `start + n/rate`. In an acquisition, sample *n* is at
`origin_utc + offset_ns + n·1e9/rate` (`streams.sql:2726-2745`, `streams.sql:2783-2787`). Every time-to-sample
conversion assumes that the acquisition clock runs at exactly the declared rate:

- `morlet_time_to_samples` (`streams.sql:2760-2781`, stable);
- the search time filters (`morlet_time_bounds`, `streams.sql:1431-1449`);
- `before_only` (`morlet_before_ns`, `streams.sql:1459-1468`, and the acquisition cutoff, `streams.sql:2481-2490`);
- the UTC times of search evidence (`streams.sql:1924-1926`) and the end time of `morlet_latest` (`streams.sql:656`);
- the SDK's `samples_for_time` (`sdk/python/src/morletdb/_client.py:398-405`);
- the MCP tools (`tools/mcp/morlet_mcp.py:138-139`, `:614-660`).

**What stays exact.** The samples themselves stay exact, and so do sample offsets. Channels on the same DAQ
sample clock also stay aligned, because they share the clock and therefore its error. A synchronous group
(#292) requires offset 0 and clock source `daq` for every member (`streams.sql:3873-3875`), so its members
agree sample for sample whatever the clock does.

**What silently goes wrong** is everything that maps a wall-clock time from another clock onto samples:

- a lookup by UTC (`locate_time`, the search time filters);
- context from a controller, PLC or SCADA clock, placed by time;
- `before_only` history;
- joins with any other timed source.

The answers come back without an error and point to the wrong samples.

**The numbers from the issue.** On closed series-production machining data, the fitted rates were 0.2 to
1.6% off the declared ones. The time axis was off by up to 168 ms at the end of a recording of about 10 s.
At a declared 10 kHz, for example, 168 ms is 1,680 samples. A rate error of 0.2–1.6% is 2,000–16,000 ppm,
far more than a typical crystal oscillator's tolerance of tens of ppm. Such an error may come from a
declared nominal rate that the hardware does not run at, or from how the source stamps its data, rather
than from oscillator drift. This design does not depend on the cause, but the cause decides whether one
corrected rate would do (section 8, decision 1).

**Shipped so far (#393, first part; CHANGELOG 0.3.12-alpha.1).** This is detection and reporting only, with
no correction:

- **The importer.** When a CSV has a time column, the importer fits the rate the timestamps actually show
  (`tools/ingest/morlet_import.py:264-302`). It stores `time_axis` in the recording metadata: `fitted_rate_hz`,
  `rate_error_ppm`, `max_offset_s`, `end_offset_s`, and the backward and zero steps. It warns when the
  timestamps depart from start + rate by more than one sample (`morlet_import.py:294-298`).
- **MCP `locate_time`.** Each match reports `time_basis: "start + rate"`, and the measured departure as its
  uncertainty (`tools/mcp/morlet_mcp.py:142-154`, `:635-640`).
- **The event importer.** It copies `max_offset_s` into each event as `time_uncertainty_s`
  (`tools/ingest/morlet_events.py:79-81`).

## 2. Time anchors

A time anchor (sync point) says: *sample `n` of this recording was at UTC time `T`, give or take `u`,
according to `source`*. A recording has an optional, append-only list of anchors.

```sql
CREATE TABLE <c>_time_anchors (
  anchor_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  recording_id   text   NOT NULL REFERENCES <c>(recording_id) ON DELETE CASCADE,
  sample_offset  bigint NOT NULL CHECK (sample_offset >= 0),
  utc_ns         bigint NOT NULL,                     -- ns since 1970; exact, not timestamptz (µs)
  uncertainty_ns bigint NOT NULL CHECK (uncertainty_ns >= 0),   -- a bound (half-width), not a std. dev.
  source         text   NOT NULL CHECK (source <> ''),  -- 'pps', 'ptp-log', 'daq-timestamps', 'controller-event',
                                                      -- 'import:<file>', 'estimate:<method>' (#208), ...
  batch_id       text   NOT NULL,
  revision       integer NOT NULL DEFAULT 1,
  supersedes     bigint REFERENCES <c>_time_anchors(anchor_id),
  retracted      boolean NOT NULL DEFAULT false,
  break_before   boolean NOT NULL DEFAULT false,      -- starts a new segment: no interpolation across it (3.1)
  recorded_at    timestamptz NOT NULL DEFAULT now(),
  recorded_by    text NOT NULL DEFAULT current_user
);
```

The rules mirror context:

- **Idempotent writes.** `morlet_add_time_anchors(collection, batch_id, rows)` writes a batch. An identical
  retry returns 0, and a different body under the same `batch_id` is an error, as for `morlet_add_context`
  (`streams.sql:2235-2238`).
- **Corrections are revisions.** A correction supersedes or retracts an anchor and keeps the old row, as
  `morlet_revise_context` does (`streams.sql:2280`). Nothing is overwritten.
- **No silent overwrite.** A second active anchor at the same sample with a different time is an error.
- **Monotonic within a segment.** Active anchors must be strictly increasing in `sample_offset`. Within a
  segment (section 3.1), they must also be strictly increasing in `utc_ns`. A clock that steps backwards or
  stands still is the block-stamp case of #383: the writer marks it with `break_before`, and it is never
  accepted implicitly. Without a break, a backward or zero step is an error.
- **Provenance.** `source`, `batch_id`, `recorded_by` and the revision chain say where each anchor came from.
  An estimate (for example #208's cross-correlation) is labelled as such in `source` and is never presented
  as a measurement.

The start time stays what the writer declared. It is not an implicit anchor: it often comes from a host
clock that differs from the anchor source. A writer that wants it counted adds it as an anchor with source
`start`.

### Where to store anchors: companion table or recording metadata

**Recording metadata** looks simpler, because it would travel with every export and transfer that copies
metadata, and its access control is the recording's own. It fails three invariants, though:

1. **Retries.** Metadata is part of a recording's identity. `morlet_ensure_recording` refuses a recording
   whose metadata differs (`streams.sql:306-314`), and the SDK transfer compares metadata for equality
   (`sdk/python/src/morletdb/_transfer.py:237-242`). Anchors added after the start would therefore turn an
   identical writer retry, or an incremental transfer, into a conflict.
2. **History.** A JSON value has no append-only history and no batch idempotency.
3. **Cost.** Every metadata filter reads the value, so a long anchor list makes every query slower.

**A companion table** `<c>_time_anchors` keeps anchors out of the identity and gives them the revision model
above. Access control then has to be built, because RLS must hold for every companion table:

- `morlet_enable_row_security` covers a fixed list of names (`streams.sql:982-985`), so the table is added to
  that list. It takes the default policy: a row is visible and writable only when its recording is visible
  (`streams.sql:1010-1014`).
- `morlet_enable_time_anchors(collection)` applies row security when the collection already has it, as every
  enable function does (for example `streams.sql:2744`). Both enablement orders then end in the same state.
- Because anchors follow their recording, a visible recording always shows all its anchors. A role that has
  `SELECT` on the collection but not on the anchor table gets a permission error, never a silent fallback to
  start + rate.

**Recommendation:** the companion table. Recording metadata keeps the importer's `time_axis` summary as
shipped: that summary is a fact about the import, fixed when the recording is created.

**Acquisitions.** Anchors belong to recordings. For a synchronous group, sample offsets coincide on every
member, so `morlet_add_time_anchors` can copy an anchor to every member exactly. For other acquisition
members, an anchor on one channel does not give an integer sample on another, so they get their own anchors
(section 8, decision 3).

## 3. The time model with anchors

Let the active anchors of a recording be `(n_1, T_1, u_1), …, (n_k, T_k, u_k)`, sorted by sample.

- **No anchors:** start + declared rate, exactly as today. No result changes for any existing recording.
- **One anchor:** the anchor plus the declared rate. This corrects the start but not the rate.
- **Two or more, between anchors:** piecewise-linear interpolation,
  `T(n) = T_i + (n − n_i)·(T_{i+1} − T_i)/(n_{i+1} − n_i)` for `n_i ≤ n ≤ n_{i+1}`.
- **Before the first or after the last anchor:** extrapolation with the slope of the nearest anchor interval.
  The result is labelled `extrapolated`, and the reported uncertainty grows with the distance (below).

**Exact arithmetic.** `n_i`, `T_i` and the queried time are integers, and every slope is a ratio of
integers. Time to sample is therefore an exact rational, and the server takes its `ceil` in integer
`numeric` arithmetic:

- The interval `[t_a, t_b)` maps to `[ceil(n(t_a)), ceil(n(t_b)))`, the first sample at or after each end.
  This is the rule of `morlet_time_to_samples` (`streams.sql:2760-2781`), of `morlet_ns_to_samples`
  (`streams.sql:1405-1420`) and of the collectors since #480 (`examples/collectors/opcua/context_rows.py:15-19`).
- An event at `t` goes to `ceil(n(t))`.
- At an anchor, `n(T_i) = n_i` exactly, which is #393's done-when criterion.

When a mapping uses the declared rate, it uses the rate as the server's exact fraction (`morlet_rate_ratio`,
`streams.sql:1366-1371`, #343). Sample to time returns `numeric` nanoseconds, as `morlet_sample_time_ns`
already does. A rounded time is only ever displayed; no sample decision uses it.

Two existing client conversions differ from this rule. MCP `locate_time` and the event importer use `floor`
in float64 (`tools/mcp/morlet_mcp.py:633`, `tools/ingest/morlet_events.py:75`). The collectors compute
`Fraction(rate)` of the float, which for 1/60 Hz is not the server's 1/60. Anchors are a good reason to move
these conversions to one server function. `locate_time` keeps its documented meaning: the sample whose
period contains the time, which is the exact `floor`.

**Uncertainty is a bound, propagated by addition.** The sources are not known to be independent, so no
root-sum-square is used. With times in seconds and distances in samples, the reported uncertainty between
two anchors is

```
u(n) = (1−λ)·u_i + λ·u_{i+1} + 2ρ·min(n − n_i, n_{i+1} − n)/rate,     λ = (n − n_i)/(n_{i+1} − n_i)
```

where ρ is a declared bound on how much the clock's rate varies (fractional, for example 50 ppm as 5e-5).
If the rate stays within ±ρ of some value, the clock departs from the chord by at most that last term.

Outside the anchors, it grows linearly with the distance `d` from the nearest anchor, whose uncertainty is
`u_end`; the first term is the slope error of the nearest anchor interval `[n_i, n_{i+1}]`:

```
u = u_end + ((u_i + u_{i+1})/(n_{i+1} − n_i) + 2ρ/rate)·d
```

When ρ is not declared, the mapping is still computed. Its uncertainty is reported as `assumes_linear_clock`,
and it is never presented as a bound.

**What never changes.** Samples are never resampled or interpolated, and calibration is never changed.
Anchors change only the mapping between sample offsets and wall-clock time. Raw chunks are never rewritten,
and the samples stay authoritative. Retracting every anchor restores today's mapping exactly.

### 3.1 Segments and breaks (block-stamped sources, #383)

Some acquisition systems stamp samples per block: spacing is constant inside a block, and between blocks the
stamp jumps, sometimes backwards or by zero. Interpolating across such a jump would invent a rate that the
source never had. An anchor with `break_before = true` therefore starts a new **segment**:

- **Segments.** The active anchors split into segments at every break. A segment covers the samples from its
  first anchor up to the next segment's first anchor (the last segment: up to the end of the recording).
  Samples before the first anchor belong to the first segment.
- **Inside a segment**, the rules above apply: interpolation between its anchors, extrapolation with the slope
  of its nearest anchor interval, or the declared rate when the segment has one anchor. For a block-stamped
  source, every block start is an anchor with a break, so each block maps as `T_i + (n − n_i)/rate`, exactly
  what the source states.
- **Sample to time** stays a function: every sample belongs to exactly one segment.
- **Time to sample** may not be. After a forward jump, a time can fall between two segments (a **gap**): it maps
  to the first sample of the next segment, the first sample at or after it, and the result is marked `gap`.
  After a backward or zero step, a time can fall in several segments (an **overlap**): every candidate is
  returned, and the result is marked `ambiguous`. A function that returns one sample returns the lowest
  candidate and says so; it never picks silently.
- **At every anchor**, `n(T_i) = n_i` still holds when `T_i` is unambiguous, which is #383's done-when
  criterion ("exact time lookup at every block start"). When an overlap makes `T_i` ambiguous, `n_i` is among
  the returned candidates.
- **Uncertainty** within a block-stamped segment grows one-sided from its anchor:
  `u(n) = u_i + ρ·(n − n_i)/rate`, with ρ as in section 8, decision 2.

The importer writes such anchors from a time column (section 4): a new block starts where the step between two
timestamps departs from the declared period by more than a tolerance (half a period by default), and every
block's first sample gets an anchor with `break_before`. Recordings whose time column shows no such steps get
the drift anchors of section 4 instead, without breaks.

## 4. Detection and reporting

`morlet_check_time_axis(collection, recording_ids text[] DEFAULT NULL)` returns one row per visible recording:

- `basis`: `anchors`, `start+rate` or `none`;
- the number of anchors;
- the declared rate, and the fitted rate between the first and last anchor (exact, `(n_k − n_1)/(T_k − T_1)`);
- `rate_error_ppm`;
- `max_time_error_ns` and `max_time_error_samples`: the largest departure of start + rate from the anchors,
  and the sample where it occurs;
- `nonlinearity_ns`: the largest residual of the anchors from one straight line, which says whether one
  corrected rate would do;
- `status`, as below.

Without anchors, it reports the importer's `time_axis` evidence when it exists. An end time that the writer
knows, for example from a file's last timestamp, is simply a second anchor.

Each status has a threshold, chosen for the following reasons:

| Status | Condition | Why |
|---|---|---|
| `not_checked` | no anchors and no import evidence | Missing evidence must not read as "no drift" |
| `ok` | start + rate within one sample period of every anchor | Below one period, a `ceil` placement moves by at most one sample; this is the importer's threshold (`morlet_import.py:294`) |
| `drift` | more than one sample period | Lookups by time land on other samples than start + rate says |
| `exceeds_bound` | more than the clock bound in use: a channel's `offset_uncertainty_ns`, or a caller's bound such as WinCC OA's 50 ms | Context is then placed outside its declared uncertainty, and edge trimming (`streams.sql:2336-2337`) no longer covers it |
| `inconsistent` | an anchor departs from the interpolation of its neighbours by more than their combined bound under the declared ρ | A wrong anchor, a clock step or a wrong ρ. A person must look |

The rate error in ppm is reported but has no threshold of its own. What matters to a user is the time error,
which grows with the duration.

**Where it surfaces:**

- **Coverage.** The MCP `coverage` and `locate_time` tools report `time_basis`, the uncertainty and
  `extrapolated`. `morlet_coverage` itself (experimental) keeps its columns, and its time questions are
  answered by the new function.
- **Steward checks.** A read-only `time_axis` check: `attention` for `drift` or `exceeds_bound` without anchors
  that correct it, and `action_required` for `inconsistent` (`tools/steward/policy.py`). Drift that anchors
  correct is reported as normal.
- **The SDK.** `Client.time_axis(recording)` returns the check, and `samples_for_time` accepts
  `basis="anchors"`.
- **The importer.** It writes anchors from a time column. It keeps the first and last row and adds rows until
  the piecewise-linear model is within a tolerance of every timestamp (half a sample period by default).
  This bounds the number of anchors while keeping the stated error.

## 5. Effects on the features that use time

- **`locate_time`** (MCP, `tools/mcp/morlet_mcp.py:614-660`) calls the server mapping. It reports `time_basis`
  (`anchors`, `start + rate`), the uncertainty in seconds and samples, and whether the result is extrapolated.
  The tool stays read-only, and every new function it calls is `STABLE`.
- **Context placement.** `morlet_add_context` takes samples (`streams.sql:2235-2238`) and does not change.
  `morlet_add_acquisition_context` takes acquisition time, the DAQ's nominal axis (`streams.sql:2877-2910`), and
  does not change either: for channels on that DAQ clock, acquisition time is exact. What changes is the step
  before them, UTC to samples. A new `morlet_utc_to_samples(collection, recording_id, t_start_ns, t_end_ns)`
  returns the sample range, the uncertainty and the basis, and the collectors, the event importer and the WinCC
  OA companion call it instead of computing `(t − start)·rate` themselves.
  - Context placed before anchors existed is not moved automatically. Re-placing it is an explicit revision.
  - To make stale placements findable, `morlet_time_axis_version(collection, recording_id)`, a digest of the
    active anchors, can be recorded with every derivation.
- **The WinCC OA clock bound** (`docs/winccoa.md`, section 3) bounds the offset between the WinCC OA host and
  the DAQ host, 50 ms by default. Drift inside a recording adds to it: hosts within 10 ms of each other can still
  put a state 168 ms off at the end of a drifting recording. The companion therefore adds the time-axis error at
  the placed sample to the host bound. When the sum exceeds the configured bound, it stops deriving
  (`clock_exceeded`). It records the time-axis version in its `derivation` table.
- **Search results.** Ranking, ties and budgets work on samples and do not change (`docs/search-contract.md`).
  - The UTC times attached to matches (`streams.sql:1924-1926`; MCP `at()`, `tools/mcp/morlet_mcp.py:138-139`)
    gain a basis and an uncertainty.
  - The time filters and `before_only` keep start + rate in the stable functions for now (section 8, decision 4).
    Near their boundaries, a drifting recording can be included or excluded by up to the reported error.
  - A conservative `before_only` would subtract the uncertainty, so that history never includes the future.
- **The viewer** (`tools/viewer`) shows the basis, and the uncertainty band where it exceeds one sample. It
  draws the samples unchanged.
- **Export, the file format and transfers.** Anchors are provenance, so they must travel.
  - **The exchange format** gets version 2, with the anchors per recording in `morletdb.exchange`. Today's
    importer refuses any version other than 1 (`tools/ingest/morlet_import.py:214-216`), so an older reader
    fails rather than quietly using start + rate.
  - **File format 1** allows extra tables, but its writers may not rely on them (`docs/file-format.md`).
    Anchors therefore need file format 2 (`user_version` 2) with a `time_anchors` table, once the embedded
    edition implements the mapping.
  - **Transfers.** Until then, a server-to-file transfer of a recording with anchors fails, unless the caller
    passes an explicit `drop_time_anchors` that the report names. Transfers compare anchors as they compare
    metadata: identical is a no-op, different is a conflict.
  - **One mapping everywhere.** The mapping lives in `crates/morlet-core`, which has no PostgreSQL dependency,
    for the embedded edition. One set of test vectors checks it against the SQL implementation.

## 6. Compatibility and API

**New, all experimental:**

- `morlet_enable_time_anchors`;
- `morlet_add_time_anchors`;
- `morlet_revise_time_anchor`;
- `morlet_utc_to_samples`;
- `morlet_sample_utc`, which returns the time, the uncertainty and the basis;
- `morlet_check_time_axis`;
- `morlet_time_axis_version`.

**Stable functions do not change.** No stable signature or documented behaviour changes
(`docs/api-stability.md`): `morlet_time_to_samples`, `morlet_sample_time_ns`, `morlet_add_context`,
`morlet_add_acquisition_context` and the search functions keep their arguments and results. Anchor-aware time
filters in search would change documented behaviour, so they need a new argument or function and are deferred.
`morlet_enable_row_security` keeps its signature and covers one more table name, a change of the same kind as
the probe and memory tables.

**SQL version.** The step is the next SQL version after the current one, 0.3.12 → 0.3.13 at the time of writing (0.3.12
is #488's fix; `extensions/pg_morlet/pg_morlet.control`). It needs:

- `pg_morlet--0.3.12--0.3.13.sql`, which creates the functions and replaces `morlet_enable_row_security`;
- new rows in `api/functions.tsv`;
- an unchanged `api/stable.txt`;
- upgrade tests that the upgraded catalog equals a fresh installation.

No existing table changes, and anchor tables exist only where a collection enables them. The exchange-format
version 2 and the file-format version 2 are separate decisions (section 5) and can follow the SQL step.

## 7. Tests and evidence plan

**Synthetic drift with known truth.** The generator declares a rate R and runs the true clock at R·(1+ε):

- ε is 0, ±50 ppm, ±2,000 ppm and ±16,000 ppm;
- some recordings get a slowly varying rate within a known ρ;
- the rates include 25.6 kHz, 1/60 Hz and 12345.678901234 Hz.

Anchors are drawn at known true times, with noise within ±u. The expected values come from an independent
implementation in Python `fractions`, which shares no code with the SQL. Golden expected values are never
regenerated to make a test pass. The tests check that:

1. at every anchor, `morlet_utc_to_samples(T_i)` returns `n_i`, and `morlet_sample_utc(n_i)` returns `T_i`;
2. for every sample, the true time lies within the reported uncertainty when ρ is declared;
3. the fitted rate is within `(u_1 + u_k)/(T_k − T_1)` of the true rate, a tolerance derived beforehand, not
   tuned;
4. recordings without anchors give results identical to before, through every function listed in section 1;
5. the samples read back with an identical SHA-256 before and after anchors are added and revised;
6. an identical batch retry returns 0, a conflicting one fails, and both commit and rollback leave a
   consistent state;
7. RLS holds for a non-owner role: direct `SELECT`, `INSERT`, `UPDATE` and `DELETE` on `<c>_time_anchors`, a
   hidden recording's anchors neither readable nor writable, and both enablement orders (anchors before and
   after `morlet_enable_row_security`), as in `extensions/pg_morlet/tests/acquisition_rls.sh`;
8. a transfer with anchors either carries them or fails, and an export round trip is identical.

**Before any confirmation run, the protocol is frozen.** It fixes:

- the datasets, and which anchors are used for fitting and which are held out (for example, every other
  anchor held out);
- ρ and the importer's tolerance, chosen on development data only;
- the metrics, which are the error at held-out anchors against start + rate and against the anchor model, and
  whether each lies within its reported bound.

A run then counts as a pass when:

- every held-out error lies within its reported bound (any violation is reported, and fails the bound claim);
- on recordings with `drift` status, the anchor model's held-out error is below the larger of one sample
  period and the anchor uncertainty.

Results are reported for the workload measured and are not generalized beyond it. A failure is published
like a pass.

## 8. Decisions (2026-11-19)

The owner decided the open questions as follows. Question 1 needs the data owner and does not block the
implementation.

1. **Stable wrong rate or varying drift, and the confirmation data.** Open; asked of the data owner. The design
   handles both: a stable wrong rate needs few anchors, a varying drift needs more. Development and the frozen
   confirmation run on synthetic data with known truth (section 7), and on a public dataset with real block
   timestamps if one exists (named and approved before the freeze). The closed production data is used only
   with its owner's consent; until then, the docs say that the anchor model is not confirmed on production data.
2. **ρ: a documented default per clock source, overridable.** The defaults are conservative and not measured:
   1e-4 (100 ppm) for measured sources (`pps`, `ptp-log`, `daq-timestamps`, `controller-event`, `import:*`,
   `start`), and none for `estimate:*`, whose mapping is reported as `assumes_linear_clock`. A caller passes
   its own ρ per call, or a recording declares it in its metadata as `time_axis.rho`. The defaults may change
   only with development evidence, recorded in the changelog.
3. **Anchors per recording.** Per acquisition in acquisition time is deferred. For a synchronous group,
   `morlet_add_time_anchors` copies an anchor to every member exactly (section 2).
4. **Anchor-aware search time filters and `before_only`: yes, as an opt-in.** A new argument or function with
   the anchor basis; the stable functions keep start + rate, so no existing result changes. The anchor-aware
   `before_only` subtracts the uncertainty, so that history never includes the future. It follows the first
   SQL step.
5. **#383's backward and zero steps: breaks in the anchor list, one design** (section 3.1).
6. **The SDK's `samples_for_time` uses anchors by default when they exist,** and every result states its basis
   (`anchors` or `start + rate`), its uncertainty and whether it is `extrapolated`, `gap` or `ambiguous`. A
   mapping known to be wrong is not the default. `basis="start+rate"` keeps the old behaviour. The SDK is
   alpha, so this is a documented change in its changelog.

**Order of work:**
1. SQL: the anchor table with breaks, `morlet_enable_time_anchors`, `morlet_add_time_anchors`,
   `morlet_revise_time_anchor`, `morlet_utc_to_samples`, `morlet_sample_utc`, `morlet_check_time_axis`,
   `morlet_time_axis_version`, row security, upgrade script.
2. The importer writes anchors from a time column, with block detection; #383's done-when on synthetic block
   data.
3. MCP `locate_time`, the event importer, the collectors and the SDK call the server mapping.
4. The mapping in `crates/morlet-core`, with shared test vectors; anchor-aware search (decision 4); the exchange
   format 2 and file format 2.
