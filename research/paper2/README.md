# Machines with a Memory — technical report 2 (draft)

**Machines with a Memory: Episodic Recall and Graded Compaction for Industrial Waveforms**, version in
[`REPORT_VERSION`](REPORT_VERSION). A position and design report: it argues that an archive of machine waveforms
should work as an episodic memory with deliberate, graded and auditable forgetting, defines the episode, a
compaction ladder (full, not searchable, represented, summarized, gone), an explicit keep-value with a ledger,
and an evaluation by retrieval regret, survival of known events and drift visibility.

It is separate from the first report ([`../paper`](../paper)), which covers lag-tolerant retrieval and storage.
Statements carry an evidence level: **m** measured (with the study), **i** implemented and tested for
correctness but not measured for benefit, **p** proposed. The compaction policies are proposals; their
evaluation (study F1) will be frozen in [`evaluation/forgetting_f1`](../../evaluation) before any result, and its
results will make version 0.2.

Build: `tectonic -X compile memory.tex` in this directory (Tectonic 0.17.0, as for the first report).
Not peer reviewed; not yet released.
