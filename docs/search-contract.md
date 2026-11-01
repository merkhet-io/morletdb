# Envelope search contract

What envelope search guarantees, for the exact and the probe path, as of SQL 0.3.1. Items
marked *(tested)* have a regression test in `extensions/pg_morlet/src/lib.rs`; the others
follow from the implementation, or have been measured in an evaluation but have no test.

## Functions

| Function | Candidates | Returned distances |
|---|---|---|
| `morlet_search_envelope_interval(collection, recording, start, end, ...)` | every eligible stored window (exhaustive) | exact |
| `morlet_search_envelopes(collection, samples, rate, channel, unit, ...)` | every eligible stored window (exhaustive) | exact |
| `morlet_search_envelope_probes(collection, recording, start, end, ...)` | routed: exhaustive when at most `exact_threshold` windows are eligible, otherwise the union of shifted inner-product probes (lags −L…L at step `probe_step`: 13 at 4096-sample windows, 7 at 2048) × `per_probe` from the HNSW index | exact for every returned row |

The probe path is **approximate in which windows it considers**, never in the distances it
reports. Its recall@10 against the exhaustive search was 0.977–1.0 at five filter levels on
held-out seismic data (evaluations BL1-C, SS3); individual queries reached as low as 0.6.

## Distance

`d = 1 − max over |lag| ≤ L of (1/n) Σ q[t+lag]·g[t]`. This is a biased cross-correlation of
z-scored envelopes (representation `xc-envelope-f16-v1`).
- The query envelope is computed at full precision. Stored envelopes are decoded from float16
  and z-scored again.
- `L = floor(max_lag_fraction × n)`; the default 0.075 gives ±30 at 4096-sample windows.
- Values fall in about [0, 2]: 0 is a perfect match at some allowed shift. It is not a metric.
- Distances are comparable only within one window size and one representation version.

## Eligibility

A stored window is a candidate when its recording has the query's sample rate, channel and
unit, its window size equals the query's, and the recording's metadata contains
`metadata_filter` (`@>`). Row-level security applies on top: recordings the caller cannot
see are never candidates (tested), and with `morlet_enable_row_security` their companion
rows are hidden as well; see [access control](access-control.md).

`max_candidates` (exhaustive path): if more windows are eligible, the search **fails**
instead of sampling.

## Exclusion, ordering, suppression

- **Source exclusion** (interval searches, `exclude_source => true`): windows of the source
  recording that overlap the query interval are never returned (tested). Searches by sample
  values (`morlet_search_envelopes`) have no source and exclude nothing.
- **Ordering:** `(distance, recording_id, start_sample)` ascending. Ties are therefore
  deterministic: identical windows in two recordings order by recording identifier
  (tested).
- **Overlap suppression** (`suppress_overlaps => true`): rows are taken in that order, and a
  row is skipped if it overlaps an already returned row of the same recording. Suppression
  is global over the result, and is applied before `top_k`. No two returned windows of one
  recording overlap (tested).
- **Fewer than `top_k`:** when the eligible, non-overlapping windows run out, the search
  returns fewer rows without an error (tested). With no stored envelopes it returns no rows.
  The probe path can also return fewer than the exhaustive path would, if its candidate union
  holds fewer than `top_k` non-overlapping windows.

## Progress and searchability

Three kinds of progress are recorded separately:

| Progress | Where | Advanced by |
|---|---|---|
| Raw | recording checkpoint (`checkpoint->>'end'`) | `morlet_append_chunk` |
| Representation | stored envelopes (last `start_sample` per window size) | `morlet_build_envelopes` |
| Index visibility | probe-table rows | `morlet_build_envelopes`, in the same call and transaction as the envelope |

- **Appends do not build envelopes.** A window becomes searchable when the transaction
  that ran `morlet_build_envelopes` for it commits. To make windows searchable at the commit
  that completes them, call `morlet_build_envelopes` in the append transaction (tested,
  within one transaction). Calling it from a background job instead makes windows searchable
  after that job's next step (evaluation BL1 Stage B: p95 0.09 s at 200× real time on one
  machine).
- **Windows** are complete windows on the grid `k × stride`. A partial tail becomes
  searchable once later appends complete it.
- **Deleting** a recording deletes its chunks, envelopes and probe rows (foreign keys,
  `ON DELETE CASCADE`). Deleting envelopes deletes their probe rows.
- **Idempotency:** repeating an identical append is a no-op. Rebuilding envelopes skips stored
  windows (`ON CONFLICT DO NOTHING`). Neither implies exactly-once processing.

## Probe path: candidates, coverage and snapshots

- **Fixed budget, no refill.** Each probe returns at most `per_probe` rows; source exclusion,
  eligibility re-checks and overlap suppression are applied to their union afterwards. If they
  leave fewer than `top_k` windows, the function returns fewer rows; it does not probe again. The
  exact path instead fetches more rows until `top_k` survive or the eligible windows run out.
- **Searchable universe.** The probe path finds only windows that have a probe-table row; the exact
  path searches every stored envelope. They coincide when `probe_backlog` in
  `morlet_collection_status` is 0, which holds when envelopes are built only by
  `morlet_build_envelopes` with probe search enabled. Envelopes written by other processes need
  their vector rows filled before probe search covers them.
- **Coverage.** `morlet_build_envelopes` continues after the last stored window, so windows built by
  it form a contiguous prefix per recording and window size. Windows written out of order by other
  processes can leave gaps that `unbuilt_samples` does not show.
- **Snapshots.** The exact functions are `STABLE` and run in the calling statement's snapshot. The
  probe function is `VOLATILE` (it sets index parameters for the call) and runs several statements;
  under READ COMMITTED, windows committed while it runs may be seen by some of them and not others.
  Use REPEATABLE READ for a single snapshot.
- **Routing.** The router counts eligible windows (stopping above `exact_threshold`) and sends small
  galleries to the exact path; windows committed between that count and the exact scan are
  included, not rejected.

## Not guaranteed

- The probe path's recall on other data, under other filters, or with other HNSW settings.
- Latency. Measurements are from one machine with warm caches and are documented with each
  evaluation.
- Relevance of results to a domain question: all guarantees are about fidelity to the
  exhaustive scorer.
