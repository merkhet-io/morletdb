# Related work notes: "Machines with a Memory"

Companion to `related.bib`. Every key below was checked on 2026-11-05: DOI metadata through the Crossref API, preprints through the arXiv API or abstract page, and documentation pages by fetching them. The "what it says" claims come from the abstract or the fetched page unless marked otherwise. There are no page-specific quotes or pin cites. Add those only after reading the full text.

Relation tags: **support** (backs a premise of the paper), **contrast** (a different design choice we argue against or depart from), **gap** (shows what is missing and what MorletDB adds).

---

## 1. Episodic memory, consolidation, adaptive forgetting

**What the literature establishes.** Tulving separated episodic memory (specific, dated events in their context) from semantic memory (general knowledge). Systems-consolidation work describes memory moving from a fast, specific store (hippocampus) to a slow, generalizing one (neocortex). Recent theory treats forgetting as functional rather than a failure. Anderson and Schooler showed that how available a memory is tracks how likely it is to be needed, based on how often and how recently it was used. Together these give the paper its vocabulary: episode, consolidation, deliberate forgetting, and a keep-value driven by recency and use.

- `tulving1972`: the original episodic/semantic distinction. **Support**: our "episode" (exact interval + operating context + outcome) is episodic in Tulving's sense, while roll-ups and summaries are semantic. Verification: no DOI. Chapter details (pp. 381–403, eds. Tulving & Donaldson, Academic Press, 1972) agree across a Cambridge University Press reference entry, Wikipedia, and a 1987 Current Contents "Citation Classic" record. The chapter text itself was not opened.
- `tulving2002`: Annual Review update. Defines episodic memory around remembering specific past events and a subjective sense of time. **Support**: a citable modern definition for "have we seen this before, and what was it?"
- `mcclelland1995`: complementary learning systems. The abstract argues that the hippocampus permits rapid learning of new items, and that interleaved reinstatement lets the neocortex slowly extract structure. **Support/analogy**: raw recent episodes (fast, specific) → representatives and roll-ups (slow, structural). This is the rationale for a ladder rather than a single cutoff.
- `squire2015`: review of systems consolidation. The abstract notes that hippocampal damage spares remote memories more than recent ones, and that replay drives hippocampal–neocortical dialogue. **Support**: consolidation as a staged reorganization, not deletion.
- `richards2017`: the abstract argues that transience (1) improves flexibility by reducing the influence of outdated information and (2) prevents overfitting to specific past events, promoting generalization. Its framing is that "the goal of memory is to optimize decision-making." **Support (central)**: forgetting on purpose is a design goal, not a storage compromise.
- `anderson1991`: the abstract (read from the first page of the PDF) shows that the probability a memory will be needed depends on frequency, recency and the pattern of prior exposures, in New York Times headlines, parental speech and electronic mail. It argues that memory is adapted to these statistics. The introduction frames memory as managing a huge store where maintaining access to everything is too expensive. **Support (central)**: an empirical basis for using recency and use in keep-value.

## 2. Memory for LLM agents

**What the literature establishes.** LLM agents now keep external memory stores, score memories for retrieval (recency, importance, relevance), move them between memory tiers, and sometimes decay them on a schedule modeled on forgetting curves. Benchmarks test recall over long interaction histories, and a 2025 position paper argues that episodic memory is the missing component. This work stores text or embeddings, and its benchmarks measure question-answering accuracy. It does not address retention under a storage budget for numeric high-rate signals, or what is lost when memory is consolidated.

