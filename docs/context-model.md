# Design proposal: synchronized channels, context timeline and compare-to-history

Status: **proposal**, not implemented. Nothing here changes the current SQL or
Rust API. SQL in this document is illustrative: names, types and limits are
subject to implementation review. Related documents: the
[CNC spindle experiment spec](cnc-spindle-experiment.md),
[demo D02](demo-catalog.md#d02--spindle-and-cutting-tool-investigation),
[durable ingestion](durable-ingestion.md) and the
[wave-RAG annotation design](wave-rag-spec.md).

## 1. Problem and non-goals

A condition-monitoring engineer on a CNC machine tool asks: **"Is this cut
different from the same cut on previous parts?"** "The same cut" is defined by
controller context: NC program, block, tool, commanded feed and spindle speed,
and the part. "Different" means, for example, an air cut (the tool moves at feed
without engaging material), tool wear, chatter or a wrong setup. The machine
records three-axis vibration, motor currents, temperature and controller
signals at once. A roughing block and a finishing block differ for reasons that
are not faults, so the comparison is meaningful only within the same context.

### What MorletDB does today

A recording is one uniformly sampled, append-only channel stored as lossless
encoded chunks. Segments are fixed windows (512..4096 samples) with
`morlet-energy-v1` features computed at append time, or none with
`extractor => 'none'`. Context is recording-level JSONB `metadata`. Search is an
exact, budgeted scan that errors on incompatible representations; functions run
as SECURITY INVOKER over caller-owned tables with RLS. Nothing relates two
recordings in time or describes what the machine did inside a recording.

### Evidence that shapes this proposal

- **Morlet features have not beaten simple baselines** in frozen held-out tests
  (Paderborn, seismic timing; see the [README](../README.md#what-has-been-measured)).
  This design therefore does not depend on Morlet features. It starts with
  simple, explainable values: RMS, band energies, duration.
- **Materialized envelopes** stored next to raw returned the same top-10 as
  decoding raw, at 0.10× the p95 latency, but cost 27.9% of raw storage against a
  25% bound ([R1 results](../evaluation/materialized/results/eval-2025/RESULTS.md)).
  Derived values stored as replaceable representations are useful; their size
  must be measured.

This proposal moves the question from "where else does this look similar?" to
"how does this compare with the same context in the past?". Whether that is more
useful is a hypothesis; section 8 proposes how to test it.

The [CNC spindle spec](cnc-spindle-experiment.md) gates *Morlet-based*
context-aware retrieval on a Morlet advantage; this proposal does not assume
one. The [architecture](architecture.md) requires partner evidence before
multichannel synchronization, so phase 2 is gated on the phase 1 pilot.

### Non-goals

- Lossy retention, downsampling of originals, or automatic deletion.
- ML models, learned embeddings, automatic diagnosis or calibrated probabilities.
- A new index type or access method. All scans stay exact and budgeted.
- A specific controller protocol; adapters outside the database handle them.
- Time warping (DTW) in the first phases.

## 2. Acquisitions and synchronized channels

### Time bases

| Clock source | Typical channels | Alignment |
|---|---|---|
| Shared DAQ sample clock | Vibration X/Y/Z, sometimes current | Exact offsets in samples; different rates are derived from one clock |
| Separate logger, disciplined wall clock (PTP/NTP) | Current, temperature | Offset plus uncertainty; drift possible |
| Controller | Program, block, tool, feed, speed; sampled 100 Hz–1 kHz or on change | Controller timestamps; offset to DAQ must be estimated |

Example rates: vibration 25.6 kHz, current 10 kHz, controller signals
100 Hz–1 kHz or event-based. Channels are **never resampled** into a common
rate. Each continuous channel stays its own recording at its native rate.
Numeric controller signals sampled at a fixed rate (spindle load, actual speed)
can be raw-only recordings (`extractor => 'none'` accepts any rate up to
200 kHz). Event-based values (block number, tool number) go to the context
timeline (section 3), not into recordings.

### Acquisition time

Each acquisition defines one time axis: **acquisition time**, in integer
nanoseconds from the acquisition origin, with an optional UTC anchor for display.
Each member channel declares where its sample 0 lies on that axis:

```
t(n) = offset_ns + n * 1e9 / sample_rate_hz        (channel sample n)
```

A time interval `[ta, tb)` maps to the half-open sample interval
`[ceil((ta - offset_ns) * rate / 1e9), ceil((tb - offset_ns) * rate / 1e9))`,
computed in `numeric` to avoid float rounding at boundaries. Sample `n` belongs
to the interval when its timestamp `t(n)` does. The same rule is used for
segmenting, reading and labels, so a time interval always maps to the same
samples.

25.6 kHz has a non-integer period in nanoseconds (39,062.5 ns), so stored
interval bounds are times and the mapping to samples is computed.

### Minimal schema (illustrative)

Two sibling tables of an existing stream collection. Existing recordings,
chunks and segments are untouched; an acquisition references them.

```sql
CREATE TABLE signals_acq (
  acquisition_id text PRIMARY KEY,
  origin_utc     timestamptz,             -- display anchor; may be NULL
  machine        text NOT NULL,
  metadata       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata)='object')
);

CREATE TABLE signals_acq_channels (
  acquisition_id text NOT NULL REFERENCES signals_acq ON DELETE CASCADE,
  recording_id   text NOT NULL REFERENCES signals(recording_id),
  role           text NOT NULL,           -- e.g. 'vib_x', 'spindle_current'
  offset_ns      bigint NOT NULL,         -- acquisition time of sample 0
  clock_source   text NOT NULL CHECK (clock_source IN ('daq','wallclock','controller')),
  offset_uncertainty_ns bigint,           -- NULL = exact (shared DAQ clock)
  PRIMARY KEY (acquisition_id, role),
  UNIQUE (recording_id)                   -- a recording belongs to one acquisition
);
```

- Rate, channel and unit stay on the recording row. A recording without an
  acquisition works exactly as today.
- Clock drift is not modelled in phase 1. A later option is per-channel sync
  points (piecewise-linear), fitted on a transient seen on all channels.
- After a gap a new recording starts, with its own `offset_ns`. How several
  recordings share one role is an open question.

## 3. Context timeline

### Intervals and events

Context is a set of time-ranged facts on the acquisition time axis:

```sql
CREATE TABLE signals_ctx (
  ctx_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  acquisition_id text NOT NULL REFERENCES signals_acq ON DELETE CASCADE,
  kind           text NOT NULL CHECK (kind IN ('interval','event')),
  key            text NOT NULL,           -- 'program','block','tool','feed_cmd',
                                          -- 'speed_cmd','part','operation','alarm',...
  value          jsonb NOT NULL,          -- string, number or object
  t_start_ns     bigint NOT NULL,
  t_end_ns       bigint,                  -- NULL for events; > t_start_ns for intervals
  source         text NOT NULL,           -- adapter and file/topic, free text
  batch_id       text NOT NULL,           -- ingest batch, for idempotent retries
  revision       integer NOT NULL DEFAULT 1,
  supersedes     bigint REFERENCES signals_ctx(ctx_id),
  retracted      boolean NOT NULL DEFAULT false,
  recorded_at    timestamptz NOT NULL DEFAULT now(),
  recorded_by    text NOT NULL DEFAULT current_user,
  CHECK ((kind='event') = (t_end_ns IS NULL))
);
```

- **Key/value, not typed columns.** Controllers differ; a fixed column set
  would need a migration per new key. Well-known keys (program, block, tool,
  feed_cmd, speed_cmd, part, operation, tool_change, alarm) are documented with
  value types; units per key are an open question.
- **Events** have one timestamp: tool change, alarm, operator stop.
- **Commanded vs actual.** `feed_cmd` and `speed_cmd` are commanded values.
  Actual speed is a signal and belongs in a recording; overrides are their own key.

### Overlap and nesting

- Different keys overlap freely: a `program` interval contains many `block`
  intervals; a `tool` interval spans several blocks; a `part` interval spans a
  whole program run.
- For one key in one acquisition, active (non-superseded, non-retracted)
  intervals must not overlap. A block executed twice appears as two intervals.
  Enforcement is either a GiST exclusion constraint (needs the `btree_gist`
  extension) or a check in the ingest function. This is an open question.
- Touching intervals (`t_end` of one equals `t_start` of the next) do not overlap,
  consistent with half-open sample intervals elsewhere.

### Ingestion

The database accepts one canonical row format through one function, for
example `morlet_add_context(collection, acquisition_id, batch_id, rows jsonb)`.
Adapters outside the database convert controller logs, OPC UA or MQTT exports
and CSV into that format, and convert controller timestamps to acquisition time
with the channel's declared offset. The adapter records which clock its
timestamps came from.

As in durable ingestion, an identical retry of a `batch_id` is verified and
ignored; a changed retry is an error. Adapters turn change-on-value streams into
intervals; an interval open at the end of a batch is closed by a later batch or
an end-of-acquisition marker.

### Corrections and versioning

Rows are append-only. A correction (wrong part number, block boundaries shifted
after a clock-offset fix) inserts a new revision that `supersedes` the old row;
a retraction sets `retracted` on a new revision. Default reads use the active
revisions in the caller's snapshot. Explicit reads by `ctx_id` still return the
old row, so earlier results stay explainable. This follows the annotation
revision rules in the [wave-RAG spec](wave-rag-spec.md).

## 4. Context segments and derived representations

### Segmenting by context

A **context segment** is one execution of a context key on one channel, for
example one execution of (program, block, tool) for one part. It replaces the
fixed window as the unit of comparison for this workflow. Fixed-window segments
keep working unchanged.

```sql
CREATE TABLE signals_ctx_segments (
  segment_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  acquisition_id text NOT NULL REFERENCES signals_acq ON DELETE CASCADE,
  recording_id   text NOT NULL REFERENCES signals(recording_id) ON DELETE CASCADE,
  start_sample   bigint NOT NULL,
  end_sample     bigint NOT NULL CHECK (end_sample > start_sample),
  context        jsonb NOT NULL,          -- e.g. {"program":"O1234","block":"N120","tool":7}
  occurrence     jsonb NOT NULL,          -- e.g. {"part":"SN-0042"}
  ctx_ids        bigint[] NOT NULL,       -- context revisions it was built from
  spec           jsonb NOT NULL,          -- segmentation rule and its version
  stale          boolean NOT NULL DEFAULT false
);
```

A segmentation spec is explicit and versioned, for example:
`{"version":"ctx-seg-v1","keys":["program","block","tool"],"split_by":"part",
"trim_start_ms":0,"trim_end_ms":0,"min_duration_ms":50}`. Segments are built by
an explicit call, never implicitly:

- one segment per maximal intersection of the active intervals of `keys`,
  per channel, mapped to samples with the rule in section 2;
- intervals shorter than `min_duration_ms`, or not fully covered by committed
  samples, are reported and skipped, not padded;
- the segment records the context revisions used. When one of them is
  superseded, the segment is marked `stale`. It is rebuilt explicitly, never
  silently changed.

Segment boundaries inherit the context clock's uncertainty (possibly
milliseconds for a controller clock); trims can exclude uncertain edges.

### Derived representations

Derived values attach to segments as **materialized representations**: stored
next to raw, versioned, replaceable, rebuildable from raw. They are not
indexes, and raw samples stay the source of truth.

Illustratively: `signals_repr(repr_id, name, version, params jsonb,
value_names text[])` describes a representation (bands, tooth count, envelope
bin width in `params`), and `signals_repr_values(segment_id, repr_id, vals
float4[])` holds scalars or a sequence such as envelope bins, one row per
segment and representation.

Candidate representations, in order of priority:

| Representation | Values | Notes |
|---|---|---|
| `simple-v1` | duration, RMS, peak, crest factor, kurtosis | Length-independent except duration |
| `bands-v1` | Energy per second in fixed FFT bands | Band edges in `params` |
| `tpf-v1` | Energy at tooth-passing frequency and harmonics | Needs tooth count and speed; uses commanded speed unless an actual-speed channel exists; records which |
| `env-v1` | Envelope at a fixed time bin (for example 10 ms) | Variable length; size to be measured as in R1 |
| `morlet-energy-v1` pooled | Existing features, pooled over the segment | For comparison only |

Rules:

- Building is synchronous and explicit, for example
  `morlet_build_representation(collection, repr_id, segment_filter, max_segments)`.
  It reads raw like `morlet_read_interval` and errors above `max_segments`.
- New parameters or algorithm means a new `repr_id`. Old values remain until
  dropped explicitly. Representations are never mixed in one comparison.
- Values are per channel; cross-channel values wait for phase 2.

## 5. Compare-to-history query

### Sketch (illustrative)

```sql
morlet_compare_to_history(
  collection     regclass,
  segment_id     bigint,                    -- or an overload with recording + [start,end)
  repr_id        text,
  match_keys     text[]  DEFAULT ARRAY['program','block','tool'],
  history_filter jsonb   DEFAULT '{}',      -- containment on context/occurrence/metadata
  before_only    boolean DEFAULT true,      -- only segments that started earlier
  duration_tolerance float8 DEFAULT 0.10,   -- relative; for sequence values
  min_history    integer DEFAULT 20,
  top_k          integer DEFAULT 10,
  max_candidates integer DEFAULT 4096
) RETURNS TABLE (
  value_name text, value float8,
  hist_n integer, hist_median float8, hist_mad float8,
  robust_z float8, quantile float8,          -- NULL when not computable, with a reason
  status text                                -- 'ok','insufficient_history','zero_spread'
);

morlet_history_neighbors(... same arguments ...)
RETURNS TABLE (segment_id bigint, recording_id text, start_sample bigint,
  end_sample bigint, occurrence jsonb, distance float8, labels jsonb);
```

### Semantics

- **History population.** Segments on the same channel role, with equal values
  for every key in `match_keys`, the same `repr_id`, containment of
  `history_filter`, visible to the caller. The query segment and every segment
  of the same occurrence (same part) are excluded. With `before_only`, only
  segments that started earlier in UTC (or in acquisition order when no UTC
  anchor exists) are used, so the score never uses the future.
- **Rate and unit** must match, as in existing search. No resampling.
- **Deviation.** Per value: `robust_z = (x - median) / (1.4826 * MAD)` and the
  empirical quantile of `x` with `hist_n` reported beside it. `MAD = 0` gives
  `NULL` with status `zero_spread`; fewer than `min_history` segments give
  `NULL` with status `insufficient_history`. No value is invented.
- **Summary score.** Maximum absolute robust z over the selected values, with
  the number of values tested. Several values tested at once inflate false
  alarms; the score is a ranking aid, not a test statistic.
- **Neighbors.** Ranked by Euclidean distance over values scaled by the history's
  robust spread, ties broken by recording ID and start sample. Each row links to
  a raw interval readable with `morlet_read_interval`, and carries any labels
  (section 6).
- **Budget.** If the history population exceeds `max_candidates`, the call
  raises an error asking for a narrower filter. It never samples or truncates
  silently. `top_k` is 1..100, as today.
- **Errors.** Mixed or missing `repr_id` values, stale segments in the
  population, and missing context keys on the query segment are errors. Stale
  segments can be included only with an explicit flag (open question).

### Variable segment lengths

The same block can take different durations (feed override, dwell, interruption).

- **Scalar values** (RMS, crest, kurtosis, band energy per second) are defined
  to be length-independent. Duration is itself a value and often the first
  signal: an air cut at an override, a skipped block.
- **Sequence values** (envelopes) are compared only when durations are within
  `duration_tolerance` of the query, over the common prefix of bins. History
  segments outside the tolerance are counted and reported, not compared.
- Linear stretching to a common length is a candidate to evaluate, not a
  default. Time warping is out of scope until scalar comparison is evaluated.

## 6. Labels

Engineer labels are ordinary rows, not model output. An illustrative `signals_labels` table holds: `label_id`, `acquisition_id`,
an optional `segment_id`, a time range `[t_start_ns, t_end_ns)`, `label`
(`air_cut`, `chatter`, `worn_tool`, `normal`, ...), free-text `note`, `author`
(default `current_user`), `created_at`, and `supersedes`/`retracted` for
revisions, as for context rows.

- Labels refer to a time range, so they survive segment rebuilds.
- `author` comes from the session role, not a client argument.
- **Gallery use.** `morlet_history_neighbors` returns labels overlapping each
  neighbor. An engineer sees "the three closest past executions of this block
  were labelled air cut".
- **Evaluation use.** An evaluation freezes a label set by exporting it with
  its label IDs, revisions and a hash. Labels added later do not change a frozen
  result.
- **Permissions.** SECURITY INVOKER functions over caller-owned tables. Label,
  context and segment visibility derive from the parent recordings, including
  through joins. Label writes are granted separately from reads. Tests use
  ordinary roles with RLS, as in the existing suites.

## 7. Migration and compatibility

- Ship as extension version **0.3.0** with `pg_morlet--0.2.0--0.3.0.sql`,
  generated and checked like the 0.1.0→0.2.0 script. The update adds functions
  only.
- Sibling tables are created per collection by an explicit owner call, for
  example `morlet_enable_context('public','signals')`. Collections that never
  call it are unchanged. The call works on collections created before 0.3.0,
  including `float8[]` chunk collections.
- No existing function changes signature or result. Fixed-window segments,
  feature search and raw-only recordings are unaffected.
- The longest table-name suffix must fit the 63-byte identifier limit with a
  46-byte collection name (17 bytes or fewer, for example `_ctx_segments`).
- The CI `upgrade` job is extended: populate a database with the 0.2.0 release,
  update to 0.3.0, check identical results for existing APIs, enable context on
  an old collection, and compare the catalog with a fresh 0.3.0 install.

## 8. Validation plan (proposals)

Both studies are **proposals**. Neither has been run. Protocols are frozen,
with a commit hash, before any held-out data is inspected, as in R1.

### 8a. Public data: context-conditioned vs context-free

**Hypothesis.** Comparing a cut against history with the same operating
context detects tool wear better than context-free similarity over the same
features.

**Candidate dataset: NASA milling** (PCoE repository; see D02). To verify
before use, from the provider's documentation: terms of use and
redistribution; the actual channels, units and sample rates (D02 lists spindle
and table vibration and motor current; acoustic emission may also be present);
the operating conditions (reported as varying depth of cut, feed and material);
how flank wear was measured and how often; how many cases and cutters exist and
whether a case equals one cutter.

Limits to record in advance:
- The dataset has no NC block context. It tests conditioning on operating
  context only, not block segmentation.
- Wear grows with time in use. A **time-in-use baseline** (run index or
  cumulative cutting time) must be included; a method that only tracks elapsed
  use is not evidence of signal value.
- The number of cutters is likely small (verify). Grouped bootstrap intervals
  over cutters will be wide; report them as they are.

**Protocol sketch.**
- Unit: one run (cut) per channel, as one context segment. Context key:
  (depth of cut, feed, material).
- Split: whole-cutter holdout. Thresholds and feature choices are fixed on
  development cutters only.
- History: runs of the reference cutters only. Methods, on the same runs and
  the same `simple-v1` and `bands-v1` values: (1) robust z against reference
  runs with the same context; (2) context-free nearest-neighbor distance to all
  reference runs; (3) time-in-use; (4) pooled Morlet features, for reference.
- Outcome: a wear threshold fixed from the documentation before the run (verify
  the wear criterion used by the provider). Metrics: AUROC of each score for
  "worn", per-cutter results, paired differences with grouped bootstrap 95%
  intervals.
- Decision: support only if (1) beats both (2) and (3) with intervals excluding
  zero. Otherwise report "not supported" and keep the infrastructure decision
  separate from this result.

### 8b. Pilot on real machine data

**Setting.** One machine, an approved data agreement, NC block context from the
controller, at least vibration and spindle current, and an event log written
independently of MorletDB (operator notes, tool-change records, scrap reports).

**Known events.** Air cuts (planned or reproduced deliberately), tool changes
and tool breakage, chatter episodes confirmed by an engineer, wrong setups.
Each event has a time range and a label in the frozen set.

**Measures.**
- Detection: fraction of known events whose segments rank in the top deviations
  at a fixed alarm budget (for example alarms per shift), chosen in advance.
- False alarms: alarms per 100 part cycles on periods reviewed as normal, with
  each alarm reviewed and categorized (real but unlogged, context error, clock
  error, noise).
- Time-to-cause: an engineer's time from an alarm to a stated cause, with and
  without compare-to-history, on matched events. Small n; report each case.
- Context quality: share of segments with missing or corrected context; the
  measured controller-to-DAQ clock offset.

**Decision.** Phase 2 starts only if engineers judge the alarms useful and the
false-alarm rate is acceptable to them at the chosen budget.

## 9. Open questions and phased plan

### Open questions

1. Key registry: free-form keys with documentation, or a registry table with
   type and unit per key?
2. Overlap enforcement for one key: `btree_gist` exclusion constraint (a new
   extension dependency) or validation in the ingest function?
3. Several recordings per role in one acquisition (after gaps): allowed with
   non-overlapping time ranges, or one acquisition per gap-free span?
4. Clock drift: is a single offset per channel enough for the pilot machine, or
   are sync points needed in phase 1?
5. Where derived values are computed: in the Rust core inside Postgres
   (synchronous, consistent) or by a client and inserted with provenance?
6. Should segment building and representation building run at append time for
   live acquisitions, or only on explicit calls?
7. Stale segments after a context correction: always an error in comparisons,
   or allowed with an explicit flag?
8. Summary score: maximum robust z, a count of values beyond a threshold, or
   no summary at all?
9. History population: should `before_only` use UTC, acquisition order or part
   order when clocks are missing?
10. Envelope storage: which bin width and precision keep storage within a bound
    (R1 found 27.9% at float4)?
11. Does the NASA milling dataset's documentation support a whole-cutter
    holdout and a clear wear threshold? (verify)
12. Which pilot partner, machine and controller export are available, under what
    data agreement?

### Phased plan

| Phase | Scope | Exit criterion |
|---|---|---|
| 1 | Acquisition with one channel; context timeline; context segments; `simple-v1` and `bands-v1`; compare-to-history and neighbors on a single channel; labels | Validation 8a run and reported; 8b started on one channel |
| 2 | Multichannel acquisitions with different rates; per-channel offsets and uncertainty; values per channel side by side; optional sync points | Pilot 8b judged useful; measured clock offsets known |
| 3 | Viewer: aligned channels, context timeline, history distribution and neighbors, label entry | Engineers use it in the pilot; time-to-cause measured |
| Later | Envelopes with a storage bound; tooth-passing values with actual speed; linear stretch; cross-channel values | Each one evaluated against the simple values first |

Phase 1 creates acquisitions with one channel so that phase 2 adds channels
without a schema migration.
