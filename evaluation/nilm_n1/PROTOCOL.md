# N1: can exact envelope search name an appliance from its turn-on transient? (PLAID, leave one location out)

Status: **FROZEN** in the commit that adds this file. Issue #293.

**Result (2026-11-07, [RESULTS.md](RESULTS.md)):** H1 PASS (Δ macro F1 −0.008 [−0.069, 0.053]), H2 FAIL
(AUROC 0.326), H3 PASS (Pi 3B+ bit for bit identical). The protocol below is unchanged.

- **Inspected so far:**
  - `metadata_submetered.json`, in full: types, statuses, locations, lengths and sampling rates.
  - The largest absolute value of each column in 25 randomly chosen CSV files (seed 1), to establish
    the column order.

  No onset, window, envelope, feature or distance has been computed on any record.
- **Not untouched:** PLAID is widely used, and published results on it (see "Context") shaped the design.
  Nothing in this repository has been run on it before.

This is **event identification with a known event**: each record contains one turn-on, which is detected
by a fixed rule. It is not disaggregation of aggregate signals, not event detection in continuous data,
and not identification of overlapping events.

## Data (verified, see `fetch_plaid.py`)

- **Source:** PLAID 2018, figshare 10.6084/m9.figshare.10084619.v2, licence **CC BY 4.0**. The files are
  `metadata_submetered.json` and `submetered.zip`. Both figshare MD5s match, and the SHA-256s are pinned
  in `fetch_plaid.py`:

  | File | SHA-256 |
  |---|---|
  | metadata_submetered.json | `f639453a…f768` |
  | submetered.zip | `7950ae28…2c95` |

  No waveform is committed to this repository.
- **Records:** 1876 CSV files `submetered_new/<id>.csv`, two columns per row: **current (A), then voltage
  (V)**. The order was established from magnitudes: the voltage column reaches about 170 V, the US 120 V
  RMS peak. Records are sampled at 30 kHz, on 60 Hz mains, so a cycle is 500 samples. Lengths run from
  0.7 s to about 15 s.
- **Correction to the dataset's own description:** it says 65 locations and 17 types; the metadata
  holds **56 locations and 16 types**.
- **Primary set:** 1341 records. These are records with status `off-on` whose type occurs at 2 or more
  locations among the `off-on` records, giving 11 types over 56 locations:

  | Type | Records | Locations |
  |---|---:|---:|
  | Compact Fluorescent Lamp | 230 | 33 |
  | Laptop | 201 | 35 |
  | Incandescent Light Bulb | 157 | 28 |
  | Hairdryer | 154 | 32 |
  | Fan | 125 | 24 |
  | Microwave | 110 | 23 |
  | Fridge | 98 | 25 |
  | Vacuum | 83 | 14 |
  | Washing Machine | 75 | 16 |
  | Air Conditioner | 63 | 15 |
  | Heater | 45 | 8 |
- **Unknown set:** 52 `off-on` records of the 5 types found at a single location, all at `CMU lab`:
  Coffee maker (10), Water kettle (10), Hair Iron (10), Soldering Iron (20) and Blender (2). These are used
  only as queries for H2.
- **Excluded:** the 483 records whose status is not `off-on`. They are steady-state operating modes
  (`high`, `low`, `highcool`, …) without a turn-on.
- **Known quirks:**
  - The metadata notes that some instances are not well calibrated.
  - The current clamps capture up to about 5 kHz.

  Both are reported and neither is corrected.

## Event window (fixed now, the same for every method)

1. **Per-cycle current RMS** over consecutive 500-sample blocks from sample 0.
2. **Baseline** b = median of the first 3 cycles; **peak** m = the largest cycle RMS.
3. **Onset cycle** c = the first cycle whose RMS exceeds b + 0.1·(m − b).
4. **Window:** 16,384 samples (0.546 s, 32.8 cycles) starting at max(0, min(500·c − 5000, length −
   16384)). That is 10 cycles before the onset, and about 22 after it, as the literature recommends
   (10 before, 20 after).
5. **No onset:** if m − b < 0.05 A, the record has no clear transient. It is kept with its window at
   sample 0 and flagged. The number of flagged records is reported per type.

## Methods

**M: MorletDB exact envelope search (the product, unchanged).**
- **Storage:** one recording per record holding the current of its event window. Channel `i`, unit `A`,
  30,000 Hz. The values are the CSV's numbers parsed to float64 (correctly rounded), stored bitwise
  losslessly, with `sample_quantum` 0.001 as a storage hint (see Amendment 1). The harness reads every
  record back and asserts bitwise equality. Metadata carries type and location.
- **Envelopes:** `morlet_build_envelopes(collection, recording, 16384, 16384)`, one window per record.
- **Query:** the current of the query's event window through
  `morlet_search_envelopes(collection, samples, 30000, 'i', 'A', '{}', top_k, 0.075)`. That is the
  default lag of ±7.5% (±1,229 samples, about 2.5 cycles). Exact path, screened, as shipped.
