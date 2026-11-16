# Study B9: Grizzbit 2.2 speed, no format change (#394)

**Status: protocol frozen, results pending.**

- **Protocol:** [PROTOCOL.md](PROTOCOL.md), frozen on 2026-11-16 in the commit that adds it
  (`git log --diff-filter=A -- evaluation/speed_b9/PROTOCOL.md`), on branch `perf/394-simd-lpc` on top of
  `345eb26`.
- **Criteria:** #394's done criteria as the owner amended them on 2026-11-16.
  - Raspberry Pi 3B+: decode at least 1.3× faster.
  - Raspberry Pi 3B+ and M5 Max: no input slower.
  - Byte identity on macOS/arm64, Linux/arm64, Linux/x86_64 and the Pi.
  - Fuzzing and Kani.
- **Baseline:** `9361aba`, main with #507.
- **Candidate:** the commit named in the build record, not yet written.
- **Measurements:** none yet. No benchmark input has been read by any build of the #394 work. Figures in the
  #394 commit messages are development measurements on development and synthetic inputs only.
- **Results:** `RESULTS.md` and `results/` follow the run, which starts only after the owner has reviewed the
  protocol and the build record has been committed.
