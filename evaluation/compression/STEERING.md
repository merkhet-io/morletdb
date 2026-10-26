# Steering: compression, progressive reconstruction and retention for condition monitoring

Rewritten 2026-10-26 from the user's steering prompt, with the review changes agreed in
conversation. The project stays **open source and non-commercial**.

## Objective and key question

Determine whether MorletDB can become a convenience signal store for condition-monitoring
engineers that keeps useful diagnostic evidence while storing and reading **materially
fewer bytes** than a straightforward implementation with existing codecs.

**"Material" (provisional, fixed in the protocol before held-out evaluation):** at least
2× smaller than optimized lossless Parquet on the same samples, with diagnostic
non-inferiority. A smaller gain is reported but does not justify a custom codec. The
memo's compression percentages (instinct PR #1) are treated as speculative.

## Starting point

- **Reuse:**
  - MorletDB chunked recordings (seismic example: raw chunks 2.84 bytes/sample on disk,
    plus 0.82 bytes/sample of segment features);
  - the Paderborn loader and splits (PR #6);
  - the msmem DWT module and its tests (PR #5);
  - the timing-test harness and data (PR #6).
- **Keep frozen:** the PTB-XL ECG pilots (test fold consumed) and the PR #6 held-out
  results. No retuning against them.
- **Facts the memo predates (PR #6):**
  - Pooled Morlet does not beat simple features on bearings.
  - Feature scaling must be standardized.
  - Timing-aware retrieval helps on transients, but plain envelope cross-correlation
    beats temporal Morlet at about 1000× the search cost per query.
- **Separate, always:**
  - independently reproducible results;
  - agent-reported results awaiting verification;
  - untested hypotheses.

## Hypotheses (each needs its own evidence; one positive does not establish another)

| | Hypothesis | Required evidence |
|---|---|---|
| H1 | Specialized compression reduces storage | Smaller total representation than optimized lossless Parquet at an explicitly stated fidelity |
| H2 | Diagnostic information survives compression | Non-inferior weak-fault detection and fault indicators on independent bearings/runs |
| H3 | Progressive access reduces query cost | Fewer bytes fetched/decoded at comparable retrieval quality, with measured latency |
| H4 | Decomposition improves compression | A wavelet or periodic-predictor + residual method beats simpler alternatives after all overhead |
| H6 | Retention tiers beat codecs for total storage | Bytes kept vs. evidence lost under triggered retention, compared with codec-only storage |

H5 (operating-context retrieval) is **deferred**: it is a retrieval question, and PR #6
already shows cross-bearing retrieval near chance.

## Gates (build only what the evidence justifies)

1. **Stage 1, always:** benchmark protocol, baseline harness, rate–distortion and
   diagnostic fidelity, retention estimate and a cascade-search measurement.
2. **Stage 2, codec interface plus experimental wavelet codec in MorletDB:** only if a
   wavelet or decomposition codec beats the best standard codec at diagnostic
   non-inferiority (H1 + H2 + H4).
   - If a standard codec wins, Stage 2 becomes "integrate that codec" and nothing else.
3. **Stage 3, progressive storage and its retrieval integration:** only if H3 shows a
   measured benefit beyond a plain cascade (cheap features → full decode of the top-N).
4. **Stage 4, compaction planner:** only after Stage 3, and dry-run first. Never delete
   originals, and never let an anomaly score alone decide what is expendable.

No new storage engine or index. Experimental features stay opt-in. Raw data held in
object storage (Parquet), with PostgreSQL as the index, is a *measured option*, not a
forbidden one.

## Benchmark requirements

- **Competitors** (unavailable ones are documented, never silently omitted):
  - optimized Parquet (zstd, native sample type, byte-stream split for floats);
  - native ADC integers;
  - scalar quantization + zstd;
  - delta/LPC predictive coding (FLAC);
  - a reconstructible DWT codec;
  - SZ3, zfp, MGARD and SPERR where practical.
- **Data:** identical samples and metadata for every competitor. Regular timing is stored
  compactly, with gaps and clock information preserved. Report each of these separately:
  - compression vs. the raw payload;
  - compression vs. optimized Parquet;
  - the complete archive size.
- **Fidelity:**
  - max absolute error;
  - normalized error;
  - spectral and envelope-spectrum distortion;
  - kurtosis and crest-factor change;
  - weak-fault detection at a fixed false-alarm rate, with a **named detector chosen
    before the test**;
  - retrieval ranking stability.
  Empirical bounds are labelled empirical.
- **Decomposition ablation:** recording = periodic predictor (harmonic or
  shaft-synchronous template) + residual. Count parameter and synchronization overhead.
  A block may fall back to a simpler codec. Parameters come from development data only.
- **Data and splits:**
  - synthetic signals for correctness only;
  - real data split by bearing, run or station-period; no overlapping-window leakage.
  - Paderborn: CC BY-NC 4.0, which fits a non-commercial project.
  - IMS/NASA run-to-failure, for weak-fault progression.
  - IPOC CX seismic counts, for native-integer lossless coding.
  - Partner industrial data, when available, supersedes all of these.
- **Freezing:** primary comparisons, non-inferiority margins and the uncertainty method
  are frozen before the held-out evaluation. Uncertainty is reported at the
  independent-unit level.

## Decision

The final report recommends **continue / narrow / pause per capability**:
- If a standard codec wins, integrate it.
- If wavelets help storage but not retrieval, say so separately.
- If the benefit disappears against a fair baseline, report that plainly and do not
  expand the feature.