- `park2023generative`: Generative Agents. The memory stream scores each memory as a weighted sum of recency (exponential decay since last retrieval, factor 0.995 per game hour), importance (LLM-rated 1–10 at creation) and relevance (embedding cosine similarity). Scores are min–max normalized with all weights set to 1. "Reflection" writes higher-level memories that cite their sources. (Checked in the arXiv HTML full text.) **Support/contrast**: the closest precedent for a multi-factor score. Ours scores *what to keep*, not *what to retrieve*, and adds context rarity and storage cost. Reflections that cite their sources parallel our cluster representatives with per-member summaries.
- `packer2023memgpt`: MemGPT. "Virtual context management" modeled on an OS memory hierarchy, paging data between fast and slow tiers. **Contrast**: tiers by access speed, with nothing forgotten on purpose. Our ladder tiers by fidelity, and data leaves it deliberately.
- `shinn2023reflexion`: Reflexion (NeurIPS 2023). Agents "maintain their own reflective text in an episodic memory buffer" across trials. **Support**: past episodes plus outcome feedback improve later decisions, the agent analogue of recalling what a past fault turned out to be. Note: the NeurIPS proceedings list five authors; the arXiv version adds Edward Berman. The .bib follows the proceedings.
- `zhong2024memorybank`: MemoryBank (AAAI 2024). The abstract describes a memory-update mechanism inspired by the Ebbinghaus forgetting curve that weighs elapsed time and a memory's significance. **Support/contrast**: the nearest example of forgetting on purpose in agent memory. It is one decay curve with no graded fidelity ladder and no ledger.
- `zhang2025survey`: survey of memory mechanisms in LLM agents (ACM TOIS 2025; arXiv 2404.13501). **Gap**: a broad overview of memory sources, forms and operations. Cite it to show that this line of work is about text and embedding memory, not lossless signal archives.
- `wu2025longmemeval`: LongMemEval (ICLR 2025). 500 questions testing information extraction, multi-session reasoning, temporal reasoning, knowledge updates and abstention. **Contrast**: evaluates answers. Our retrieval regret evaluates what a compacted archive can still retrieve against the full archive.
- `pink2025episodic`: position paper. Proposes five properties of episodic memory for agents: long-term storage, explicit reasoning, single-shot learning, instance-specific memories, contextual relations. **Support (central)**: these properties match our episode definition closely (instance-specific interval, contextual relations = operating context, single-shot = one occurrence of a fault is enough to keep).
- Verified but left out of the .bib to stay within budget: Maharana et al., "Evaluating Very Long-Term Conversational Memory of LLM Agents" (LoCoMo), ACL 2024, doi:10.18653/v1/2024.acl-long.747. Add it if a second benchmark is wanted.

## 3. Case-based reasoning in maintenance and fault diagnosis

**What the literature establishes.** CBR solves new problems by retrieving similar past cases, reusing and revising their solutions, and retaining new cases (the 4R cycle). It has been applied to machine fault diagnosis since at least the early 2000s, typically on features extracted from sensor signals and with a curated case base. **Gap**: these systems assume someone curated a case library. None addresses keeping raw episodes searchable at scale, or deciding which cases to retain, demote or forget under a storage budget, which is the "Retain" step we make explicit.

- `aamodt1994`: the canonical CBR cycle (retrieve, reuse, revise, retain). **Support (central)**: "have we seen this before, and what was it?" is CBR's retrieve step over machine episodes. Our keep-value and ladder are a policy for the retain step.
- `olsson2004`: wavelet features from sensor signals plus CBR to classify industrial faults, applied to industrial robots at ABB Robotics (per publisher and institutional records). **Support**: CBR on industrial sensor data, motivated by noisy signals and a shortage of experienced technicians.
- `olsson2005survey`: short survey of case-based diagnostic systems for machines (e.g. gearboxes in industrial robots, industrial printers). **Support**: shows that the approach has an established body of work.
- `yang2004artkohonen`: an ART-Kohonen network proposes hypotheses that guide CBR to similar past cases. Applied to electric-motor fault diagnosis. **Support/contrast**: retrieval over learned features. We retrieve over exact stored waveforms.
- `yang2008turbomachines`: a CBR subsystem added to a fuzzy rule-based turbomachinery vibration diagnosis system lets past experience be applied more directly, which the authors report improves accuracy. **Support**: past-case recall adds value beyond rules in vibration diagnosis.

## 4. Time-series and floating-point compression

**What the literature establishes.** Lossless float compression for time series has gone from XOR-with-previous encodings (Gorilla, Chimp) to decimal-aware and vectorized schemes (ALP, BtrBlocks' pseudodecimal and cascaded encodings, Pcodec's binning). Error-bounded lossy compressors (SZ, ZFP) trade precision for size under a user-chosen bound. **Gap**: all of these shrink bytes at fixed fidelity, so cost still grows linearly with retained history. Our ladder changes *what* is kept, not how compactly, and it stacks on top of any of these codecs.

- `pelkonen2015gorilla`: Facebook's in-memory TSDB. The abstract states that users value aggregate analysis over individual points and that "recent data points are of much higher value than older points." **Support** for recency in keep-value. **Contrast**: that recency judgment fits ops metrics, but not rare industrial faults whose old episodes matter.
- `liakos2022chimp`: Chimp. Lossless streaming float compression for time-series databases, improving on Gorilla-style XOR encoding. **Gap/baseline**.
- `afroozeh2023alp`: ALP (PACMMOD 1(4), presented at SIGMOD 2024). Vectorized lossless double compression. The abstract reports better speed and ratio than Gorilla, Chimp128, PDE, Elf and Patas. **Baseline**: the state of the art for lossless floats.
- `kuschewski2023btrblocks`: BtrBlocks (SIGMOD 2023). Cascaded lightweight columnar encodings for data lakes, including a pseudodecimal float scheme (stated from the paper, not re-read for this note). **Baseline**.
- `loncaric2025pcodec`: Pcodec (arXiv 2025). Lossless numerical compression. A binning algorithm converges to the entropy of smooth i.i.d. integers, with mode and delta preprocessing. **Baseline**.
- `di2016sz`: SZ. Error-bounded lossy compression for HPC floating-point data. **Contrast**: lossy at a fixed bound across all data. Our design keeps raw episodes exact while they are kept, then drops to *exact* summaries, never to approximate samples.
- `lindstrom2014zfp`: ZFP. Fixed-rate compressed floating-point arrays. **Contrast**: same as SZ.
- `chiarot2023survey`: ACM Computing Surveys taxonomy of time-series compression (lossless and lossy). **Context**: the survey to cite for the field.