- **Leaving a location out:**
  - `top_k` = (the number of primary records at the query's location) + 5;
  - rows from the query's location are dropped, and the first k remaining rows are used.

  This equals a search over the other locations only, because the distance of one window does not
  depend on any other window.
- **Representation:** z-scored amplitude envelopes, so **M ignores absolute current level**. That is the
  product as it is, and part of what is tested.

**B1: current features + 1-NN (our baseline, the same information as M).** These are computed on the
event window: steady part = its last 10 cycles (5,000 samples), with an FFT over those 10 cycles, so
harmonic h is at bin 10·h.
- log₁₀ of the steady RMS current;
- crest factor (peak / RMS) of the steady part;
- |I_h| / |I_1| for h = 2, 3, 5, 7, 9;
- inrush ratio: the largest cycle RMS in the window divided by the steady RMS.

That gives 8 features. They are standardised with the mean and standard deviation of the gallery (the
other locations) and compared by Euclidean distance.

**B2: B1 + voltage (descriptive).** B2 adds:
- log₁₀ |P| (mean of v·i over the steady part);
- power factor P / (V_rms · I_rms);
- cos and sin of the phase of I_1 relative to V_1.

**For every method:**
- **Gallery:** the primary records at every location other than the query's.
- **Answer:** the type of the nearest gallery record (k = 1).
- **Ties:** broken by (distance, record id), the order MorletDB returns. For k = 5, the majority type
  wins, and a tie goes to the tied type with the nearest member.

## Splits

- **Leave one location out:** 56 folds. Every primary record is a query exactly once, in the fold of its
  own location.
- **Isolation:** no record from the query's location is ever in the gallery, its feature scaling or its
  answer. This is asserted in the harness.

## Primary metric and hypotheses

- **Primary metric:** macro F1 over the 11 types, from all 1341 predictions pooled. **Accuracy** is
  reported alongside it.
- **Uncertainty:** a 95% bootstrap over locations (2,000 resamples of the 56 locations with replacement,
  seed 7, the 2.5th and 97.5th percentiles). Applied to macro F1 and to the difference M − B1.

**H1 (identification).** Δ = macro F1(M) − macro F1(B1).
- **PASS** if Δ ≥ −0.05 and the CI lower bound of Δ is ≥ −0.10.
- **FAIL** if the CI upper bound of Δ is < −0.05.
- **INCONCLUSIVE** otherwise.

**H2 (unknown appliances).**
- **Queries:** the 52 unknown records (with the `CMU lab` location left out, as for every query) and the
  1341 primary records.
- **Score:** M's nearest distance.
- **PASS** if the AUROC (unknown = positive) is ≥ 0.80. A 95% bootstrap over records (2,000, seed 7) is
  reported.
- **Limitation stated now:** all unknowns come from one location and 5 types, so this is a first check,
  not a calibration.

**H3 (edge).** M runs on the Raspberry Pi 3B+ (the X2 machine) and on the desktop. **PASS** if every one
of the 1393 queries returns the same recording ids, in the same order, with bitwise-equal distances.
Median and p95 latency are reported for both machines, with the load average.

**What the results decide.**
- **H1 PASS:** the envelope representation is a credible NILM retrieval method. The next step is the V–I
  arm on synchronous channels (#292).
- **H1 FAIL:** MorletDB is positioned for NILM as the lossless archive and exact retrieval back-end for
  features such as B1 or learned embeddings, not as the classifier. The website says so.

Either way the result is published in `RESULTS.md`.

## Reported descriptively (decides nothing)

- B2, the k = 5 variants, per-type F1, the confusion matrices and the onset-flag counts.
- **Storage:** MorletDB bytes per record for the chunk and the envelope, against raw float32 and CSV
  bytes.
- **Published numbers for context only,** because their protocols differ from this one:
  - De Baets et al. 2018: V–I images with a CNN, macro F1 77.6% leave one house out (PLAID 2017).
  - 1-NN on current features: 75.98% accuracy leave one house out (review table, arXiv 2102.04808).
  - Faustine & Pereira 2020: 88.53% macro F1, split to be checked.

## Not in this task

- No voltage in M (that needs #292).
- No aggregated PLAID records.
- No learned models, no parameter search for M or B1 (every value above is fixed).
- No WHITED or COOLL (secondary, after this result).
- No industrial data.

## Amendment 1 (2026-11-07, before any result)

**Found:** the first run of `n1.py windows` stopped on record 1, as this protocol required, because its
values are not multiples of 0.01. Counting decimals over all 1876 files (a format check, no signal
statistic):
- **Current:** mostly 2–3 decimals (178M values), 0–1 decimals (15M), and about 2.8M values with 6–19
  decimals, some of them float-formatting artifacts.
- **Voltage:** similar.

**Changed:**
- **No fixed grid.** The windows hold the parsed float64 values.
- **Storage.** MorletDB stores them bitwise losslessly whatever the quantum (every chunk kind decodes
  bitwise to its input; the quantum is a storage hint only), with `sample_quantum` 0.001.
- **Read-back.** The harness asserts bitwise read-back for every record.
- **Same numbers on the Pi.** The windows file (float64) is what the Pi receives, so both machines search
  identical numbers.

**Unchanged:** methods, windows, splits, metrics and hypotheses. The storage figures now include any
extra cost of the off-grid values.

## Files

- `fetch_plaid.py`: download, hash checks and extraction.
- **To follow, before any result:** the harness (`n1.py`: import, windows, M, B1, B2, folds, metrics)
  with its isolation, onset and tie tests, committed before it is run on the full data.
- `RESULTS.md` and `results/` after the run.
