# Machines with a Memory — technical report 2 (draft)

**Machines with a Memory: Episodic Recall and Graded Compaction for Industrial Waveforms**, version in
[`REPORT_VERSION`](REPORT_VERSION). A position and design report: it argues that an archive of machine waveforms
should work as an episodic memory with deliberate, graded and auditable forgetting, defines the episode, a
compaction ladder (full, not searchable, represented, summarized, gone), an explicit keep-value with a ledger,
and an evaluation by retrieval regret, survival of known events and drift visibility.

It is separate from the first report ([`../paper`](../paper)), which covers lag-tolerant retrieval and storage.
Statements carry an evidence level: **m** measured (with the study), **i** implemented and tested for
correctness but not measured for benefit, **p** proposed. Version 0.2 adds the first evaluation of forgetting
policies, study F1 ([`evaluation/forgetting_f1`](../../evaluation/forgetting_f1)): protocol frozen before its two
confirmation runs, results in its `RESULTS.md`.

Build: `tectonic -X compile memory.tex` in this directory (Tectonic 0.17.0, as for the first report).
Not peer reviewed; not yet released.