## 5. Downsampling, visual summaries, mergeable summaries

**What the literature establishes.** Visualization-oriented reduction (M4) can give error-free line charts by keeping min/max/first/last per pixel column. Heuristic downsampling (LTTB) keeps visual shape with few points. Mergeable summaries are sketches that can be combined across partitions with error and size guarantees preserved. **Support**: our "exact mergeable summaries/roll-ups only" rung builds on this, but restricts itself to *exact* mergeable statistics (count, sum, min, max, moments) so that roll-ups of roll-ups stay exact and auditable.

- `jugel2014m4`: M4 (VLDB 2014). Aggregation-based reduction giving error-free line-chart visualizations at high reduction rates. **Support**: a display-exact representation. **Gap**: M4 is exact for the picture but not for search or spectral analysis.
- `steinarsson2013lttb`: LTTB (MSc thesis, University of Iceland, 2013). Verification: the thesis handle hdl.handle.net/1946/15343 and the attribution come from the author's own repository (github.com/sveinn-steinarsson/flot-downsample). The Skemman repository returned HTTP 403 to automated fetches, so the title and year come from consistent third-party citations and the PDF was not opened. **Contrast**: a visual heuristic with no error guarantee, unsuitable as a rung of record.
- `agarwal2012mergeable` / `agarwal2013mergeable`: PODS 2012 and TODS 2013 versions. Defines mergeability and shows that summaries such as heavy hitters, quantiles and ε-approximations can be merged without growing error or size. Cite the TODS version as the journal reference. **Support (central)**: a formal basis for roll-ups that compose over time. Our rung uses the exact (error-free) special case.

## 6. Retention and roll-ups in TSDBs (documentation)

**What the practice establishes.** Production TSDBs already use a retention ladder of sorts: raw data with a time-to-live, plus downsampled aggregates kept longer. Thanos downsamples raw → 5 min (after 40 h) → 1 h (after 10 days), keeping count/sum/min/max/counter per interval, with retention set per resolution. Prometheus alone keeps local data for 15 days by default (time- or size-based) and does not downsample. TimescaleDB continuous aggregates refresh incrementally, and raw chunks can be dropped while the aggregates stay. InfluxDB v1 pairs retention policies with continuous queries. RRDtool pre-allocates fixed-size round-robin archives with AVERAGE/MIN/MAX/LAST consolidation. **Gap (central)**: every one of these is age-only. They demote by timestamp, not by novelty, labels, use or context rarity. None keeps cluster representatives or a ledger of what was forgotten, and none is evaluated by retrieval regret.

- `thanos_compactor`: two-step downsampling and per-resolution retention flags. The docs note that downsampling is for query speed, not space, and can increase storage by about 3x. **Contrast**.
- `prometheus_storage`: default 15-day retention and size-based retention. Long-term storage is delegated to remote integrations. **Contrast**: keep-or-drop by age or size.
- `timescale_caggs`, `timescale_retention_caggs`: incrementally refreshed aggregates, and dropping raw data while keeping aggregates (the refresh window must not cover deleted data). **Contrast**: the nearest PostgreSQL-native analogue of our roll-up rung, but age-only. (docs.timescale.com now redirects to tigerdata.com/docs.)
- `rrdtool_rrdcreate`: fixed-size RRAs that overwrite the oldest rows, with consolidation functions. **Contrast**: the classic fixed-budget memory. Fidelity loss is uniform and blind to content.
- `influxdb_downsample`: a two-hour raw retention policy plus a 52-week policy fed by a 30-minute-average continuous query. **Contrast**.

## 7. Data reduction by representatives; similarity under shift

**What the literature establishes.** There are principled ways to keep a small subset that stands in for the whole: uniform one-pass samples (reservoir), weighted subsets with approximation guarantees (coresets), and actual members as cluster centers (medoids). Time-series data mining supplies motif and discord discovery (matrix profile) and similarity under temporal misalignment (DTW, made scalable by the UCR suite). **Support/gap**: our cluster rung keeps the medoid plus outliers, which are real stored episodes rather than synthetic centroids. Recall@k against the full archive plays a role similar to a coreset's guarantee, but measured empirically.

