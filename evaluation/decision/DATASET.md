# Candidate dataset: Paderborn KAt bearing data (verification record)

Checked 2026-10-26 against the provider's pages and paper. No data has been downloaded.
**UNVERIFIED** marks facts that no primary source states; they must be checked in the
files.

## Source and access

- Provider: KAt, Paderborn University. Bearing DataCenter:
  <https://mb.uni-paderborn.de/kat/forschung/bearing-datacenter/>
- Paper: Lessmeier et al., "Condition Monitoring of Bearing Damage in Electromechanical
  Drive Systems by Using Motor Current Signals of Electric Motors: A Benchmark Data Set
  for Data-Driven Classification", PHM Society European Conference 2016.
- **Licence: CC BY-NC 4.0.** Non-commercial academic use is allowed with a citation; the
  provider says to contact the author for commercial use. **Open: does our use count as
  commercial?** (Question 1 below.)
- Download: one directory of per-bearing RAR archives at
  `https://groups.uni-paderborn.de/kat/BearingDataCenter/`. **The host refused connections
  on 2026-10-26.** A Wayback listing of 2024-12-01 shows 32 archives of 152–178 MB
  (about 5.1 GB) and `readme_versions.txt`, whose one entry is the 2016-07-13 KI03
  fact-sheet correction.
- **No checksums are published.** We record SHA-256 hashes of every archive and every
  extracted file on first download, and the evaluation refuses mismatches afterwards.

## Content

- 32 physically distinct 6203 ball bearings (FAG, MTK, IBU), all with 8 rolling elements.
  - 6 are healthy, with differing run-in: K001–K006.
  - 12 have artificial damage: KA01, KA03, KA05–KA09 (outer ring); KI01, KI03, KI05,
    KI07, KI08 (inner ring). The methods are EDM, electric engraving and drilling, at
    extent levels 1–2.
  - 14 have real damage from accelerated lifetime tests on a separate rig, then remounted:
    KA04, KA15, KA16, KA22, KA30 (outer); KI04, KI14, KI16, KI17, KI18, KI21 (inner);
    KB23, KB24, KB27 (combined inner and outer). These are pitting or indentations at
    extent levels 1–3.
  - **The KA/KI prefix does not tell artificial from real damage.** Classify origin from
    paper Tables 4 and 5.
- 4 operating settings, one factor at a time:

  | Setting | Speed (rpm) | Load torque (Nm) | Radial force (N) |
  |---|---|---|---|
  | N15_M07_F10 | 1500 | 0.7 | 1000 |
  | N09_M07_F10 | 900 | 0.7 | 1000 |
  | N15_M01_F10 | 1500 | 0.1 | 1000 |
  | N15_M07_F04 | 1500 | 0.7 | 400 |

  Temperature is about 45–50 °C throughout.
- 20 measurements of 4 s each per bearing and setting: 80 `.mat` files per bearing,
  named like `N15_M07_F10_KA01_1.mat`.
- Channels:
  - housing acceleration (PCB 336C04 with a charge amplifier, 30 kHz low-pass) at 64 kHz;
  - 2 motor phase currents at 64 kHz;
  - radial force, load torque, speed and oil temperature at "lower sampling rates".
  - **No units are documented for any channel** (UNVERIFIED). Neither are the auxiliary
    rates or the `.mat` structure. Inspect one file before freezing.

## Independence

- **The independent unit is the bearing.** The specimens are physically distinct, but all
  were measured on **one test rig** with one motor, inverter and sensor set, so results
  cannot generalize to other machines.
- The real-damage bearings shared shafts (4 per shaft) on the lifetime rig. Whether two of
  the 14 shared a shaft is undocumented (question 6).
- Measurement dates and order are undocumented. Session or remount effects could
  correlate a bearing's files across settings.
- The paper reports that models trained on artificial damage behave differently on real
  damage, so the two origins are reported separately.

## Alternatives considered

| Dataset | Why not primary |
|---|---|
| IMS / NASA run-to-failure | Essentially one operating condition; about 12 bearings on shared shafts within 3 runs |
| XJTU-SY | 15 run-to-failure bearings, but each bearing runs under one condition only (condition confounded with group); no licence named |
| CWRU | Already used; too easy (every method ties); no independent bearings across loads |

## Decisions (user, 2026-10-26)

1. **Licence:** non-commercial research use, so CC BY-NC 4.0 applies as published, with
   attribution to Lessmeier et al. (2016) and KAt, Paderborn University.
2. **Source:** the official host is still unreachable, and the user has no copy. We use the
   third-party Zenodo upload <https://zenodo.org/records/15845309> ("Paderborn Bearing
   Dataset", uploaded 2025-07-09 by J. van den Hoogen).
   - The upload is labelled CC BY 4.0, but a re-uploader cannot relicense the data, so
     the provider's CC BY-NC 4.0 still governs.
   - It holds **31 of 32 archives; KA08 is missing.**
   - All 31 archive sizes match the official 2024-12-01 listing (Wayback) within its
     rounding. That corroborates the files; it does not prove them identical.
   - Zenodo MD5s are checked on download, and our SHA-256s are recorded in
     `SHA256SUMS.archives`. Retry the official host before publishing results, and note
     any differences.
3. **Publishing:** reports include per-query match lists (file, window, distance, labels),
   per-bearing metrics and a failure gallery with short waveform and spectrum plots. They
   are derived data under CC BY-NC 4.0 with attribution. Raw recordings and bulk
   feature dumps are not redistributed.
