# W1 evaluation: wavelet vs standard indicators on FEMTO run-to-failure bearings

**Decision under the frozen rules (with amendment 2): CLOSE.** Morlet spectral
kurtosis did not raise stable alarms earlier than the STFT kurtogram:

| | Value |
|---|---|
| Δ mean stable lead | **−0.056** |
| 95% CI | [−0.168, 0.000] |
| Unstable alarms | 6 vs 6 |

The CI upper bound is below +0.05, so wavelet indicators earn no niche in early
bearing-fault detection here. Per the protocol, further wavelet work in this
project needs new evidence.

- **Protocol:** [../../PROTOCOL.md](../../PROTOCOL.md). It was frozen at `1841fd7`
  before the FEMTO download.
- **Logged changes:**
  - a loader fix with no method change;
  - amendment 2, decided before evaluation: only the impulsiveness family decides.
- **Run:** one run at `ab464c3` (`commit.txt`), on 2026-10-26, over the 11
  full-test-set bearings, horizontal channel. Candidates come from development:
  `../dev/candidates.json`.
- **Environment:** Apple M5 Max, macOS 27.0.1, Python 3.12, NumPy, SciPy,
  PyWavelets. MorletDB features come from the Rust `features` example built at
  `bb4ee6f`.

## Per indicator (11 bearings)

Stable lead is the fraction of post-calibration life remaining at a stable alarm.
It is 0 for an unstable alarm or no alarm.

| Indicator | Mean stable lead | Unstable alarms | No alarm |
|---|---|---|---|
| **B-SK** (STFT kurtogram, baseline) | 0.084 | 6 | 2 |
| **W-MSK** (Morlet spectral kurtosis, candidate) | 0.028 | 6 | 2 |
| W-WPK (wavelet-packet kurtosis) | 0.002 | 8 | 2 |
| *Novelty family (descriptive)* | | | |
| B-FFT (FFT bands + statistics) | 0.704 | 0 | 0 |
| W-MDB (MorletDB `morlet-energy-v1`) | 0.707 | 1 | 0 |
| W-WPN (wavelet-packet energies) | 0.807 | 0 | 0 |
| *Reported* | | | |
| RMS | 0.101 | 1 | 1 |
| Wideband kurtosis | 0.110 | 5 | 0 |
| Envelope-spectrum peak ratio | 0.029 | 1 | 4 |

Per-bearing cells: `alarms.txt`. Alarm minutes after calibration:
`decision.json` → `alarm_minutes_after_calibration`.

## Reading

- **Impulsiveness indicators rarely gave stable early alarms on FEMTO**, whether
  wavelet-based or not. Most alarms were transient spikes that did not persist.
  This is consistent with what is commonly reported for these accelerated tests: degradation
  shows mainly as a late broadband rise, not as a long phase of growing
  impulsiveness.
- **Where they alarmed, they mostly agreed.** B-SK and W-MSK often alarm in the
  same snapshot (bearings 1_3, 1_4, 1_5, 1_6, 2_7). Both respond to the same
  impulsive event; the Morlet filter bank adds no earlier sensitivity.
- **Novelty family (descriptive only).** MorletDB's own features and FFT bands are
  indistinguishable:
  - Δ +0.002 [−0.216, +0.168];
  - both alarm within minutes to a few hours after calibration on most bearings;
  - this is consistent with the run-in or drift concern from development.

  Wavelet-packet energies show a higher mean (0.807). Under amendment 2 this was
  not a decision candidate, so no claim is made.
- **Simple RMS** reached a mean stable lead of 0.101 with one unstable alarm. That
  is at least as good as every impulsiveness indicator here.

## IMS harness check (development only; not evidence)

IMS failing bearings were already consumed in compression stage 1, so this run only
checks the harness on a second rig (`../ims/alarms.txt`).
- **Impulsiveness family.** The kurtogram, Morlet and wavelet-packet versions give
  identical stable leads on the failing bearings: set 1 bearing 3 at 0.16, set 2
  bearing 1 at 0.32. None alarms on any non-failing bearing.
- **Novelty family.** It raises early stable alarms on bearings that did *not*
  fail: set 1 bearings 1–2 (0.93–0.98) and set 3 bearings 1–2 (0.96–0.98). This
  confirms the drift concern behind amendment 2. A persistent novelty alarm on
  these rigs often means "different from the first 120 snapshots", not "damaged".

## What this does and does not show

- **Shows:** on this public run-to-failure set, with one shared, fixed alarm rule,
  wavelet impulsiveness indicators (Morlet CWT and wavelet-packet spectral
  kurtosis) do not detect degradation earlier than the STFT kurtogram.
- **Does not show:**
  - that wavelets never help: other rigs, defect types, or a fault phase longer
    than FEMTO's accelerated tests could differ;
  - anything about tuned per-machine detectors or the fast-kurtogram variant;
  - true detection accuracy: there is no labelled fault onset, and stable lead is
    a proxy.
- **Scope.** Eleven bearings from one rig at three conditions. With this many
  units, the CI is wide; the "close" verdict rests on its upper bound being 0.000.

## Consequence for MorletDB

Together with Paderborn H2 (not supported), the seismic timing test (temporal
Morlet below the margin; envelope cross-correlation better) and compression
(decomposition paused), this closes the wavelet question for the project:
- Morlet features remain an optional extractor;
- the product does not depend on them;
- raw-only recordings (`extractor => 'none'`) are the natural default
  candidate, which is a separate decision.

## Reproduce

```sh
cargo build --locked --release -p morlet-core --example features
# FEMTO: NASA PCoE "10. FEMTO Bearing.zip" (sha256 e21bb22b…d81f), nested zips extracted to $F
python evaluation/wavelets/w1.py indicators --role femto-dev  --femto $F --out OUT/dev
python evaluation/wavelets/w1.py alarms --out OUT/dev
python evaluation/wavelets/w1.py indicators --role femto-eval --femto $F --out OUT/eval
python evaluation/wavelets/w1.py alarms --out OUT/eval
python evaluation/wavelets/w1.py decide --out OUT/eval --candidates evaluation/wavelets/results/dev/candidates.json
```

Cite: Nectoux et al., "PRONOSTIA: An Experimental Platform for Bearings Accelerated
Life Test", IEEE PHM 2012; the NASA Prognostics Data Repository.
