# C7 results: a pinned healthy baseline on XJTU-SY run-to-failure bearings

Run once on 2026-10-29, as frozen in `PROTOCOL.md`, with the three logged changes after freeze.
The verdicts were committed (5ac447a) before the post-hoc check and the exploratory Paderborn run.

**Short version.** By the frozen rule, Q1 is **inconclusive** and Q2 is **supported**. Q2's support
does not mean what the protocol hoped. It comes from two bearings whose pinned alarms were raised by
vibration **falling** after run-in, not rising toward failure. Counting only rising alarms (post
hoc), the pinned baseline is no earlier than a plain RMS alarm. Absorption, the C5 failure that C7
was built to fix, barely happens on XJTU-SY's horizontal channel. On C5's B09 (consumed data,
exploratory) the pinned baseline does fix it.

## Data

- `XJTU-SY_Bearing_Datasets.part01.rar` to `part06.rar` from the official Google Drive mirror,
  4,444,600,440 bytes, exactly the sizes in change after freeze 3. SHA-256 in
  `results/SHA256SUMS`.
- Extracted with `unar` 1.10.7 (Homebrew). Its integrity test passed for all 9,236 entries.
- Snapshot counts for all 15 bearings match Table 2 of the dataset's own
  `Introduction_to_XJTU-SY_Bearing_Dataset.pdf`. Every bearing's files are `1.csv` to `N.csv`
  with 32,768 × 2 samples.
- The PDF settles two points the protocol left open:
  - condition 2 is 2250 rpm, as the official page said;
  - a test stopped when the horizontal **or** vertical amplitude exceeded 10 × its normal-stage
    maximum.
- Outputs: `results/c7.json`, `results/sql_check.json`, `results/posthoc_direction.json` and
  `results/c7_paderborn.json`. The data and values stay in `data/xjtu-sy/` (not committed).

## Product check

On pg_morlet 0.3.1, the pinned score comes from
`morlet_compare_summary(..., metadata_filter => '{"c7_pin": true}', before_only => false,
min_history => 5)`. It matched the harness exactly (relative error 0.0) on Bearing1_1, 2_1 and
3_1, five queries each:

- pinned snapshots 0 and 29 had 29 history recordings (the other pinned ones);
- snapshot 30, the middle one and the last one had 30.

## Verdicts (horizontal channel, 15 bearings)

| Question | Δ stable lead | 95% CI | Unstable alarms | Verdict |
|---|---|---|---|---|
| **Q1** H-PIN vs H-ALL | **+0.049** | [−0.059, 0.196] | 0 vs 0 | **inconclusive** |
| **Q2** H-PIN vs B-RMS | **+0.100** | [0.005, 0.242] | 0 vs 0 | **supported** |

Mean stable lead: H-PIN 0.56, H-ALL 0.51, B-RMS 0.46.

## Per bearing (stable lead; − = no alarm; arrow = direction of the value behind the alarm)

| Bearing | Life (min) | H-PIN | H-ALL | B-RMS | H-GATED |
|---|---|---|---|---|---|
| 1_1 | 122 | 0.45 ↑ | 0.49 ↑ | 0.46 ↑ | 0.45 |
| 1_2 | 160 | 0.88 ↑ | 0.96 ↑ | 0.92 ↑ | 0.87 |
| 1_3 | 157 | 0.76 ↑ | 0.79 ↑ | 0.66 ↑ | 0.76 |
| 1_4 | 121 | 0.43 ↑ | 0.40 ↑ | 0.40 ↑ | 0.43 |
| 1_5 | 51 | 0.68 ↑ | 0.88 ↑ | 0.68 ↑ | 0.68 |
| 2_1 | 490 | 0.08 ↑ | 0.08 ↑ | 0.08 ↑ | 0.08 |
| 2_2 | 160 | 0.89 ↑ | 0.89 ↑ | 0.85 ↑ | 0.87 |
| 2_3 | 532 | 0.80 ↑ | 0.81 ↑ | 0.80 ↑ | 0.80 |
| 2_4 | 41 | 0.75 ↑ | 0.86 ↑ | 0.75 ↑ | 0.75 |
| 2_5 | 338 | 0.65 ↑ | 0.71 ↑ | 0.60 ↑ | 0.70 |
| 3_1 | 2537 | 0.06 ↑ | 0.08 ↑ | 0.06 ↑ | 0.06 |
| 3_2 | 2495 | 0.51 ↑ | 0.62 ↓ | 0.51 ↑ | 0.35 |
| **3_3** | 370 | **0.51 ↓** | 0.08 ↑ | 0.08 ↑ | 0.08 |
| **3_4** | 1514 | **0.98 ↓** | 0.06 ↑ | 0.06 ↑ | 0.06 |
| 3_5 | 113 | − | − | − | − |

Q2's Δ is almost all in 3_3 (+0.43) and 3_4 (+0.92). On the other 13 bearings its mean is +0.013.

## Post hoc: alarm direction (decides nothing)

The score is two-sided: |x − median| / |median|. So a pinned baseline taken during run-in alarms
when the bearing *settles*. That is the risk the protocol listed under "The pin may not be
healthy". `posthoc_direction.py` records, at each alarm, whether the value behind it was above or
below its reference (median of the first 30 snapshots on bearings 3_3 and 3_4 below):

