# FourCastNet 3: feasibility note (no downloads)

Checked on 2026-10-31 against official pages only. Nothing was downloaded or run. This is
background for a possible later weather track. It is **not part of F1**.

## Documented facts

| Item | Value | Source |
|---|---|---|
| Model | 710,867,670 parameters; a fully convolutional spherical neural operator | [model card](https://huggingface.co/nvidia/fourcastnet3) |
| Filter basis | local filters "parameterized by linear combinations of Morlet wavelets on a disk" | [paper](https://arxiv.org/abs/2507.12144) (via RELATED_WORK.md) |
| Licence | Apache 2.0 | model card |
| Checkpoint | 2.65 GB (version 0.1.0, July 2025) | [NGC catalog](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/earth-2/models/fourcastnet3) (search listing) |
| Platform | **Linux only**; NVIDIA Ampere, Hopper or Turing; tested on A100, H100 and L40S | model card |
| Precision | bf16 AMP recommended, float32 inputs and outputs | model card |
| Inference speed | a 60-day, 6-hourly global forecast in under 4 minutes on one GPU | model card, paper |
| GPU memory | not stated for FCN3. Earth2Studio's general table: compute capability ≥ 8.9, ≥ 40 GB, 128 GB disk | [Earth2Studio install guide](https://nvidia.github.io/earth2studio/main/userguide/about/install/) |
| Inputs | 72 variables (7 surface plus u, v, z, t and q on 13 levels), 0.25° grid of 721 × 1440, 6-hourly | model card |
| Data periods | training 1980–2015, testing 2016–2017, evaluation 2018–2019 (ERA5) | model card |
| Tooling | Makani (training), Earth2Studio (inference), torch-harmonics | model card |

## Blocker

**This machine (macOS, Apple M5 Max) cannot run FCN3.** The model card lists Linux and
NVIDIA GPUs only. The minimum practical setup is one A100, L40S or H100 with 40 GB or
more, on Linux, with about 0.5 TB of local disk.

## Cost of a minimal analogue-correction pilot (estimates, not measurements)

- **Contamination:** to avoid the documented training and evaluation years, use 2020
  onwards. ARCO-ERA5 (public, anonymous Zarr on Google Cloud) covers this. Google's
  ERA5 page says the program covers 1940 to about May 2023 and is no longer updated.
- **Design:** a hindcast archive for 2020–2021 (one run a day, about 730 runs) plus 2022
  test runs every fourth day (about 90), each to 10 days (40 steps).
- **Data:** 72 × 721 × 1440 float32 is about 300 MB per input state, so about 250 GB
  streamed in total.
- **Compute:** at about 1 s per step (derived from "60 days in under 4 minutes"), about
  10 GPU-hours for one member each, or about 100 GPU-hours for 10-member ensembles.

## Cheaper routes (not evaluated here)

- **Stored forecasts from WeatherBench 2,** mostly for the 2020 evaluation year. They
  need no GPU, but limit the archive and test design.
- **GraphCast_small (1°) or Pangu-Weather (ONNX) on CPU,** for a small pilot only. Both
  have non-commercial weight licences.
- **ECMWF AIFS Single** (CC BY 4.0), which needs a GPU.

Every route needs explicit approval for downloads and any paid compute.
