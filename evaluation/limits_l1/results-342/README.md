# Sample rates up to 100 MHz (#342): L1's rate points at MHz rates

Development runs, not a frozen study. Run on 2026-11-09 on the owner's Mac, load average 3.4–7.9, with the
unchanged L1 harness (`l1.py`). Its rate list was extended by 2.5, 3.125, 10 and 100 MHz and by 100,000,001 Hz,
just above the new limit. Image: built from branch `feat/342-mhz-storage`. "Before":
`ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1`.

Each point:
- one recording of 1,048,576 ADC-grid samples with bursts, without feature extraction (`extractor 'none'`), stored
  in durable batches;
- read back and compared by SHA-256;
- 1,002 derived times checked exactly against fractions, in both directions.

| Rate | Times exact | Read back | Ingest (samples/s) | Bits per sample |
|---|---|---|---:|---:|
| 64 kHz (reference) | 1,002 / 1,002 | identical | 7,182,027 | 11.75 |
| 1 MHz | 1,002 / 1,002 | identical | 7,084,973 | 11.75 |
| 2.5 MHz | 1,002 / 1,002 | identical | 6,808,935 | 11.75 |
| 3.125 MHz | 1,002 / 1,002 | identical | 7,037,423 | 11.75 |
| 10 MHz | 1,002 / 1,002 | identical | 6,853,438 | 11.75 |
| 100 MHz | 1,002 / 1,002 | identical | 6,636,557 | 11.75 |
| 100,000,001 Hz | refused: `sample_rate_hz must be finite and in (0,100000000]` | | | |
| 1 MHz on 0.3.10-alpha.1 | refused: `sample_rate_hz must be finite and in (0,200000]` | | | |

**Reading:** storage, reads, statistics and time conversion do not depend on the rate. The rate decides only how many
samples per second must be stored. One writer stores about 7 M samples/s on this machine, which is enough for a
continuous channel up to a few MHz. Continuous 10 MHz needs parallel writers (L1: 15.1 M/s with 4, 21.0 M/s with 16)
or triggered recording, as acoustic emission systems usually do. Search features keep their domains
(`morlet-energy-v1` 128 Hz–200 kHz, low-rate 1–128 Hz). Search at MHz rates is a separate step.
