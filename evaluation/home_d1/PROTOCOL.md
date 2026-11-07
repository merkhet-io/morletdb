# D1: a washing machine on a smart plug, recorded at home by an edge box without a person (draft)

Status: **DRAFT.** It is frozen after the calibration week (phase 0), before any test data exists; the values marked
*calibrate* are filled in then, in the freezing commit.

## Question

Can a Raspberry Pi running MorletDB and the steward record one household appliance for a month without anyone
looking after it? And can exact search over the stored power curves tell which wash program ran, without being
trained on it?

## Setup

- **Plug:** a Shelly Plug S Gen3, which reports power, voltage, current, mains frequency and an energy counter through
  its local RPC (`Switch.GetStatus`). It sits on its own wall socket, with no power strip. The appliance's rated power
  must be clearly below the plug's rating; if not, a dishwasher or a fridge is used instead and this protocol is
  amended before freezing.
- **Box:** the Raspberry Pi 3B+ (passively cooled, 55–65 °C normal). PostgreSQL's data directory is on an external
  disk (drive A) behind a powered USB hub. Backups go to a second disk (drive B), kept offline between backups.
- **Software:**
  - the release image with data checksums;
  - `examples/collectors/shelly` polling once per second;
  - `deploy/steward` as the sidecar.

  The image and commit are recorded at freezing.
- **Labels:** the household notes each wash as a line with day, time and program (for example "Tue 19:40, Cotton
  40, 1200 rpm"). Run boundaries are found from power alone, by a rule fixed at freezing.
- **Privacy:** the raw data stays on the box and drive B. The public repository gets only aggregate results: counts,
  rates, metrics and confusion matrices. No timestamps, daily curves or anything that shows when people are home.

## Phases

1. **Phase 0, calibration (about 7 days, not evaluated).**
   - The plug's actual update cadence and resolution.
   - The idle noise floor.
   - The threshold and minimum duration that delimit a run.
   - The search window and stride.
   - The storage hint for each channel.

   Two or three labelled runs are used only to check the pipeline.
2. **Freeze.** This file with the calibrated values, and the analysis script, both committed before test data.
3. **Phase 1, test (4 weeks; extended in 1-week steps until every evaluated program has at least 4 runs, up to
   8 weeks).** Normal household use.

## Hypotheses

- **H1, unattended capture.**
  - At least 99.5% of the test period's seconds are stored.
  - Every missing second lies in a gap logged with a cause (`gaps.jsonl`: no answer, late answer, restart).
  - No sample is stored that the plug did not report: stored samples equal the spooled readings, bitwise.
- **H2, program recognition.**
  - Each labelled run of the test period is a query; the window is the first *calibrate* samples of the run.
  - Every other test run is the gallery, and nearest-neighbour exact envelope search names the program.
  - Baseline: 1-NN on run features (energy, duration, peak power, minutes above the heating threshold).
  - PASS if MorletDB's macro F1 is within 0.10 of the baseline's. Programs with fewer than 4 runs are reported but
    not scored.
- **H3, edge operation.** The month passes without manual intervention on the box:
  - the steward's status is never *action required* for more than 1 hour;
  - its audit table explains every automatic action;
  - a monthly restore from drive B into a fresh container matches the box, checked by `morlet_verify` and a
    bitwise comparison of a random 1% of chunks.

**Descriptive:**

- storage per sample against raw JSON and float32;
- disk growth per day;
- load, temperature and throttling;
- search latency on the Pi;
- mean(v·i) from synchronous cross statistics against the plug's own power reading;
- whether H2 already holds within the first 8.5 minutes.

## What it does not show

Anything that needs the current waveform, such as turn-on transients. The plug reports aggregated values about once a
second; transient work stays with N1 and a waveform-capable meter.
