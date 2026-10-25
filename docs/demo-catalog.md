# Demo and evaluation catalog

Updated: 2026-10-25. Status: **proposed experiments**, not implemented product
capabilities, validated diagnoses or committed roadmap deliveries.

Collect demonstrations that help an engineer answer a recurring question using
recorded signals. Value and priority below are product judgments to test, not
market-size estimates or demonstrated customer demand. Choose one next experiment;
this catalog is not a request to build fifteen applications.

## What already exists

The [recording walkthrough](recording-workflow.md),
[visual notebook](../examples/notebooks/README.md) and
[offline wave-index PoC](wave-index-poc.md) are reusable foundations.
They provide ingestion, interval inspection and controlled retrieval experiments.
The PoC is not a persistent PostgreSQL index. The small CWRU baseline does not
establish a Morlet advantage over simple statistical features.

## Prioritized shortlist

P1 = strongest next product-validation candidate. P2 = valuable but needs partner
data or additional representation/integration work. P3 = showcase or exploratory
extension. A high commercial hypothesis does not imply easy data access.

| ID | Priority | Demo | Question an engineer asks | Starting data |
|---|---|---|---|---|
| D01 | P1 | Industrial sound triage | Which reviewed episodes resemble this new machine sound? | MIMII; later partner recordings |
| D02 | P1 | Spindle and cutting-tool investigation | Does this cutting episode resemble previously reviewed wear or instability? | NASA milling; controlled machining runs |
| D03 | P1 | Bearing degradation explorer | How do similar vibration episodes change over a machine's history? | Existing CWRU baseline; NASA/IMS; permission-dependent Paderborn |
| D04 | P1 | End-of-line quality comparison | Which accepted/rejected units had a similar test trace? | Partner test bench or owned hardware; no public corpus established |
| D05 | P2 | Actuator-cycle diagnostics | Where has this movement/current/vibration sequence occurred before? | Owned robot/linear actuator or partner maintenance data |
| D06 | P2 | Electrical-transient and firmware regression | Did a new controller/firmware revision change a known waveform pattern? | Owned power-supply/controller bench traces |
| D07 | P2 | Valve and pressure-cycle investigation | Which previous opening/closing or pressure-recovery episodes look similar? | Owned pneumatic/hydraulic rig; partner pressure traces |
| D08 | P2 | Generator and support-equipment cold starts | Which earlier startup sequences resemble this one, and what was observed later? | Partner/owned equipment logs and sensor recordings |
| D09 | P2 | Structural response comparison | Has the response to the same controlled excitation changed? | Owned beam/fixture experiment; partner structural test records |
| D10 | P3 | HDD acoustic/vibration explorer | Is this drive episode similar to reviewed behavior under the same workload? | Own recordings paired with SMART/I/O context |
| D11 | P2 | Seismic replay and live event explorer | Which historical seismic intervals resemble this new transient? | GFZ GEOFON/EarthScope; official catalog annotations |
| D12 | P3 | Spoken-phrase retrieval | Where was an acoustically similar phrase spoken? | Consented local recordings across sessions/speakers |
| D13 | P3 | Speaker-similarity retrieval | Which recordings contain a similar speaker representation? | Consented local recordings plus an external speaker model |
| D14 | P3 | Environmental sound search | Where do similar knocks, alarms or other events occur? | Owned recordings; rights-compatible ESC subset |
| D15 | P3 | Battery aging trajectory comparison | Which historical charge/discharge trajectories resemble this cycle? | NASA battery experiments; separately approved bench data |

## Demonstration briefs

### D01 — Industrial sound triage

**Potential value:** make maintenance investigations searchable and auditable.
MIMII supplies normal/anomalous recordings of pumps, fans, valves and slide rails
with factory background noise. Begin with one asset type and one microphone
channel; its array recordings do not grant MorletDB multichannel capability.

