# Compression, progressive access and retention benchmark (stage 1)

| File | Contents |
|---|---|
| [STEERING.md](STEERING.md) | Rewritten steering prompt: hypotheses H1–H4, H6 (H5 deferred), gates, minimum effect |
| [PROTOCOL.md](PROTOCOL.md) | Frozen stage-1 protocol (datasets, units, codecs, detectors, operating-point rules, decisions) |
| [results/ab70819/RESULTS.md](results/ab70819/RESULTS.md) | **Results, gate decisions and recommendations** |
| `blockcodecs.py` | Lossless and error-bounded block codecs (zstd, FLAC, 5/3 lifting, quantization, DWT, harmonic/TSA predictors, SZ3, zfp, SPERR) |
| `datasets.py`, `prepare_ims.py`, `fetch_cx.py` | Paderborn, IMS and IPOC CX loaders (integer ADC counts, independent units) |
| `fidelity.py` | Waveform, spectral, envelope and named-detector (D-KURT, D-ENV) metrics |
| `bench.py`, `h2.py`, `h3.py`, `h6.py`, `costs.py`, `report.py` | Rate–distortion, diagnostics, search cost, retention, speed, aggregation |
| `test_codecs.py` | Round-trip controls: lossless exactness, empirical error bounds, partial decode |

## Reproduce

```sh
uv venv --python 3.12 .venv-compress
uv pip install --python .venv-compress/bin/python numpy==2.2.6 scipy==1.16.3 pyarrow zstandard h5py hdf5plugin zfpy soundfile PyWavelets pandas matplotlib
# Data (git-ignored): Paderborn (see ../decision/DATASET.md), IMS from
#   https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip (outer zip sha256 21001ac2…),
#   CX via fetch_cx.py (ObsPy environment). Then prepare_ims.py.
O=data/compress-runs/<commit>
for d in ims cx paderborn; do python evaluation/compression/bench.py rd --dataset $d --role dev --out $O; done
python evaluation/compression/bench.py operating --out $O
for d in ims cx paderborn; do python evaluation/compression/bench.py rd --dataset $d --role heldout --out $O; done
python evaluation/compression/h2.py paderborn --out $O && python evaluation/compression/h2.py ims --out $O
python evaluation/compression/h3.py --timing-run data/timing-runs/012bb3d --out $O
python evaluation/compression/h6.py catalog --out $O   # ObsPy environment
python evaluation/compression/h6.py run --out $O && python evaluation/compression/costs.py --out $O
python evaluation/compression/report.py --out $O
python -m unittest discover -s evaluation/compression -p 'test_*.py'
```

Set `OMP_NUM_THREADS=1` (and the Accelerate/OpenBLAS equivalents) when running the
parallel stages. Acknowledge IMS/NASA (Lee, Qiu, Yu, Lin and Rexnord, 2007), Paderborn
KAt (Lessmeier et al., 2016) and IPOC (doi:10.14470/PK615318) when reporting results.
