# Pilot stage evidence (protocol commit 0b26f8e, clean tree)

No task-level result yet: the test fold has not been evaluated.

- `verify.json`: all 43,601 used files (21,799 records x .dat/.hea plus 3 metadata files)
  match PhysioNet's SHA256SUMS.txt.
- `prepare.json`: 21,388 records from 18,617 patients. 411 excluded (no diagnostic
  superclass) and 0 for bad signals. Folds 1-10 hold 2,129-2,158 records each.
- `retrieve.json`: exhaustive top-8 over the 17,084-record training gallery in 3.6 s; no
  same-patient neighbours. NumPy/Accelerate printed divide-by-zero/overflow warnings during
  the float32 matmul. A follow-up check found all similarities finite, the maximum
  |float32 - float64| similarity difference 1.1e-6, and the stored top-8 sets identical to
  an independent float64 exhaustive search for 2,000 sampled queries. The warnings are
  spurious.
- `contexts.json`: budgeted neighbour contexts; representation build for the gallery took
  32 s with 1.8 GB peak memory. Mean relative reconstruction error (lower is better):

  | Budget (scalars) | R raw-decimated | W fixed DWT | C coarse+refine |
  |---|---|---|---|
  | 32 | 0.796 | 0.798 | 0.529 |
  | 64 | 0.673 | 0.678 | 0.319 |
  | 128 | 0.477 | 0.484 | 0.113 |

  This is signal fidelity, not task benefit. C also transmits uint32 indices
  (C64 = 640 bytes vs 512 for R64/W64).
- `smoke-A3-seed0.json`: 2 epochs on 512 records (validation only, no test predictions)
  took 10.5 s on MPS; the model has 40,325 parameters.