Show an unfamiliar query, playable historical matches, spectra and source metadata.
Compare RMS/statistics, FFT/MFCC features, pooled Morlet, temporal cells and an
external audio embedding where practical. Hold out source files and physical
machine identities where the dataset supports it. Report reviewed retrieval
precision, negative-query behavior and work/latency; class labels alone do not
establish the same root cause. Source and license: [MIMII](https://zenodo.org/records/3384388).

### D02 — Spindle and cutting-tool investigation

**Potential value:** help process engineers compare suspect cutting intervals with
reviewed runs. Start with the [NASA milling entry](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
and inspect its run, operating-condition and wear documentation before selecting
the task. Confirm actual available channels and units; do not invent annotations.

Compare within compatible speed/feed/tool/material cohorts first. Hold out whole
tools/runs and measure useful wear-related retrieval or reviewed event localization.
RPM invariance, chatter diagnosis and causal attribution are not existing features.
Use FFT/order-domain or other appropriate machining baselines alongside Morlet.

### D03 — Bearing degradation explorer

**Potential value:** connect a present vibration episode with earlier reviewed
conditions and subsequent observations. Build on the [existing evaluation](../evaluation/README.md),
then inspect [NASA/IMS bearing records](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
for chronology and outcome documentation. [Paderborn](https://mb.uni-paderborn.de/kat/forschung/bearing-datacenter)
offers vibration/current experiments but has noncommercial restrictions.

Show a chronological history with retrieved intervals. Split by physical bearing
and experiment, not adjacent windows. Compare statistical/FFT/envelope baselines.
Fault similarity needs condition labels; early warning needs genuinely pre-failure
recordings, event times, lead time and false-alert measurement. Do not infer
remaining life from a nearest-neighbor distance.

### D04 — End-of-line quality comparison

**Potential value hypothesis:** turn archived production tests into a searchable
reference for quality and rework engineers. Use a repeatable motor/pump/actuator
test program with independently recorded acceptance and inspection outcomes.

Show a new unit's trace, matching accepted/rejected units and their inspection
records. Hold out physical units and later production batches; account for fixture,
sensor, temperature and product-revision changes. Compare existing tolerance/test
limits and simple features. Measure reviewed retrieval and false rejects at a
predeclared rule. Data access and reliable outcome linkage are the first gates;
no matching public corpus or production accuracy is claimed here.

### D05 — Actuator-cycle diagnostics

**Potential value hypothesis:** find repeat episodes of friction, contact or
sequence changes during maintenance. Capture current, position or vibration from
an owned test actuator; inspect one channel initially with commanded cycle context.

Compare complete repeats, shuffled/reversed stages, timing shifts and different
loads. Measure episode retrieval/localization against aligned traces and bounded
DTW baselines. Longer cycles, time warping and cross-channel association need
explicit additions; current fixed windows and positional cells do not supply them.

### D06 — Electrical-transient and firmware regression

**Potential value hypothesis:** help hardware/firmware teams find repeated startup,
load-step or reset behavior across test revisions. Capture owned voltage/current
or oscilloscope traces plus stimulus, firmware and test-program identifiers.

Retrieve older comparable transients and show what changed. Include normal
variation and known independently verified regression cases. Compare peak,
settling-time/overshoot measurements and aligned waveform error. Measure whether
retrieval finds a useful prior case beyond these simple checks. Changed supply,
load, probing or calibration must not masquerade as a firmware defect.

### D07 — Valve and pressure-cycle investigation

**Potential value hypothesis:** make process-cycle changes inspectable using
pressure or acoustic traces and command timestamps. Collect a controlled owned
rig or approved partner recordings; no public pressure corpus is asserted.

Retrieve opening/closing and recovery episodes with known operating context.
Compare timing/threshold rules and pressure-shape distances. Use reviewed outcomes
for leakage/stiction claims. Ordered motif joins, synchronized channels and
automatic leak diagnosis remain future capabilities.

### D08 — Generator and support-equipment cold starts

**Potential value hypothesis:** support maintenance review of repeat startup
sequences for generators, pumps or other support equipment, including defense
engineering contexts. Approved data and independent maintenance outcomes are needed.

Show historical starts under comparable ambient temperature, load and equipment
configuration. Compare conventional startup timing and telemetry checks. Evaluate
retrieval by held-out equipment/start sessions. A similar startup is not a verified
readiness decision; sparse slow telemetry may favor conventional time-series
methods instead of the present wavelet representation.

### D09 — Structural response comparison

**Potential value hypothesis:** retrieve comparable impulse/forced-response tests
from beams, mounts or fixtures. Start with an owned controlled experiment and
independently verified assembly states; no labeled public corpus is assumed.

Hold excitation and sensor placement constant, then test changed fixtures and
measurement sessions. Compare modal/FFT features and raw response distances.
Report retrieval and nuisance sensitivity. Identifying a crack or fatigue state
requires independent evidence; a changed waveform alone is not a diagnosis.

### D10 — HDD acoustic/vibration explorer

**Value:** an approachable rotating-machine bench demonstration. Record spare,
backed-up drives during repeatable idle, read/seek and spin-up states, pairing
waveforms with model, firmware, mounting, workload and SMART/I/O context.

Hold out physical drives and sessions; normal seeking/parking must be hard
negatives. [Backblaze](https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data)
provides daily SMART/failure telemetry, not a paired acoustic failure dataset.
Separate finding similar behavior, diagnosing a reviewed fault and predicting a
future failure. Do not join unrelated Backblaze units to locally recorded sounds.

Representation gate: at 48 kHz, the current lowest Morlet band is about 750 Hz,
above nominal 90/120 Hz rotation fundamentals for 5400/7200 RPM. Its maximum search
window is only about 85 ms. Evaluate versioned lower-frequency and longer-temporal
representations; do not assume the default captures all relevant behavior.

### D11 — Seismic replay and live event explorer

**Value:** a public-data demonstration of continuous ingestion, waveform history,
data quality and analyst investigation. Start with historical
[GEOFON](https://geofon.gfz.de/waveform/) or
[EarthScope](https://docs.earthscope.org/service/seedlink) waveforms and replay them
through the future live adapter. Add SeedLink only after replay/recovery works.

Compare against a seismic detector and matched-filter/correlation or specialist
model baselines. Keep catalog association, trigger detection and waveform retrieval
separate. Split by events/stations/time as appropriate; report background station-hours,
unmatched triggers, reviewed relevance, gaps and observed latency. An unmatched
trigger is not automatically a false positive.

Native seismic rates, frequency coverage, UTC mapping and discontinuities require
work; the current minimum rate is 128 Hz. Historical explosion references may
support research with authoritative labels. Similarity cannot establish nuclear
origin; [CTBTO](https://www.ctbto.org/our-work/monitoring-technologies) uses complementary
monitoring technologies. Operational warning or treaty-verification claims are
outside this proposed demo.

### D12 — Spoken-phrase retrieval

**Value:** an intuitive audible demonstration of similarity and invariance limits.
Use consented local recordings, phrase annotations and multiple recording sessions.
Show selected phrases and playable retrieved intervals with provenance.

Separate exact acoustic repeats from same words spoken differently. Include same
speaker/different words and different speaker/same words, speech-rate changes and
noise. Compare MFCC/bounded DTW, transcript search and external speech embeddings;
hold out sessions/source recordings. Transcription requires an external model;
the wave index has no demonstrated word understanding or speed invariance.

### D13 — Speaker-similarity retrieval

**Value hypothesis:** test versioned learned-representation storage/retrieval and
playback provenance. Use consented recordings and an external speaker encoder.
Morlet features alone are not established speaker identity features.

Use separate enrollment/query sessions and unseen-speaker negative queries.
Compare cosine search over the same embeddings and evaluate false acceptance/
rejection at thresholds selected on separate validation data. This demonstrates
an embedding adapter, not a biometric authentication product or a proven wave-index
advantage. External model identity and preprocessing must be pinned.

### D14 — Environmental sound search

**Value:** demonstrate acoustic retrieval beyond speech with knocks, alarms and
other annotated events. Use owned sounds or an explicitly permitted
[ESC subset](https://github.com/karolpiczak/ESC-50); class labels are proxies for
relevance, not proof of the same physical cause.

Preserve source-recording/fold separation and compare MFCC, temporal/pool features
and external audio embeddings. Measure retrieval on noisy and unknown queries.
ESC-50 has noncommercial terms; the separately identified ESC-10 subset has
attribution terms. Verify per-clip attribution and intended-use permissions.

### D15 — Battery aging trajectory comparison

**Value hypothesis:** help battery-test engineers retrieve comparable historical
charge/discharge behavior. Inspect the [NASA battery experiments](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
and their measurement/outcome documentation, then define one bounded task.

Slow cycles, irregular samples and age/load/temperature dependencies are not
automatically a fit for the current waveform API. Compare capacity/impedance trends,
cycle-shape distances and conventional time-series models first. Split by physical
cell and time. Prediction needs independently defined outcomes and lead time;
raw-input wavelets do not reproduce wavelet pooling inside a neural network.
Fast electrical transients are a separate D06 experiment.

## Recommended next choices

- **Public-data diagnostics:** D01 with a rights-compatible bounded MIMII subset,
  replay and a reviewed match table. It offers audible results and harder real
  negatives without first designing a hardware acquisition system.
- **Product/design-partner evaluation:** D04 if a partner can provide repeatable
  test traces and acceptance/inspection records; otherwise D02/D03 with verified
  dataset conditions and a clear external engineer workflow.
- **Local hardware learning:** D10 for controlled recording and context collection.
  Treat a small drive collection as an experiment, not failure-prediction evidence.
- **Continuous public-stream showcase:** D11, once native-rate/gap handling is
  implemented and historical replay is reproducible.
- **Fast understandable audio showcase:** D12 or D14; explicitly compare learned
  embeddings/transcripts where the task depends on semantic understanding.

## Shared acceptance contract

Every selected demo needs a named user, an actual question, a bounded source
manifest and a predeclared usefulness criterion. A five-minute visual story should
lead to raw evidence an engineer can inspect.

1. **Reproduce:** pin sources/checksums, rates, units, preprocessing, annotations,
   representation/model versions and query/gallery splits. Cache approved data;
   do not silently replace unavailable real data with synthetic clips.
2. **Compare fairly:** include simple domain baselines, existing pooled search,
   exhaustive temporal scoring and approximate candidates on the same population.
   Separate candidate recall from human relevance and calibrated diagnosis.
3. **Challenge nuisance factors:** use independent devices/runs/sessions and
   negatives, not overlapping windows from the same recording. Vary workload,
   mounting/gain, noise, speed or station context where relevant.
4. **Expose cost/failure:** report posting visits, scored windows, extraction/build
   cost, repeated query latency, hardware, gaps, unknowns and budget failures.
   Fewer reranks alone do not demonstrate lower total cost.
5. **Make limits visible:** keep raw intervals, labels and provenance inspectable;
   distances are not probabilities. Abnormality, fault type and future failure
   are separate claims requiring different outcome evidence.

The current 512..4096-sample windows, fixed relative frequency layout and minimum
128 Hz rate constrain several ideas here. The present SQL path does not provide
resampling, time warping, general motif joins or synchronized multichannel search.
External ML encoders and calibrated diagnostic decision rules are separate work.
See [architecture](architecture.md) and [retrieval design](advanced-retrieval.md).

## Data and source register

Primary source pages checked 2026-10-25. Access/licensing is separate from code
licensing: Apache-2.0 does not relicense third-party datasets or recordings.
Verify the selected version, individual files and intended demonstration use
before downloading or redistributing; no data is bundled by this catalog.

| Source | Relevance and practical boundary |
|---|---|
| [MIMII public 1.0](https://zenodo.org/records/3384388) | Industrial sounds; CC BY-SA 4.0. Preserve attribution and review share-alike obligations for distributed adaptations. Start with bounded data; inspect archive granularity. |
| [Paderborn Bearing DataCenter](https://mb.uni-paderborn.de/kat/forschung/bearing-datacenter) | Vibration/current research data; CC BY-NC 4.0; provider requests contact for commercial use. Do not assume a company demo is covered. |
| [NASA PCoE repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) | Milling, IMS bearings and battery starting points; verify original provider documentation, labels, access and terms for each selection. |
| [ESC-50 author repository](https://github.com/karolpiczak/ESC-50) | Environmental audio; noncommercial full collection, identified ESC-10 attribution subset; retain per-clip attribution and source splits. |
| [GEOFON](https://geofon.gfz.de/waveform/) / [EarthScope SeedLink](https://docs.earthscope.org/service/seedlink) | Archived/live seismic sources; verify channel access, attribution and stream rules. Continuity/latency must be observed, not assumed. |
| [Backblaze Drive Stats](https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data) | SMART/outcome context; not an acoustic waveform corpus. Check provider terms separately. |

For every promoted idea, add the implementation PR, exact source manifest,
evaluation report and remaining evidence gaps here. Mark status from evidence;
a proposed demo or available dataset is not a validated product capability.
