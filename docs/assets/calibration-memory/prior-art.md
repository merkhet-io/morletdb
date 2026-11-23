# Calibration memory: technical prior-art search

Searched 23 November 2026. This note supports the accompanying research post.
The search was focused, not exhaustive. No claim of algorithmic priority or unique
ownership of the phrase “calibration memory” follows from it.

## Questions and search coverage

We looked for (1) reuse of labelled historical cases in industrial diagnosis,
(2) query-specific local models and soft sensors, (3) few-shot bearing diagnosis
and support-set memory, and (4) existing dataset lineage infrastructure.
Search phrases included “calibration memory”, “case based reasoning fault diagnosis
condition monitoring”, “just in time learning historical database industrial
process”, and “few shot bearing fault diagnosis prototypical domain adaptation”.
Primary research papers and official project documentation were preferred.
Publication dates below come from the sources, not search-engine crawl dates.

## Closest sources and what they establish

| Source | Evidence examined | Relevance and boundary |
|---|---|---|
| Aamodt & Plaza (1994), [Case-Based Reasoning: Foundational Issues, Methodological Variations, and System Approaches](https://www.cs.auckland.ac.nz/~ian/CBR/AICom.pdf), AI Communications 7(1),39–59; DOI10.3233/AIC-1994-7104 | Original paper, full text | Reusing earlier cases and retaining useful experience is established. This is a conceptual predecessor, not our waveform experiment. |
| Cheetham, Varma & Goebel (2001), [Case-Based Reasoning at General Electric](https://cdn.aaai.org/FLAIRS/2001/FLAIRS01-019.pdf), FLAIRS-01 | Original proceedings paper, full text | Industrial diagnostic systems connected fault logs to repair histories. The paper describes deployed applications; its results were not independently replicated here. Log-derived cases differ from our waveform feature pipeline. |
| Cheng & Chiu (2004), [A new data-based methodology for nonlinear process modeling](https://www.sciencedirect.com/science/article/pii/S0009250904002453), Chemical Engineering Science59,2801–2810; DOI10.1016/j.ces.2004.04.020 | Publisher-indexed abstract/introduction; direct full-page access returned403 | Places local modelling in just-in-time/instance-based learning. We cite this conceptual relationship, not numerical superiority or implementation details we could not inspect. |
| Snell, Swersky & Zemel (2017), [Prototypical Networks for Few-shot Learning](https://proceedings.neurips.cc/paper/2017/hash/cb8da6767461f2812ae4290eac7cbc42-Abstract.html), NeurIPS30 | Official proceedings abstract and paper description | Learned metric spaces and class representatives from support examples are established. WRL5 is not a reproduction of ProtoNet. |
| Wang, Tan, Bai & Yang (2019 preprint), [Deep Prototypical Networks Based Domain Adaptation for Fault Diagnosis](https://arxiv.org/abs/1912.03688), related journal DOI10.1007/s10845-020-01709-4 | Authors’ abstract/version history | Directly overlaps few-labelled-target bearing diagnosis through domain adaptation and prototype learning. The paper uses CWRU, not our HUST split. We do not compare published scores across incompatible protocols. |
| Belay, Haghipour & Rossi (2026 preprint), [Robust Prototypical Networks for Few-Shot Sensor Fault Diagnosis](https://arxiv.org/abs/2609.12287) | Authors’ abstract; September10,2026 submission | Reports prototype accumulation across disjoint support episodes. Under an equal total support budget it reports no statistical difference from the ordinary prototype baseline. Synthetic faults injected into real measurements; not bearing hardware faults. No replication here. |
| [MLflow Dataset Tracking](https://mlflow.org/docs/latest/ml/dataset) | Official documentation, accessed November23,2026 | Dataset names, digests, sources and lineage already exist in ML tooling. A versioned calibration set is not novel merely because it tracks its source data. A waveform-specific integration could complement these tools. |

Additional corroborating industrial precedent: [TMDoctor: A Fuzzy Rule- and
Case-Based Expert System for Turbomachinery Diagnosis](https://www.sciencedirect.com/science/article/pii/S1474667017424578)
(IFAC1997, DOI10.1016/S1474-6670(17)42457-8). Its publisher-indexed abstract describes
using earlier experience in vibration diagnosis. Direct full-page access returned403;
we did not use it to substantiate detailed performance claims in the post.

## Novelty assessment

The high-level idea—use past examples to interpret a new machine observation—is
not new. Neither is adapting a model with a small target support set, retrieving
related examples, keeping a case library, or tracking dataset provenance.

Our actual contribution here is a transparent local comparison: fixed source-only
selection; separate bearing-type targets and support/query acquisitions; equal
label access; explicit distinction between signal duration and independent cases;
ordinary adaptation and random/shuffled controls; and retained negative findings.
The original-study scope and corrections are in the evidence package.

The product proposal combines exact waveform references, context/label provenance,
versioned eligibility and calibration-set exports in a PostgreSQL workflow.
The search does not establish that this combination is unique. Its practical
value still needs a customer comparison against existing files, databases and ML
tracking tools. The website must describe it as a proposal, not a shipped or
benchmark-validated MorletDB capability.

The new term is a workflow label, not a claim of a newly discovered brain-like
learning principle. No JEPA, xLSTM, repair-outcome learning, manufacturing-agent
benefit or database serving advantage was measured by WRL5.
