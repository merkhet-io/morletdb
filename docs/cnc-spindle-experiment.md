# Experiment spec: CNC spindle episode retrieval (pilot)

Status: **proposed specification**, not implemented. Builds on demo-catalog entry
[D02](demo-catalog.md#d02--spindle-and-cutting-tool-investigation) and the
[condition-monitoring origins](../README.md#origins-in-condition-monitoring).
Do not build another application until this spec is reviewed and data access
is approved.

## Question

Given an engineer-reviewed vibration episode on one spindle axis, does
retrieval return other reviewed-relevant episodes from **held-out tools or runs**
more usefully than simple baselines? Comparisons are made under comparable
operating context (speed, feed, load), with current and temperature available
for inspection.

Out of scope: fault diagnosis, failure prediction, RPM or speed invariance,
synchronized multichannel retrieval, and any general claim of superiority.
MorletDB retrieves single channels today. Other axes and context are displayed
alongside a match; they are not searched jointly.

## Data requirements

- **Primary:** approved recordings from one machine and one spindle family.
  Three-axis spindle vibration (X/Y/Z), spindle current and temperature, plus
  tool, run, material, program, speed and feed. Include several tools run to
  replacement, so that degradation and normal variation both appear.
- **Public pilot fallback, verify before use:** the NASA milling set listed in
  D02 has spindle/table vibration and motor current with wear measurements,
  but no three-axis spindle vibration or temperature. Any other candidate
  (for example the PHM 2010 milling data) must have its channels, units,
  labels and terms confirmed from the provider's documentation first. A
  fallback narrows the question; record exactly which parts it cannot test.
- **Manifest:** as in the seismic example. Source checksums, channels and
  units, sample rates, clock source, and a run/tool table.

## Time alignment and context

Treat current and temperature as **time series aligned to the vibration**, not
as recording-level metadata.

- **Timestamp mapping.** Document each channel's clock: shared DAQ clock,
  separate logger, or controller log. Record any offset or drift estimate and
  how it was measured (for example a spindle start transient seen on all
  channels), plus the residual uncertainty in milliseconds.
- **Storage.** Each vibration axis is its own MorletDB recording at its native
  rate (`morlet-energy-v1` for 128 Hz to 200 kHz). Context channels keep their
  original samples, either as MorletDB recordings where their rate is in a
  supported domain or in a sidecar table keyed by (run, channel, UTC). They are
  never resampled into the vibration stream.
- **Window context.** For each vibration window, derive context from the
  context samples inside that window's UTC span: mean, minimum, maximum, slope,
  sample count, and the maximum gap. Changing conditions within a run stay
  visible at window resolution.
- **Missingness.** Report per channel and per run the fraction of windows with
  no context sample, too few samples, or a gap above a threshold. Missing
  context is an explicit state, never a zero or an imputed value.

## Splits and leakage controls

- **Holdouts.** Hold out whole tools (and their runs). The reference gallery
  holds windows from reference tools only; queries come only from held-out
  tools. Fix the split before any method is run, and never tune on held-out tools.
- **Overlap.** Windows overlapping in time on the same run and axis never
  appear as query and candidate. Source exclusion and overlap suppression run
  before top-k, exactly as in MorletDB.
- **Adjacency.** Report same-run neighbours separately from cross-tool
  relevance. A match next to the query in the same run is not evidence of
  retrieval value.
- **Recording.** The split, the window grid and the leakage checks are written
  into the report and asserted in code, as in the seismic evaluation.

## Queries and reviewed labels

- **Pilot size:** 20 held-out queries, chosen before retrieval runs:
  - About 8 fault-relevant episodes, such as reviewed wear-related, chatter-like
    or impact episodes, as the engineer defines them.
  - About 6 normal operating variations: speed or feed changes, tool
    entry/exit, air cutting, spindle acceleration.
  - About 6 fault-relevant negatives: episodes that look anomalous but were
    reviewed as benign.
- **Candidate pooling:** pool each method's top 10 for every query. Reviewers
  grade the pool blind to method and rank: 0 not relevant, 1 related,
  2 same phenomenon. Reviewers see all three axes and the aligned context.
- **Second reviewer:** a second reviewer grades at least 5 queries. Report
  agreement (Cohen's kappa) and keep disagreements.
- **Label records:** reviewer, date, criteria and notes are stored with the
  labels. Unreviewed results are never counted as relevant or as negatives.

## Methods

Every method runs on the same candidate population, with the same window
grid, normalization choice, source exclusion and overlap suppression. **Any
operating-context filter is one predicate applied identically to all methods,**
for example speed within ±5% of the query or the same material. Missing
context follows one documented policy for all methods: either excluded or
reported as its own bucket.

1. **Statistics:** RMS, crest factor and kurtosis per window.
2. **FFT:** FFT band energies; order-domain bands where RPM is reliable.
3. **MorletDB pooled:** `morlet-energy-v1` features (the database path).
4. **Temporal (exploratory):** the wave-index temporal score, exhaustive only.
   It is labeled exploratory, given the seismic result.

## Metrics and reporting

- **Per query:** precision@10, nDCG@10 and the rank of the first relevant
  result, reported for every query and method. This table is the primary result.
- **Paired comparison:** per-query wins, ties and losses for each method pair.
- **Uncertainty:** bootstrap 95% intervals over queries for each method's mean
  metric and for the paired differences. With 20 queries these intervals are
  expected to be wide; report them as they are.
- **Context sensitivity:** the same metrics with and without the shared
  context filter.
- **Failures:** queries where every method fails, and cases where simple
  baselines match or beat Morlet, are reported explicitly.

## Decision gate

Consider multichannel or context-aware retrieval design only if two
conditions hold on reviewed labels: Morlet-based retrieval shows a consistent
per-query advantage over the simple baselines, and engineers judge the
retrieved episodes useful. Otherwise, record the outcome and choose the next
investigation from the demo catalog.
