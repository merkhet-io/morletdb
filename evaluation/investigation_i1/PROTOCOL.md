# Investigation I1: does MorletDB help an agent find prior cases and ground an investigation on public run-to-failure data? (#582)

Status: **DRAFT, not frozen.** Nothing has been run. No model was called, no dataset was downloaded and no
signal of any dataset named here was read for this draft. Drafted 2026-11-21 from `main` at `d06634c`. It is frozen by
the procedure in [section 15](#15-freeze-amendments-and-archive); changes after that are dated amendments at the end
of this file.

"I1" here always means *investigation I1*. The repository also has an unrelated ingestion study in
`evaluation/ingest_i1`.

## 1. What this study can and cannot show

**In scope.** On public bearing run-to-failure recordings, with the same agent and model, does access to MorletDB's
read-only tools change:

- how well the agent retrieves relevant prior cases;
- whether its cited evidence resolves and supports what it says;
- whether it holds back when the evidence does not allow a conclusion?

**Not established by this study, whatever the result:**

- any effect on **real manufacturing investigations**;
- any effect on **engineer time**. Agent runtime is not engineer time, and no engineer takes part;
- any effect on the **Claude for Manufacturing workflow**. Its plugin and skills (#581) are not used here;
- diagnostic accuracy on machines, sensors, faults or operating regimes other than those measured;
- the value of work orders, technician close-out notes or maintenance records. **Public run-to-failure datasets have
  none.**

Those questions stay for a later pilot stage (#199), under a separate protocol. I1 therefore covers only part of #582.
It does not satisfy that issue's plugin comparison, its contradictory-maintenance-note case or its engineer-time
measurement, and **completing I1 does not close #582**.

## 2. Questions and hypotheses

- **Q1, retrieval.** Does the agent with MorletDB tools return more of the relevant prior cases than the same agent
  with ordinary exports of the same evidence?
- **Q2, grounding.** Is its cited evidence at least as correct, with no more unsupported diagnostic claims?
- **Q3, restraint.** Does it abstain or qualify at least as appropriately when the evidence is insufficient?

Δ is always **MorletDB arm minus export arm**, computed per held-out unit ([section 12](#12-analysis-and-decision-rules)).

| | Metric | PASS | FAIL | Otherwise |
|---|---|---|---|---|
| **H1** (superiority) | Case recall@5, families A and B | Δ ≥ **+0.15** and 95% interval lower bound > 0 | interval upper bound < +0.15 | INCONCLUSIVE |
| **H2** (non-inferiority, with a floor) | Supported-claim share, all families | MorletDB arm ≥ **0.90**, and interval lower bound of Δ > **−0.05** | MorletDB arm < 0.90, or interval upper bound of Δ < −0.05 | INCONCLUSIVE |
| **H3** (non-inferiority, with a floor) | Appropriate-response share, families C–F | MorletDB arm ≥ **0.80**, and interval lower bound of Δ > **−0.10** | MorletDB arm < 0.80, or interval upper bound of Δ < −0.10 | INCONCLUSIVE |

**The claim "MorletDB improved retrieval without degrading grounding or restraint" needs all three to PASS.** Each
hypothesis keeps its own verdict. Requiring all three means no multiplicity correction is applied.

**How the thresholds were chosen.** They were set from what the measure can resolve and what would matter, without
any measurement:

- **+0.15 (H1).** A held-out unit has at most five same-rig prior cases, so one more relevant case in the top five is
  worth at least 0.20 on that unit. +0.15 asks for close to one extra relevant case per incident on average. A smaller
  gain could not be told from run-to-run variation at this sample size.
- **0.90 and −0.05 (H2).** An investigation aid whose citations fail more than one time in ten needs checking line by
  line. −0.05 allows about one extra bad claim in twenty.
- **0.80 and −0.10 (H3).** These are looser because the families are few and each incident weighs more. A tool that
  makes the agent overclaim on more than one hard case in ten is a regression worth reporting.

These values are provisional until the freeze. They may be changed before it, for a recorded reason, but never after
held-out data has been read. Development results may set budgets and prompts ([section 5](#5-conditions)); **they may
not move these thresholds in the direction of an observed effect.**

## 3. Contamination audit

Data read by an earlier study cannot be called held-out. The audit searched the working tree at `d06634c` for each
dataset name, and all local and remote git history (`git log --all -G`) for the names and record DOIs of the candidates
chosen below.

| Dataset | Units | Earlier use in this repository | Status for I1 |
|---|---|---|---|
| **CWRU** | seeded-fault recordings | Early retrieval baseline, files 105, 106, 118, 119, 130, 131 ([evaluation/README.md](../README.md)); gammatone study, 15 fault conditions ([gammatone/PROTOCOL.md:84](../gammatone/PROTOCOL.md)) | **Consumed in part** (the rest was unseen at the gammatone freeze). Not run-to-failure. Not used |
| **IMS** | 3 tests, 12 bearings | Compression H2, sets 1–3 ([compression/PROTOCOL.md:12](../compression/PROTOCOL.md), line 114); W1 development ([wavelets/PROTOCOL.md:53](../wavelets/PROTOCOL.md)); storage studies; machine demo with agent sessions on tests 2 and 3 ([README:31](../../examples/machine-demo/README.md)); fleet demo on three runs ([README:156](../../examples/fleet-investigator/README.md)) | **Consumed, and agents have already investigated it.** Not used |
| **FEMTO/PRONOSTIA** | 17 bearings | W1: 6 learning bearings for development and 11 full-test bearings for evaluation ([wavelets/PROTOCOL.md:35–47](../wavelets/PROTOCOL.md)); harness checks in C5 and C7; Bearing1_1 in storage B5, B6 and codec G2.1 ([codec_g21/README.md:35](../codec_g21/README.md)) | **Consumed, all 17.** Not used |
| **XJTU-SY** | 15 bearings | C7, all 15, whole life ([context/c7/PROTOCOL.md:37](../context/c7/PROTOCOL.md)); W2, last 10 snapshots ([multiwavelet/PROTOCOL.md:34–43](../multiwavelet/PROTOCOL.md)); G1, descriptive ([gammatone/PROTOCOL.md:93](../gammatone/PROTOCOL.md)); F1 forecasting, which already states that no untouched test data exists ([forecasting/PROTOCOL.md:8](../forecasting/PROTOCOL.md)); edge F2, 11 bearings ([edge_relevance_f2/PROTOCOL.md:35](../edge_relevance_f2/PROTOCOL.md)); Bearing1_1 in storage B5 and B6 | **Consumed, all 15.** Used for **development only** |
| **Paderborn KAt** | 32 bearings (31 obtained) | Retrieval H2 ([decision/PLAN.md:31](../decision/PLAN.md)), compression H2, edge F2, CV1 ([current_vibration/PROTOCOL.md:17](../current_vibration/PROTOCOL.md)), storage studies | **Consumed.** Damaged bearings measured after remounting, not run-to-failure recordings. Not used |
| **Paderborn run-to-failure** (Zenodo 10868257) | 17 bearings | C5 and C6 ([context/c5/PROTOCOL.md:38](../context/c5/PROTOCOL.md)); exploratory in C7 ([context/c7/PROTOCOL.md:143](../context/c7/PROTOCOL.md)); L1 walkthrough ([level/PROTOCOL.md:74](../level/PROTOCOL.md)) | **Consumed.** Not used |
| **University of Ferrara** (Mendeley htk59pp5wx, zz8hpyx939) | 6 bearings | **No match** in the tree or in git history | **Unused: primary held-out** |
| **UNSW** (Mendeley h4df4mgrfb) | 4 tests | **No match** in the tree or in git history | **Unused: conditional held-out** ([section 4.3](#43-held-out-replication-unsw-conditional)) |
| KAIST (Mendeley 5hcdd3tdvb) | 1 bearing documented | No match | Unused. Rejected: one unit has no prior case |
| DLR needle bearings (Zenodo 20742913) | 8 tests, 2 bearings each (inferred from the record) | No match for the record | Unused. Rejected: the record states no failure criterion or per-bearing damage, so there is no ground truth |
| Ottawa UORED (Mendeley y2px5tg92h) | 20 bearings | No match | Unused. Rejected: three health-stage files per bearing; the record does not say these form continuous run-to-failure sequences |

**Limits of the audit.**

- It covers files and commits. **Dataset directories outside git were not inspected.** Before the freeze the owner
  confirms that neither held-out dataset is already on disk or was used in uncommitted work.
- "Unused by this project" does not mean unseen by the model. Both held-out datasets and their papers are public and
  may be in a model's training data. [Section 8](#8-information-available-at-incident-time) reduces direct recognition
  but cannot rule it out.

## 4. Data

No dataset is downloaded until the freeze. Sizes below are the compressed archive sizes in each repository's file
listing, read on 2026-11-21; no archive was opened.

### 4.1 Development: XJTU-SY (consumed; never evidence)

| | |
|---|---|
| Source | <https://biaowang.tech/xjtu-sy-bearing-datasets/>, <https://github.com/WangBiaoXJTU/xjtu-sy-bearing-datasets> |
| Terms as stated | No formal licence. The page says the data are publicly available and anyone can use them to validate prognostics algorithms, and asks publications to cite Wang, Lei, Li and Li, IEEE Trans. Reliability 69(1):401–412, 2020. **Licence unclear:** evaluate and cite only; do not redistribute |
| Size | Not stated by the source |
| Signals | 2 housing accelerometers, 25.6 kHz, 32,768 samples (1.28 s) every minute |
| Units | 15 LDK UER204 bearings, 5 per condition: 35 Hz/12 kN, 37.5 Hz/11 kN, 40 Hz/10 kN |
| Ground truth | Failed elements per bearing, from the dataset's Table 2, as transcribed in [multiwavelet/PROTOCOL.md:43](../multiwavelet/PROTOCOL.md): outer, inner, cage and two compound failures. The source table is an image; re-check it against the transcription before the freeze |
| Not available | Onset time, spall size, maintenance records. Condition is confounded with test group |

XJTU-SY is the only candidate with several documented failure components, which is why it is used for development and
as a **labelled distractor library** at held-out time ([section 7](#7-incidents)). Its signals are never a held-out
query.

### 4.2 Primary held-out: University of Ferrara

| | |
|---|---|
| Source | Part 1 <https://data.mendeley.com/datasets/htk59pp5wx/2> (E1–E3 by file listing), Part 2 <https://data.mendeley.com/datasets/zz8hpyx939/2> (E4–E6). Article: Arpa, Gabrielli, Battarra and Mucchi, *Data in Brief* 55, 110620, 2024, doi:10.1016/j.dib.2024.110620, [PMC11261296](https://pmc.ncbi.nlm.nih.gov/articles/PMC11261296/) |
| Terms as stated | Both data records: **CC BY 4.0**. The article: **CC BY-NC 4.0**. These are separate objects, so the record's licence is taken to govern the data and the article's its text. **A third-party page gives CC BY-NC for the work; the data licence should be confirmed** (open decision 5) |
| Size | 12,047,218,805 bytes in six `.7z` archives, plus two readme PDFs |
| Signals | One radial accelerometer in g, 25.6 kHz, 5 s every 5 minutes. E4's first 453 files are hourly |
| Units | 6 bearings, model 1205 ETN 9, 40 Hz. Radial load and duration: E1 4 kN/17 d, E2 4 kN/6.9 d, E3 4 kN/8.3 d, E4 3 kN/20.9 d, E5 4.7 kN/6 d, E6 5 kN/1.8 d |
| Ground truth | Stopped at a 20 g peak. **All six ended with an extended outer-raceway defect; all other components were examined and found undamaged.** Characteristic frequencies are published (BPFO 195.8 Hz, BPFI 284.2, BSF 103.2, FTF 16.3) |
| Not available | Onset time, intermediate inspections, spall growth, a second failure mode, maintenance records |

**Consequence.** Every Ferrara unit has the same confirmed cause, so an agent that always answers "outer race" would
be right every time. **Cause identification is therefore not a held-out hypothesis.** It is reported descriptively,
with this base rate beside it, and H1 is built on fault-stage retrieval instead ([section 9](#9-ground-truth-and-relevance)).

### 4.3 Held-out replication: UNSW (conditional)

| | |
|---|---|
| Source | <https://data.mendeley.com/datasets/h4df4mgrfb/3>, doi:10.17632/h4df4mgrfb.3. Paper: Zhang, Borghesani, Randall and Peng, *Mech. Syst. Signal Process.* 166, 108466, 2022 |
| Terms as stated | **CC BY 4.0** on the record |
| Size | 9,720,294,991 bytes in four `.7z` archives, plus a readme |
| Signals (record) | Horizontal and vertical acceleration, two encoders, load cell, tacho; `Fs` is stored per file. One folder per test holds all signals at 6 Hz shaft speed, another selected samples at 6, 12, 15 and 20 Hz |
| Units | 4 run-to-failure tests, 2019–2020 |
| **Unverified** | Sampling rate (51.2 kHz), load (10.5 kN; 7 kN for Test 2), bearing (Nachi 6205), seeded initial defect (0.5 or 1 mm), and outer-race location for Tests 1 and 3 all come from **secondary or third-party sources only**. The location for Tests 2 and 4 was not found. The paper's full text could not be retrieved for this draft |

**Admission gate, before the freeze and before any UNSW signal is read.** Read the record's readme and the paper. UNSW
enters I1 only if, for each admitted test, a primary source documents the failed component, load and speed. Tests that
fail the gate are excluded by name. If none pass, I1 runs on Ferrara alone.

If the defects were seeded, UNSW shows the growth of a seeded defect, not natural initiation, and is reported as such.

## 5. Conditions

Both arms use the same agent harness, model, system prompt, answer schema, budgets, compute limits and incident text.
They differ only in how the same eligible evidence is reached.

| | **Export arm** | **MorletDB arm** |
|---|---|---|
| Evidence | Per-incident directory: one CSV per eligible recording, a manifest with rate, unit, channel and pseudonymous unit, closed-case labels, coverage file | Per-incident database holding exactly the same eligible samples, labels and coverage |
| Tools | File read and a sandboxed Python with NumPy and SciPy | The read-only MCP server (`tools/mcp/morlet_mcp.py`), and the same sandboxed Python without file access to the evidence |
| Search | Whatever the agent writes | `investigate_interval`, `search_similar` and the other shipped tools |

- **Same evidence, verified.** CSV values use shortest round-trip decimal. For every incident, a checker decodes both
  arms and compares the SHA-256 of the exact float64 bytes per recording, including signed zero. Any mismatch voids
  that incident until it is fixed; no value is rounded or resampled.
- **Agent.** Fresh headless session for every run, no memory, no project instructions, no web, no other MCP server, no
  access to other incidents, the source archives or this protocol.
- **Model.** One exact model identifier, recorded at the freeze and in every run's trace with harness, MCP, SDK and SQL
  versions. An alias such as "latest" is not allowed. If the model is withdrawn mid-study, the study stops and an
  amendment decides; arms are never run on different models.
- **Budgets per run** (the same in both arms; provisional until set on development):
  - **60 tool calls.** The longest recorded demo session took 38
    ([machine demo](../../examples/machine-demo/README.md));
  - **15 minutes** wall time;
  - **400,000 tokens**, input plus output, with cached tokens counted and also reported separately;
  - identical CPU and memory limits, set before the first development run.
- **Budget exhaustion is a result.** The run is scored on what it has submitted. With no valid final answer it scores
  zero on H1 and H2 and "not appropriate" on H3. Exhaustion rates are reported per arm.
- **Repeats and order.** Each incident × arm runs **3 times**. Run order is randomised from a seed fixed at the freeze,
  blocked by incident. Failed infrastructure runs (not agent failures) are logged and retried at most twice.
- **Variability.** Report per-incident range across repeats, the share of incidents whose scored outcome changes across
  repeats, and each hypothesis's Δ per repeat index.

**Maximum size.** With both held-out datasets fully admitted: 56 incidents × 2 arms × 3 repeats = **336 runs**, at most
**134.4 million tokens**. Cost is computed from the provider's published prices on the run date and reported as
measured.

## 6. Splits and leakage

- **The unit is the physical bearing/test.** Windows, snapshots and incidents of one unit are never independent.
- **Development and held-out share no unit, rig or dataset.** Prompts, tool guidance, window size, budgets and oracle
  checks are developed on XJTU-SY only.
- **At held-out time, XJTU-SY may appear in the library**, as labelled closed cases from another rig. It is never a
  query and never counts as a relevant case in H1.
- **Leave-one-unit-out within a held-out dataset.** The library for an incident on unit *u* holds other units as closed
  cases, plus *u*'s own history up to the cutoff. *u*'s own history never counts as a prior case.
- **Same-rig results are reported separately from cross-rig results.** Physically distinct bearings on one rig still
  share a motor, mounting and sensor chain, so a same-rig match can reflect the rig.
- **No random window split** is used anywhere.

## 7. Incidents

Incident rules are fixed before held-out data is read. A script applies them; nobody inspects held-out signals or
outcomes to choose or edit an incident. Pseudonyms, seeds and generated incident files are hashed before the first run.

**Trigger (the incident time *t*).** Per unit, on the first accelerometer channel:

1. Compute the RMS of each mean-removed snapshot.
2. Baseline: the mean and standard deviation of the **first 60 snapshots**. No run length or end-of-life is used.
3. *t* is the end of the fifth of five consecutive snapshots with RMS above mean + 4 sd.
4. A unit with no trigger contributes no incident in families A–F; this is reported.

E4's hourly start means its baseline spans about 60 hours. This is kept, not corrected.

| Family | Construction | Library | Expected response |
|---|---|---|---|
| **A: standard** (one per unit) | Query at *t* | All other units of the dataset, plus XJTU-SY | Relevant prior cases ranked, with cited evidence |
| **B: changed condition** (units with a same-load peer) | Query at *t* | As A, with **same-load peers removed** | As A, and the load difference stated |
| **C: misleading lookalike** (one per unit) | Query on the **highest-RMS pre-onset snapshot** ([section 9](#9-ground-truth-and-relevance)) before *t*. A unit with no such snapshot has no family C incident, and this is reported | As A | No confirmed fault stated on this evidence |
| **D: missing or compacted evidence** (one per unit) | Query at *t*. One prior relevant unit, drawn by seed, has its fault-stage chunks moved to level 3 (`summarized`) through `morlet_set_level`; the export arm has those sample files removed and the same summaries and coverage rows | As A | The unavailable evidence is named and not treated as a negative result |
| **E: clock uncertainty** (one per unit) | Query at *t*, reported with an **injected, documented** clock offset of ±1 to ±3 snapshot intervals (seeded), through time anchors in the database and the same anchors in a sidecar file. Samples are not changed | As A | Timing uncertainty carried into the answer; candidates inside the bound; no claim of exact onset |
| **F: no relevant precedent** (one per unit) | Query at *t* | **Only XJTU-SY units with no documented outer-race failure** (1_4, 2_1, 2_3, 3_3, 3_4). If an admitted UNSW test has a different documented failed component, the set is adjusted by the same rule before the freeze | States that no prior case with the same confirmed cause exists. A signal-based hypothesis is allowed, marked unconfirmed |

Counts with both datasets admitted: A 10, B 6 (E1–E3 and three UNSW tests, subject to the load check), C 10, D 10,
E 10, F 10: **56 incidents**. Ferrara alone: 33.

**Contradictory maintenance notes cannot come from public data.** These datasets have no notes, and I1 **does not
fabricate any.** That case from #582 remains untested and moves to the pilot stage. If a synthetic note is ever used to
check that a tool handles the field, it is labelled an *interoperability fixture*, kept out of every scored incident,
and excluded from all effectiveness claims.

## 8. Information available at incident time

`investigate_interval` has **no temporal cutoff**: later occurrences and annotations can participate
([SDK](../../docs/python-sdk.md#investigate-an-interval)). Filtering on start time is not enough, because a segment
that starts before *t* can end after it, and because labels may be recorded later (#158). I1 does not depend on #158.
It enforces the cutoff by **physical absence**:

1. **One database per incident.** It is built from a manifest listing exactly the eligible samples. Nothing later than
   the cutoff is ever loaded into it, so no query, tool or missing filter can reach it.
2. **Cutoff for the query unit.** Only complete snapshots ending at or before *t* (in family C, at or before the end
   of the query snapshot). A snapshot in progress at the cutoff is excluded.
3. **Closed cases.** Other units are complete, with their documented failed component as a label. In this replay they
   are defined as closed before the incident. **This ordering is a study construction.** The sources do not establish
   that these tests ran in this order, so no claim about real chronology is made.
4. **Labels.** The query unit has none. Because each database is built for one incident, every label in it is by
   construction one available at the cutoff. Stored recorded-at values reflect the build and are not evidence of
   chronology. No onset, end-of-life or oracle output is stored in either arm.
5. **Identity.** Dataset, rig and unit names are replaced by per-incident pseudonyms, and file times by a synthetic
   clock with the same intervals. Bearing geometry and shaft speed are supplied, since a real engineer has them. They
   can still hint at the source. This is a stated limitation.
6. **Independent validation.** A validator, sharing no code with the loader, reads each built database and export
   directory and fails the incident unless:
   - the recording set, lengths and sample hashes equal the manifest;
   - the query unit's stored end is at or before the cutoff;
   - no label, context row, anchor or companion row refers to later data;
   - both arms hold the same bytes.
7. **Leak control.** On a synthetic fixture, the harness plants a future copy of the query window in a control database
   and confirms that `investigate_interval` finds it, then confirms it is absent from the snapshot built by the same
   pipeline. This shows that the check can detect a leak. It runs before development and again before confirmation.

Any validation failure stops the affected incident before an agent sees it.

## 9. Ground truth and relevance

Relevance is defined from source documentation and an independent signal oracle. **It never uses a MorletDB distance,
rank or tool output.** Two claims are kept apart:

- **Confirmed cause:** the failed component documented by the data provider after the test.
- **Symptom similarity:** an interval showing a comparable signal. It does not establish cause.

**Fault-stage oracle.** A standalone NumPy/SciPy script, fixed on development data:

- envelope spectrum of the 2–8 kHz band, over 5–500 Hz;
- a snapshot is *positive* if one of its top five peaks lies within ±max(2%, 1 Hz) of the documented characteristic
  frequency of the unit's failed component or its second harmonic (the tolerance and peak rule of
  [W2](../multiwavelet/PROTOCOL.md));
- **fault stage:** from the first of five consecutive positive snapshots to the end of the unit;
- **pre-onset:** the first half of the snapshots before that point;
- everything else is **ambiguous** and is left out of both numerator and denominator.

The oracle is a proxy: a spectral line is not an inspection. A unit with no fault stage cannot serve as a relevant case
and is reported. **If fewer than five held-out units have a fault stage, H1 is INCONCLUSIVE.**

**Relevance of a returned prior case**, for a query on unit *u*:

| Grade | Rule | In H1 |
|---|---|---|
| **Relevant** | Another unit of the same dataset, same documented failed component, cited interval in its fault stage | Counts |
| **Not relevant** | Same dataset but cited interval pre-onset; or any unit with a different documented component | Counts against precision |
| **Neutral** | Another rig with the same documented component; ambiguous intervals; compound failures that include the component | Removed from the list before scoring, and counted separately |

The truth table (units, components, sources, intervals) is generated and hashed before the first held-out agent run,
and stays out of both arms' environments.

## 10. Answer format

Both arms submit the same JSON schema. Prose may accompany it but is scored only as described in
[section 13](#13-adjudication).

- `status`: `supported`, `insufficient_evidence` or `no_relevant_precedent`.
- `prior_cases`: up to five, ranked, each with a citation.
- `observations`: typed signal claims (`rms`, `kurtosis`, `band_power`, `envelope_peak`), each with value, unit and
  citation.
- `hypotheses`: component, `confirmed` or `unconfirmed`, and supporting citations.
- `timing`: the stated time or interval and its uncertainty.
- `unavailable_evidence`: what could not be examined.

A **citation** is `(recording pseudonym, start_sample, end_sample)`, zero-based and half-open, in both arms. Tool names
and arm-specific wording are not part of it.

## 11. Metrics

**Retrieval** (case level: repeated hits on a unit count once, at its best rank):

- recall@1, @3 and **@5 (primary)**, and precision@1, @3 and @5, after neutral entries are removed;
- with no relevant case in the library, recall is undefined and that incident is scored under H3.

**Citation correctness.** A citation is correct only if all hold:

1. it **resolves**: the recording exists in that incident and the interval is valid;
2. it was **eligible**: inside the incident's evidence;
3. it **supports** the claim under the rule for that claim type:

| Claim | Supported if the independent recomputation on the cited samples gives |
|---|---|
| `rms`, `kurtosis`, `band_power` | a value within ±5% of the stated value |
| `envelope_peak` | a top-five peak within ±max(2%, 1 Hz) of the stated frequency |
| `prior_case` | a resolving interval on that unit; its stated component equals the documented one |
| `hypothesis` (`unconfirmed`) | a cited `envelope_peak` at that component's frequency, or a cited prior case with that documented component |
| `hypothesis` (`confirmed`) | a documented component for the **cited prior case** only. The query unit's cause is never known at incident time, so calling it confirmed is unsupported |

- **Supported-claim share (H2):** supported typed claims over all typed claims, per run. A run with no valid final
  answer scores 0. In families A and B, a valid answer with no typed claims also scores 0, so saying nothing cannot
  pass H2. In families C–F, making no claim can be correct: such a run has no share, is left out of H2, and is counted
  and reported per arm.
- **Unsupported diagnostic claims:** count per run, reported separately from the share, including diagnostic statements
  found in prose ([section 13](#13-adjudication)).

**Appropriate response (H3)**, per incident family:

| Family | Appropriate if |
|---|---|
| C | No `confirmed` hypothesis, and `status` is not `supported` for a fault |
| D | `unavailable_evidence` names the affected unit, and it is not described as showing no fault |
| E | `timing` has an uncertainty covering the true interval, and no exact onset is claimed |
| F | `status` is `no_relevant_precedent`, and no prior case is presented as the same confirmed cause |

**Runtime and cost**, per arm: wall time, tool calls, input, output and cached tokens, cost, exhaustion rate and
infrastructure failures. Database build and export preparation are reported separately from agent time. These are
**model and tool runtimes, not engineer time.**

**Non-agent baselines.** Run on the same per-incident evidence, with the same case-level scoring:

| Baseline | Definition |
|---|---|
| Random | Random eligible windows, 100 seeded draws |
| RMS / kurtosis / crest | Nearest neighbours, features standardised on the incident's library only |
| FFT band power | Nearest neighbours on log power in eight fixed log-spaced bands |
| Envelope-band features | Nearest neighbours on envelope-spectrum power at the unit's documented characteristic frequencies |
| MorletDB search alone | `search_similar` from the query window, without an agent |

Earlier frozen studies found that simple features often match Morlet features ([evaluations](../../docs/evaluations.md)).
So: **a gain may be attributed to MorletDB's search only if "MorletDB search alone" beats the best simple baseline by at
least 0.10 in recall@5.** Otherwise a passing H1 is described as a benefit of the *tool workflow*, not of the
representation or the search.

## 12. Analysis and decision rules

- **Unit of analysis:** the held-out bearing/test. Scores are averaged over repeats within incident, then over
  incidents within unit. Each unit weighs the same.
- **Interval:** a two-sided 95% interval for the mean paired Δ, by inverting an **exact sign-flip test** over units
  (grid step 0.001). Bootstrap intervals are not used for decisions, as they are too narrow with 6 to 10 clusters.
- **Verdicts** follow the table in [section 2](#2-questions-and-hypotheses).
- **Primary analysis:** all admitted held-out units. Ferrara alone and UNSW alone are also reported, and decide nothing
  when UNSW is admitted.
- **Power.** This study is small. With six units, a sign-flip test can reject only when every unit moves the same way,
  so **INCONCLUSIVE is a likely and legitimate outcome**. It must be reported as "not shown", never as "no effect" or
  as a trend toward benefit.
- **Reported, deciding nothing:** each family separately, same-rig versus cross-rig, cause identification with its
  base rate, each baseline, and development results.

## 13. Adjudication

**Layer 1, mechanical.** A scorer independent of both arms resolves citations and recomputes typed claims. It has no
access to the arm label. Its code and tests are frozen before the first held-out run.

**Layer 2, human review of what cannot be checked mechanically.** All `hypotheses`, all diagnostic statements in prose,
and every mechanical failure a reviewer disputes.

- **Blinding.** Outputs are converted to one neutral format with a random run ID. Tool names, tool traces, runtime and
  wording that reveals the arm are removed. A reviewer who can still infer the arm records that.
- **Two reviewers** judge each item independently against the rules in sections 9 and 11, without seeing each other's
  judgments.
- **Disagreements:** the two discuss with the arm still hidden. If unresolved, a third reviewer decides; without one,
  the item is recorded as **unresolved** and counted as unsupported in both arms' sensitivity analysis.
- **Agreement** (Cohen's κ, per item type) is reported before unblinding.
- **Relevance review.** One reviewer checks the truth table against the source documents before the first held-out run.

**If no independent human reviewer is available:**

- H1 and H3 can still be scored from documented truth and the structured answer.
- H2 is reported as **"machine-checked citation support"** for typed claims only.
- The words "independently reviewed" and "evidence-grounded" are not used for the result, and the corresponding #582
  criterion stays open.
- A model may assist in triage but **cannot be the adjudicator of record**. The owner may review, but as a
  non-independent reviewer, and this is stated with the result.

## 14. What may be claimed

| Result | Allowed statement |
|---|---|
| H1, H2 and H3 PASS | "On these public bearing run-to-failure tests, this agent with MorletDB tools retrieved more relevant prior cases than with file exports, without worse citation support or restraint." With the workload, model and limits named |
| H1 PASS, H2 or H3 not | The retrieval gain and the cost in grounding or restraint, side by side. No overall benefit claim |
| H1 FAIL | "Not supported on this workload." Published with the same prominence |
| Any INCONCLUSIVE | "Not shown." The interval is reported |
| Any result | No claim about real plants, engineer time, root-cause accuracy, other machines or the Claude for Manufacturing workflow |

## 15. Freeze, amendments and archive

**Before the freeze, all of:**

1. The owner resolves [section 18](#18-open-decisions-for-the-owner).
2. The UNSW admission gate is applied from documentation only.
3. The XJTU-SY component table is re-checked against its source.
4. The owner confirms that neither held-out dataset was used outside git.
5. The harness, validator, scorer, oracle and baselines pass their tests on **synthetic fixtures**.
6. A development rehearsal on XJTU-SY sets prompts, window size, budgets and compute limits. Development results are
   committed and labelled as development.
7. The leak control of [section 8](#8-information-available-at-incident-time) passes.
8. An independent review of this file is recorded, and its findings are resolved.

**The freeze** is the commit that changes the status line to FROZEN and records: the commit of every tool, the exact
model identifier, prompts, seeds, budgets, window size, expected archive SHA-256 values where the source publishes them
and the analysis code. **Held-out archives are downloaded only after that commit.** Their SHA-256 values are recorded
on first download.

**After the freeze.**

- One confirmation run. No prompt, tool, parameter, threshold, incident rule or scorer changes on the basis of held-out
  data.
- **Deviations** are appended to this file with date, reason, what had been seen when the decision was made, and the
  effect. A harness bug may be fixed and the affected runs repeated only if no affected outcome was read, or if every
  run is repeated and both results are kept.
- Any analysis not specified here is labelled **exploratory** and decides nothing.
- **A held-out dataset is consumed once read.** A failed or inconclusive study cannot be retuned and rerun on it. A new
  attempt needs new data.

**Archive** in `evaluation/investigation_i1/results/`:

- protocol commit, manifests, source URLs, archive and per-recording hashes;
- prompts, configuration, tool and model versions;
- full tool traces, raw model outputs and token and time records for **every** run, including failed runs;
- scorer outputs, blinded review sheets, adjudications, disagreements and deviations;
- the code that regenerates every reported number.

Source recordings and bulk derived features are **not redistributed**. They are identified by source, version and hash.
Traces containing sample excerpts are published only as the data's terms allow (open decision 5).

**Negative results** are published in `RESULTS.md` under the same headings and in the same place as positive ones, and
added to [docs/evaluations.md](../../docs/evaluations.md). The report separates **integration correctness**,
**development measurements** and **confirmation evidence**.

## 16. Limits of generalization

- **Few units, few rigs:** at most 10 held-out units on 2 laboratory rigs, each with one bearing type.
- **One confirmed cause in the primary dataset.** Discriminating between root causes is not tested on held-out data.
- **Accelerated laboratory tests:** constant speed and load within a test, a clean signal path and a single dominant
  failure. Plants have varying load, process noise, sensor faults and several concurrent problems.
- **Constructed history:** the case library, its order, its pseudonymous clock and the injected clock faults are study
  constructions.
- **No maintenance context:** no work orders, close-out notes, operators or contradictory records.
- **Oracle proxy:** fault stage comes from a spectral rule, not from inspection during the test.
- **Known-frequency assistance:** geometry and characteristic-frequency information is supplied, and may not exist for
  a real asset.
- **One agent and model at one time.** Results may not carry over to other models, versions, prompts or budgets.
- **Possible model familiarity** with the public datasets and their papers.
- **The export arm is one reasonable baseline.** A team with mature analysis scripts or a historian may do better than
  an agent given CSV files.

## 17. Dependencies and what can start now

| Item | State on 2026-11-21 | Effect on I1 |
|---|---|---|
| `investigate_interval` and `Client.investigate` (#572, #576, #577) | On `main`, unreleased; release PR #579 open | **Needed.** The freeze pins a commit or release |
| #158 causal history | Open | **Not needed.** Per-incident snapshots enforce the cutoff. Adopting it later requires an amendment before the freeze |
| #580 asset/incident resolver | Open | **Not needed.** Incidents are given as recordings and intervals, and clock cases use the shipped `locate_time` and time anchors. I1 does not evaluate the resolver |
| #573 match explanations | Open | **Not needed, and not to be added mid-study.** A tool change after the freeze would change the condition |
| #575 evidence export | Open | **Not needed.** I1 keeps its own archive |
| #581 Claude for Manufacturing companion | Open | **Out of scope** |
| #217 reviewed findings | Open | **Not used.** The MCP interface stays read-only, and no finding is written back |
| #199 pilots | Open | Where the excluded questions go |

**Can start now, with no dataset and no model:** the owner's decisions; the UNSW documentation check; the incident
generator, snapshot builder, independent validator, leak control, scorer and baselines, all on synthetic fixtures.

**Needs the owner's approval first:** downloads, model spend and any run. XJTU-SY development comes first; held-out
data comes only after the freeze.

## 18. Open decisions for the owner

1. **Held-out data.** Accept Ferrara as primary and UNSW as conditional replication, knowing that Ferrara has one
   failure cause, so that I1 tests fault-stage retrieval and not root-cause discrimination? *Recommended: yes.* The
   alternative is reusing consumed data, which could not be called held-out.
2. **Model and spend.** Which exact model identifier, and what spending cap for up to 336 confirmation runs (at most
   134.4 million tokens) plus development? *Recommended: one current model, with a cap set after the development
   rehearsal.*
3. **Reviewers.** Who are the two independent reviewers, and is there a third? Without them, H2 is reported as
   machine-checked only and the corresponding #582 criterion stays open.
4. **Small sample.** Run with 6–10 units, accepting that INCONCLUSIVE is likely, or first look for another eligible
   public dataset? *Recommended: run, and report it as a first bounded test.*
5. **Licence and publication.** Treat the Ferrara data as CC BY 4.0 (its data records), despite the article's CC BY-NC
   4.0, and may short sample excerpts appear in published traces? *Recommended: ask the authors or publish identifiers,
   metrics and hashes only, with no sample excerpts.*

## Deviations and amendments

(none; the protocol is not frozen)
