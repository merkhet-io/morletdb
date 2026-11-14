# Note for #326 (outside the B7 protocol)

Followed up by study [B8](../storage_b8/RESULTS.md) (chunk format version 6), which this estimate motivated.

B7 found that MorletDB stores float32 input above float32 size on the force and ctrl channels. It upcasts to
float64, and Grizzbit finds no value grid there. A quick estimate of what a float32-aware chunk path could reach:
pcodec 1.0.4, default level, on the same float32 values in 32,768-sample chunks
(`results/f32_probe.py`, run 2026-11-14 after B7).

Bytes per sample:

| Group | pcodec f32, 32,768-sample chunks | MorletDB 0.3.13 (B7, chunk table) | ModelarDB lossless (B7) |
|---|---:|---:|---:|
| force | 2.88 | 5.20 | 3.65 (not bit-exact) |
| ctrl | 1.60 | 4.91 | 3.27 |
| pmid | 1.34 | 1.54 | 2.19 |

- **What this suggests:** a float32 chunk type (bit-exact float32, decoded to float64 on read) would make force
  and ctrl 45–67 % smaller than today and smaller than ModelarDB on every group.
- **What this is not:** pcodec is a stand-in for what is reachable, not a proposal to embed it.
- **What a change needs:** a chunk-format change, so a codec study under its own frozen protocol, golden files
  and the owner's decision.
