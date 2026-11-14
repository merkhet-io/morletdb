# Local estimate for #326 (not a study): pcodec on the float32 values of B7's benchmark set, chunked like MorletDB.
import sys
import numpy as np
sys.path.insert(0, '.')
sys.path.append('/Users/zin/sophia/morletdb/.venv-b4/lib/python3.13/site-packages')
import b7, workload as wl
import pcodec
import pcodec.standalone as pcs
man = b7.manifest()
for g in ("force", "ctrl", "pmid"):
    units = b7.group_units(g, man)
    n = b = b_whole = 0
    for _, x, _ in units.values():
        for ch, a in x.items():
            n += a.size
            for s in range(0, a.size, 32768):
                b += len(pcs.simple_compress(np.ascontiguousarray(a[s:s + 32768]), pcodec.ChunkConfig()))
            b_whole += len(pcs.simple_compress(np.ascontiguousarray(a), pcodec.ChunkConfig()))
    print(f"{g}: {n} samples; pcodec f32 per 32768-chunk {b/n:.3f} B/sample, whole series {b_whole/n:.3f}", flush=True)
