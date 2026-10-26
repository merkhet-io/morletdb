"""Multiscale coefficient representations with provenance (research module).

A `CoefficientSet` stores every DWT coefficient of one source window, level-major,
so a reader can fetch the coarse approximation alone and refine later. Raw data is
never replaced: representations are derived, rebuildable and carry a checksum of
the exact source samples. Specs are immutable; incompatible specs are refused.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import struct

import numpy as np

from . import dwt

IMPLEMENTATION_VERSION = "msmem-dwt-v1"
MAGIC = b"MSMEM1\n"


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def samples_sha256(samples: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(samples, dtype="<f8").tobytes()).hexdigest()


@dataclass(frozen=True)
class RepresentationSpec:
    family: str = "db4"
    levels: int = 5
    sample_rate_hz: float = 100.0
    units: str = "mV"
    window_samples: int = 1000
    normalization: str = "zscore-per-window"   # or "none"
    channel: str = "II"
    boundary: str = "symmetric-half-sample-to-multiple-of-2^levels"
    transform: str = "dwt-periodized-orthonormal"
    dtype: str = "float64-le"
    sparsification: str = "none"               # full coefficients are always stored
    implementation: str = IMPLEMENTATION_VERSION

    def __post_init__(self):
        dwt._filters(self.family)
        if self.levels < 1 or self.window_samples < 2 ** self.levels:
            raise ValueError("window too short for the requested levels")
        if self.normalization not in ("none", "zscore-per-window"):
            raise ValueError("normalization must be none or zscore-per-window")
        if not (np.isfinite(self.sample_rate_hz) and self.sample_rate_hz > 0):
            raise ValueError("sample rate must be positive and finite")

    @property
    def id(self) -> str:
        return hashlib.sha256(canonical(asdict(self))).hexdigest()

    @property
    def padded_samples(self) -> int:
        return dwt.padded_length(self.window_samples, self.levels)

    def shapes(self) -> list[tuple[str, int]]:
        """[("a5", n), ("d5", n), ..., ("d1", n)]: coarsest first, level-major."""
        n = self.padded_samples
        names = [(f"a{self.levels}", n >> self.levels)]
        for level in range(self.levels, 0, -1):
            names.append((f"d{level}", n >> level))
        return names

    def band_hz(self, name: str) -> tuple[float, float]:
        """Nominal frequency band of a coefficient block. Depends on the sample rate:
        equal level indices at different rates are different physical bands."""
        nyquist = self.sample_rate_hz / 2
        level = int(name[1:])
        if name[0] == "a":
            return (0.0, nyquist / 2 ** level)
        return (nyquist / 2 ** level, nyquist / 2 ** (level - 1))


@dataclass(frozen=True)
class Source:
    recording_id: str
    start_sample: int
    end_sample: int
    samples_sha256: str


@dataclass
class CoefficientSet:
    spec: RepresentationSpec
    source: Source
    blocks: dict[str, np.ndarray] = field(repr=False)
    scale: tuple[float, float] = (0.0, 1.0)   # (mean, std) removed before the transform

    # -- construction ------------------------------------------------------------
    @classmethod
    def from_samples(cls, samples, spec: RepresentationSpec, recording_id: str, start: int):
        x = np.asarray(samples, dtype=float)
        if x.ndim != 1 or len(x) != spec.window_samples or not np.isfinite(x).all():
            raise ValueError("source window must be finite, 1-D and match spec.window_samples")
        source = Source(recording_id, int(start), int(start) + len(x), samples_sha256(x))
        mean, std = 0.0, 1.0
        if spec.normalization == "zscore-per-window":
            mean = float(x.mean())
            std = float(x.std()) or 1.0
        coeffs = dwt.wavedec(dwt.extend((x - mean) / std, spec.levels), spec.family, spec.levels)
        blocks = {name: c for (name, _), c in zip(spec.shapes(), coeffs)}
        return cls(spec, source, blocks, (mean, std))

    # -- reconstruction ----------------------------------------------------------
    def reconstruct(self, keep: set[str] | None = None, denormalize: bool = True) -> np.ndarray:
        """Inverse transform keeping only `keep` blocks (all when None), cropped to
        the original window. Dropping blocks is an orthogonal projection: the error
        energy equals the energy of the dropped coefficients (on the padded signal)."""
        coeffs = []
        for name, size in self.spec.shapes():
            block = self.blocks[name]
            coeffs.append(block if keep is None or name in keep else np.zeros(size))
        y = dwt.waverec(coeffs, self.spec.family)[: self.spec.window_samples]
        if denormalize:
            y = y * self.scale[1] + self.scale[0]
        return y

    def coarse(self) -> np.ndarray:
        return self.blocks[self.spec.shapes()[0][0]]

    def refinement(self, budget: int, candidates: list[str] | None = None) -> list[tuple[str, int, float]]:
        """Deterministic refinement rule: the `budget` largest-magnitude coefficients
        among `candidates` (default: all detail blocks), ties broken by coarser level
        first, then position. This rule favours strong components; weak high-frequency
        events can be omitted from a budgeted context, but the stored set keeps them."""
        names = candidates if candidates is not None else [name for name, _ in self.spec.shapes()[1:]]
        values = np.concatenate([self.blocks[name] for name in names])
        owner = np.concatenate([np.full(len(self.blocks[n]), i) for i, n in enumerate(names)])
        position = np.concatenate([np.arange(len(self.blocks[n])) for n in names])
        # Stable sort keeps the concatenation order (coarser level, then position) for ties.
        order = np.argsort(-np.abs(values), kind="stable")[:budget]
        return [(names[owner[i]], int(position[i]), float(values[i])) for i in order]

    def reconstruct_from(self, coarse: set[str], refinement: list[tuple[str, int, float]]) -> np.ndarray:
        """Normalized-domain signal from whole `coarse` blocks plus sparse refinements."""
        coeffs = [self.blocks[name].copy() if name in coarse else np.zeros(size)
                  for name, size in self.spec.shapes()]
        index = {name: i for i, (name, _) in enumerate(self.spec.shapes())}
        for name, position, value in refinement:
            coeffs[index[name]][position] = value
        return dwt.waverec(coeffs, self.spec.family)[: self.spec.window_samples]

    # -- deterministic serialization ----------------------------------------------
    def to_bytes(self) -> bytes:
        header = {"spec": asdict(self.spec), "spec_id": self.spec.id, "source": asdict(self.source),
                  "scale": list(self.scale), "layout": [[n, s] for n, s in self.spec.shapes()]}
        head = canonical(header)
        body = b"".join(np.asarray(self.blocks[n], dtype="<f8").tobytes() for n, _ in self.spec.shapes())
        return MAGIC + struct.pack("<I", len(head)) + head + body

    @staticmethod
    def read(blob: bytes, keep: set[str] | None = None, expected_spec: RepresentationSpec | None = None):
        """Parse the header, then read only the requested level blocks.
        Returns (CoefficientSet with missing blocks zeroed, bytes_read)."""
        if not blob.startswith(MAGIC):
            raise ValueError("not a msmem coefficient blob")
        (head_len,) = struct.unpack_from("<I", blob, len(MAGIC))
        start = len(MAGIC) + 4
        header = json.loads(blob[start:start + head_len])
        spec = RepresentationSpec(**header["spec"])
        if spec.id != header["spec_id"]:
            raise ValueError("spec id does not match its contents")
        if expected_spec is not None and spec.id != expected_spec.id:
            raise ValueError("incompatible representation spec; refusing to mix")
        offset = start + head_len
        bytes_read = offset
        blocks = {}
        for name, size in spec.shapes():
            end = offset + 8 * size
            if keep is None or name in keep:
                blocks[name] = np.frombuffer(blob[offset:end], dtype="<f8").astype(float)
                bytes_read += 8 * size
            else:
                blocks[name] = np.zeros(size)
            offset = end
        return CoefficientSet(spec, Source(**header["source"]), blocks, tuple(header["scale"])), bytes_read
