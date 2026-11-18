# B6 reference results (published, not re-run)

B6's G1 must match B4's and B5's Grizzbit 1 exactly, and B5's Grizzbit 2 (G2) is reported beside G21 (PROTOCOL.md
section 3). B4's results are in this branch (`evaluation/storage_b4/results/b4_final.json`). B5's results live on its
branch, so they are copied here unchanged:

| File | Source | sha256 |
|---|---|---|
| `b5_storage.json` | `evaluation/storage_b5/results/b5_storage.json` on `bench/b5-grizzbit2` at `1fafdbb19d0030cfd0bf2876398bd8249db0f81b` | `6592c0c167fb1d129da9f7ed44f0c98358dc729cb3b84ba826dbe9d487d74a10` |
| `b5_speed.json` | `evaluation/storage_b5/results/b5_speed.json`, same commit | `1cc88571c443c0a49ad9028336c77f7b7cb09095a4bb979e32b42a3721342253` |

`measure_b6.py` checks these hashes before using the files.
