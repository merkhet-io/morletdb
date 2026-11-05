# Keeping the Spikes at the Edge — technical report 3 (draft)

**Keeping the Spikes at the Edge: Wavelet Approximation of Machine Waveforms at a Tenth of Lossless Size**, version
in [`REPORT_VERSION`](REPORT_VERSION). It states four falsifiable hypotheses on an approximation an edge device can
afford (error per byte against SZ3, ZFP and decimation; whether diagnosis survives; real time on a Raspberry Pi;
whether Bayesian selection is needed) and reports development findings. Version 0.1 also proposed a label-free
relevance score; it was dropped after development (not better than kurtosis). The study is F2
([`evaluation/edge_relevance_f2`](../../evaluation/edge_relevance_f2)); its protocol is a draft and will be frozen
before confirmation data are used.

References: [`related.bib`](related.bib) (verified; notes in [`RELATED_NOTES.md`](RELATED_NOTES.md)),
[`own.bib`](own.bib). Build: `tectonic -X compile spikes.tex` in this directory (Tectonic 0.17.0).
Not peer reviewed; not yet released.
