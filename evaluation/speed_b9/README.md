# Study B9: Grizzbit 2.2 speed, no format change (#394)

**Status: gates 1-5 and 7 passed; gate 6 (development dry run) done on the Pi, not yet on the M5 Max; build record (gate 8) not written; no benchmark input has been read.**

- **Protocol:** [PROTOCOL.md](PROTOCOL.md), frozen on 2026-11-16 in the commit that adds it
  (`git log --diff-filter=A -- evaluation/speed_b9/PROTOCOL.md`), on branch `perf/394-simd-lpc` on top of
  `345eb26`. The build record is its section 10.
- **Criteria:** #394's done criteria as the owner amended them on 2026-11-16.
  - Raspberry Pi 3B+: decode at least 1.3× faster.
  - Raspberry Pi 3B+ and M5 Max: no input slower.
  - Byte identity on macOS/arm64, Linux/arm64, Linux/x86_64 and the Pi.
  - Fuzzing and Kani.
- **Baseline:** `9361aba`, main with #507. **Candidate:** `8517ef1` (build record).
- **Figures in the #394 commit messages** are development measurements on development and synthetic inputs only.

## Owner decisions, 2026-11-16 (recorded here; they change no part of the protocol)

1. **H1 stays as written:** all 12 inputs, judged per input, configuration G21+6. No subset or aggregate replaces a
   failing input.
2. **Push approved:** the candidate may be pushed (draft PR #508), so the CI parts of H3(b) and H4 can run.
3. **A native x86_64 Linux host is provided** (the owner's home server, an Intel Celeron J3060, Debian 12). It was
   running a soak test (#191, planned to end 2026-11-17) and other services when B9 ran, so nothing was run on it
   by the study. The hash step for that host is prepared (`x86_host.sh`: static x86_64 musl builds of both arms, the 12
   inputs, `nice -n 19`, no Docker, files under `~/b9` only) and is left to the owner. Its hashes count towards H3(a)
   when they exist (protocol section 2, "if the owner provides a native x86_64 Linux host").

## Files

| Path | What |
|---|---|
| `b9codec/` | the harness (section 3), copied unchanged into every arm |
| `build.sh` | the builds of every arm and platform (section 3) |
| `b9.py` | input verification, the M5 Max runner (sections 6.C, 6.D) and the analysis (section 7) |
| `b9pi.py`, `pi_steps.sh` | the Raspberry Pi runner with its 1-second sampler (section 6.B), its hashes and tests |
| `hashes.sh` | H3(a) hashes per platform and arm |
| `dev_inputs.py` | the synthetic development inputs, generated as #394's development generated them (gate 6) |
| `x86_host.sh` | the owner's native x86_64 hash step |
| `results/` | section 9's results |
