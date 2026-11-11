# B6 summary (DEVELOPMENT DRY RUN: not results)

| Criterion | Holds |
|---|---|
| S1 | True |
| S2 | False |
| S3 | False |
| S4 | False |
| S5 | False |
| S6 | — |
| passes | False |

**S2:** six waveforms G21/G1 total 0.9755; smaller on 3 of 4.

## Storage (bits/sample; development series marked)

| Input | Set | G1 | G21 | G21/G1 | Changed | v5: table / exc. / per sample / none / LPC | CH best | PCO | G2 (B5) |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| pad-c-b6 | waveform | 7.819 | 7.587 | 0.9703 | 1014/1024 | 0 / 0 / 0 / 1014 / 1014 | — | — | — |
| pad-p-b6 | waveform | 9.115 | 7.736 | 0.8487 | 1024/1024 | 1024 / 0 / 0 / 0 / 1017 | — | — | — |
| femto-dev | waveform | 10.654 | 10.092 | 0.9472 | 1024/1024 | 0 / 896 / 0 / 128 / 1024 | — | — | — |
| xjtu-dev | waveform | 63.659 | 63.659 | 1.0000 | 0/984 | 0 / 0 / 0 / 0 / 0 | — | — | — |
| Blockchain-tr | elf | 13.960 | 13.960 | 1.0000 | 0/25 | 0 / 0 / 0 / 0 / 0 | 13.164 | 12.367 | 13.257 |
| POI-lat | elf | 60.841 | 60.841 | 1.0000 | 0/25 | 0 / 0 / 0 / 0 / 0 | 53.510 | 53.302 | 53.585 |
| Stocks-USA | elf | 4.562 | 4.562 | 1.0000 | 0/25 | 0 / 0 / 0 / 0 / 0 | 5.199 | 5.232 | 4.562 |
| electric_vehicle_charging | elf | 10.031 | 9.800 | 0.9770 | 1/1 | 0 / 0 / 0 / 1 / 1 | 10.455 | 10.392 | 10.031 |
| b3-vibration_1 | b3 | 9.274 | 7.821 | 0.8434 | 6570/6570 | 6570 / 0 / 0 / 0 / 6072 | — | — | — |
| b3-phase_current_1 | b3 | 10.554 | 8.627 | 0.8174 | 6570/6570 | 6570 / 0 / 0 / 0 / 6570 | — | — | — |
| b3-phase_current_2 | b3 | 10.745 | 8.607 | 0.8010 | 6570/6570 | 6570 / 0 / 0 / 0 / 6570 | — | — | — |
| b3-force | b3 | 8.670 | 7.468 | 0.8614 | 416/416 | 416 / 0 / 0 / 0 / 118 | — | — | — |
| b3-speed | b3 | 9.190 | 9.190 | 1.0000 | 0/416 | 0 / 0 / 0 / 0 / 0 | — | — | — |
| b3-torque | b3 | 48.930 | 48.930 | 1.0000 | 0/416 | 0 / 0 / 0 / 0 / 0 | — | — | — |

## Codec speed (M samples/s, single thread, best of 3)

| Input | Set | G1 enc | G1 dec | G21 enc | G21 dec | enc ratio | dec ratio | load (passes) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| pad-c-b6 | waveform | 61.1 | 132.7 | 33.4 | 103.7 | 0.547 | 0.781 | 3.03, 3.03, 3.03 |
| pad-p-b6 | waveform | 23.3 | 79.2 | 14.2 | 78.3 | 0.608 | 0.989 | 3.43, 3.43, 3.43 |
| femto-dev | waveform | 22.9 | 94.0 | 16.4 | 107.0 | 0.716 | 1.138 | 3.43, 3.43, 3.43 |
| xjtu-dev | waveform | 23.6 | 78.0 | 24.4 | 78.4 | 1.035 | 1.005 | 3.43, 3.43, 3.23 |
| Blockchain-tr | elf | 26.2 | 110.1 | 21.8 | 112.1 | 0.831 | 1.018 | 3.23, 3.23, 3.23 |
| POI-lat | elf | 20.7 | 60.8 | 21.2 | 58.0 | 1.024 | 0.955 | 3.23, 3.23, 3.23 |
| Stocks-USA | elf | 30.6 | 119.8 | 23.2 | 119.9 | 0.758 | 1.000 | 3.23, 3.23, 3.23 |
| electric_vehicle_charging | elf | 20.1 | 95.2 | 18.8 | 107.2 | 0.933 | 1.126 | 3.23, 3.23, 3.23 |
| b3-vibration_1 | b3 | 22.1 | 76.8 | 13.5 | 74.5 | 0.614 | 0.970 | 3.23, 3.29, 3.19 |
| b3-phase_current_1 | b3 | 19.6 | 76.8 | 11.4 | 52.8 | 0.582 | 0.687 | 3.19, 3.10, 2.93 |
| b3-phase_current_2 | b3 | 19.8 | 77.7 | 11.5 | 52.6 | 0.581 | 0.677 | 3.09, 2.92, 2.85 |
| b3-force | b3 | 18.3 | 72.1 | 12.3 | 81.6 | 0.670 | 1.131 | 3.02, 3.02, 3.02 |
| b3-speed | b3 | 48.4 | 105.0 | 49.3 | 104.3 | 1.019 | 0.994 | 3.02, 3.02, 3.02 |
| b3-torque | b3 | 25.0 | 86.7 | 25.3 | 114.3 | 1.011 | 1.319 | 3.02, 3.02, 3.02 |

## Platforms: linux-arm64, macos-arm64; differing inputs: none; required missing: linux-amd64

- linux-amd64: not available: rust:1.99.0-bookworm for linux/amd64 is not local (docker pull --platform linux/amd64 rust:1.99.0-bookworm needs the owner's OK)
- linux-arm64: ok: 14 inputs, rust:1.99.0-bookworm sha256:e393e07b3c3c39f238de676b0eb60c03dc7aefe5d6e37be6d29c686bf4d7e256, b6codec b773dc8d3c38dd5b0cc318f10bc52cec53b107ee72311455ec143c943ca0a67d
- macos-arm64: ok: 14 inputs, b6codec 31ac6d8adf2c1c42e504df3af5fac1353762836071996063bdcc6842a25f6e7f, Darwin arm64

## B3

Loads void or missing: none; read mismatches (D2 r1): {'g21': 0, 'rel': 0, 'q': 0}; Grizzbit 2.1 chunk format: ['{"asked": "grizzbit21", "server": "grizzbit21"}', '{"asked": "grizzbit21"}']