- **3_4:** the pinned alarm fires at 4% of a 25 h life on **crest**, 5.84 against a pinned median
  of 7.27. Over the first 5 h, std fell 0.56 → 0.48, crest 7.3 → 5.3 and kurtosis 4.2 → 3.2.
  Vibration only rose in the last 6% of life.
- **3_3:** the pinned alarm fires at 53% of life on **std**, 0.52 against 0.63. Std fell from 0.63
  to about 0.53 over the first five hours and rose only from about 90% of life.
- Every B-RMS alarm is a rise. H-PIN has 2 falling alarms; H-ALL has 1 (3_2, on crest).

Counting alarms raised by a fall as no alarm (lead 0):

| Comparison | Δ | 95% CI | Rule would give |
|---|---|---|---|
| H-PIN vs H-ALL | −0.009 | [−0.068, 0.075] | inconclusive |
| H-PIN vs B-RMS | +0.001 | [−0.021, 0.023] | not supported |
| H-PIN vs H-ALL, no crest | −0.000 | [−0.044, 0.065] | inconclusive |
| H-PIN vs B-RMS, no crest | −0.010 | [−0.143, 0.100] | inconclusive |

The frozen verdict stays "supported", because that is what the rule returns. Read with this check,
though, **the pinned baseline did not warn of degradation earlier than RMS on XJTU-SY**. Its extra
lead is an alarm on run-in settling. On a real machine that is a false alarm, or at best a
"conditions changed" notice.

## Reported analyses

- **Absorption** (share of snapshots after a stable B-RMS alarm that are above threshold, 14
  bearings):
  - horizontal: H-PIN 0.98, H-ALL 0.96, H-GATED 0.97;
  - vertical: H-PIN 0.93, H-ALL 0.85.
  - H-ALL has **no unstable horizontal alarm**: degradation on XJTU-SY is short and steep, so the
    history has no time to absorb it. The worst cases are 1_4 (0.68) and 2_3 (0.89). The C5
    problem that motivated C7 hardly occurs on this rig's horizontal channel.
- **Without crest:**
  - by the rule, Q1 +0.179 [0.015, 0.385] and Q2 +0.236 [0.083, 0.414] are both "supported";
  - but 4 of H-PIN's no-crest alarms are falls (2_1 at 10% of life, 2_3 at 6%, 3_3 at 32%, 3_4
    at 2%), and counted as no alarm the advantage disappears (table above);
  - one early no-crest alarm is a genuine rise: 3_2 at 7% of life, std +29% against the pin. RMS
    alarmed there at 49%.
- **Alarms in the last 10% of life:** H-PIN 2, H-ALL 4, B-RMS 4, H-GATED 4.
- **H-GATED** (reported): vs H-ALL Δ −0.050 [−0.095, −0.014]; vs B-RMS Δ +0.001
  [−0.029, 0.028]. The gated refresh brings no benefit here.
- **Vertical channel:**
  - Q1 +0.019 [−0.060, 0.118] and Q2 +0.028 [−0.070, 0.144], both inconclusive;
  - unstable alarms: H-PIN 2 (3_1, 3_4), H-ALL 3, B-RMS 0;
  - H-PIN alarms at 6% of life on 3_2 (crest) and 8% on 3_1 (unstable).
- **3_5:** no arm alarms on the horizontal channel in its 113-minute life. On the vertical channel
  H-PIN alarms at 65% of life and B-RMS at 84%; H-ALL does not alarm.

## Exploratory: Paderborn run-to-failure (consumed, decides nothing)

The pin is the first 150 measurements, restricted to the query's context and scored with ≥ 5.
The rest is C5's harness and alarm rule (`results/c7_paderborn.json`).

| Comparison | Δ | 95% CI | Unstable |
|---|---|---|---|
| H-PIN vs H-CTX | +0.071 | [−0.000, 0.207] | 1 vs 2 |
| H-PIN vs B-RMS | +0.024 | [0.003, 0.059] | 1 vs 0 |

- **B09:** H-PIN 0.94, stable; H-CTX unstable (0); B-RMS 0.92. The pinned baseline keeps
  flagging the lasting rise that the all-history score absorbed, which is what C7 was designed
  for.
- **B14:** H-PIN 0.06; H-CTX unstable; B-RMS 0.01.
- **B12:** H-PIN raises an unstable alarm; the other arms raise none.
- Alarm direction was not checked on Paderborn.

## What this means for the product

- The **pinned-baseline pattern** (metadata tag plus `metadata_filter`) works with pg_morlet 0.3.1
  as it is. It reproduced the analysis exactly and fixed absorption on the one bearing where
  absorption occurred (B09).
- **It did not give earlier warning than RMS.** On XJTU-SY its only gains came from alarms on
  vibration *falling* after run-in.
  - If a pinned baseline is ever documented, it needs two caveats: pin after run-in, and read
    the score's direction. The second may justify a signed or one-sided score option.
  - Neither was tested here. Either would need its own frozen protocol and unused data.
- No new product code is justified by C7. A first-class baseline option is **not** supported.

## Limitations

The protocol's limitations apply in full: 15 bearings, one rig, accelerated life, an
amplitude-defined end of life, and no labelled onset. In addition:

- the direction analysis was designed after seeing the results;
- XJTU-SY is now consumed.