- `vitter1985`: reservoir sampling. A uniform random sample of fixed size from a stream of unknown length in one pass. **Contrast/baseline**: uniform retention ignores novelty and labels, so rare faults are lost in proportion to their rarity. A natural baseline for keep-value.
- `feldman2020coresets`: updated survey of coresets (Springer chapter, 2020). **Support**: the theoretical vocabulary for "a small weighted subset that preserves the answer."
- `bachem2017coresets`: overview of practical coreset constructions for ML (arXiv). **Support**: an accessible companion to Feldman.
- `kaufman1990`: *Finding Groups in Data*, which includes PAM (partitioning around medoids). **Support (central)**: medoids are actual observations, so a kept representative is a real, replayable episode.
- `yeh2016matrixprofile`: Matrix Profile I (ICDM 2016). All-pairs similarity join for subsequences that unifies motifs, discords and shapelets. **Support**: motifs = recurring episodes (candidates for consolidation). Discords = novel episodes (high keep-value).
- `sakoe1978`: DTW with a slope-constrained adjustment window (the Sakoe–Chiba band). **Context**: similarity under time shift and warping. Explains why our recall metric has to say whether matches are shift-tolerant.
- `rakthanmanon2012`: UCR suite (KDD 2012). Exact subsequence search under z-normalized ED/DTW at trillion scale, using lower bounds and early abandoning. **Support/baseline**: the exact-search reference our retrieval-regret ground truth borrows from.

## 8. Condition monitoring, fault diagnosis, novelty detection

**What the literature establishes.** Vibration-based condition monitoring has a mature signal-processing toolkit (envelope analysis, spectral and order analysis), and CBM practice organizes data acquisition, processing and maintenance decisions. ML for fault diagnosis has moved from hand-crafted features to deep learning. Reviews point to scarce labeled fault data and transfer across machines and conditions as open problems. Novelty detection, which models normal behavior and flags departures, is the standard framing when fault examples are rare. **Gap**: these works assume the needed history is available. None treats which history to keep as part of the monitoring problem.

- `randall2011`: textbook on vibration-based condition monitoring (Wiley, 2011; Crossref lists online publication Dec 2010). **Context**: domain grounding for kHz vibration signals and why exact waveforms matter (diagnostics need the raw time signal and spectra, not averages).
- `jardine2006`: widely cited review of diagnostics and prognostics for CBM. **Context**: places episode recall within the acquisition → processing → decision chain.
- `lei2020`: review and roadmap of ML for machine fault diagnosis (MSSP 138, 106587). **Support/gap**: the review's emphasis on limited labeled fault data supports keeping labeled and rare episodes at high fidelity, the "importance/labels" term.
- `pimentel2014`: review of novelty detection (Signal Processing 99). **Support**: the methods behind our "novelty" keep-value term. **Contrast**: they score novelty in order to *alarm*. We score it in order to *retain*.

## 9. Experience replay as an analogy for keep-value

**What the literature establishes.** RL agents store past transitions and replay them. Prioritized replay samples transitions by how informative they are (the TD-error magnitude, per the paper body) rather than uniformly. Lifelong-learning work compares strategies for which experiences to *keep* in a bounded buffer. **Support (analogy)**: keep-value plays the same role for an archive that replay priority plays for a buffer. Note the difference: replay priority sets *sampling frequency*, while keep-value sets *fidelity rung*.

- `schaul2016per`: Prioritized Experience Replay (ICLR 2016). The abstract says it replays "important transitions more frequently" and reports DQN with prioritized replay beating uniform replay on 41 of 49 Atari games. **Support (analogy)**: non-uniform value of past experience pays off.
- `lin1992`: experience replay in RL agents (Machine Learning 8(3–4)). Commonly credited as the origin of experience replay. **Context**.
- `isele2018selective`: Selective Experience Replay (AAAI 2018). Compares surprise, reward, distribution matching and coverage maximization for deciding what goes into long-term memory. The abstract reports distribution matching as best overall, and coverage maximization helping when less-trained tasks matter more. **Support/caution (central for design)**: purely novelty-driven retention can underperform. This argues for keep-value mixing novelty with coverage/rarity of context, and for checking survival of known events and drift visibility, not only novelty.

---

## Not verified / not included

- Primary full text not opened for `tulving1972` (bibliographic data from three consistent secondary sources) and `steinarsson2013lttb` (repository blocked automated access; handle confirmed through the author's GitHub README). Both are in the .bib because their existence and bibliographic details are well corroborated. Check the PDFs before citing anything specific.
- BtrBlocks' pseudodecimal detail and PER's TD-error priority come from familiarity with the full papers, not the abstracts re-read today. Both are standard and safe, but confirm them if quoted.
- Not used: Xie et al. 2023 (improved CBR for rotating machinery, J. Phys. Conf. Ser.) and other CBR conference papers found in search. Their details could not be confirmed, so they are not in the .bib.
