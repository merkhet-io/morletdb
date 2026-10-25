#!/usr/bin/env python3
"""Offline wave-cell posting-index PoC; Rust owns extraction, never Python.

No PostgreSQL schema/API, durability, RLS or supported-capacity claim is made.
"""
import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import platform
import struct
import subprocess
import time
from types import MappingProxyType


VERSION = 'morlet-cell-postings-poc-v1'
MAX_WINDOWS = 4096
MAX_ENTRIES = 1_000_000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Spec:
    window: int = 2048
    rate: float = 4096.0
    channel: str = 'vibration'
    unit: str = 'g'
    normalization: str = 'zscore'
    bin_width: float = 0.25

    def __post_init__(self):
        if (type(self.window) is not int or not 512 <= self.window <= 4096
                or not math.isfinite(self.rate) or not 128 <= self.rate <= 200000
                or not math.isfinite(self.bin_width) or not 0.001 <= self.bin_width <= 10
                or self.normalization not in ('none', 'demean', 'zscore')
                or not isinstance(self.channel, str) or not self.channel
                or not isinstance(self.unit, str) or not self.unit):
            raise ValueError('invalid representation specification')

    @property
    def id(self):
        # Quantizer, score, layout and normalization are frozen in this namespace.
        return digest({'version': VERSION, 'extractor': 'morlet-energy-v1',
                       'layout': 'valid-128-coefficient-cells',
                       'metric': 'equal-band-duration-weighted-log-energy-rmse-v1',
                       **asdict(self)})


@dataclass(frozen=True)
class Window:
    recording: str
    start: int
    spec_id: str
    layout: tuple  # (band, local center-start, local center-end, support-radius)
    values: tuple  # log1p energy; not signed or invertible coefficients
    pooled: tuple
    source_sha256: str
    metadata: tuple = ()  # sorted string key/value pairs; immutable cohort metadata

    def __post_init__(self):
        for name in ('values', 'pooled', 'metadata', 'layout'):
            values = getattr(self, name)
            if name in ('layout', 'metadata'):
                values = (tuple(v) for v in values)
            object.__setattr__(self, name, tuple(values))


def extract_windows(rows, spec, binary):
    """Rows are explicitly selected frozen windows, not an acquisition connector."""
    if not rows or len(rows) > MAX_WINDOWS:
        raise ValueError('window count outside 1..4096')
    payload = []
    for row in rows:
        samples = row['samples']
        if (len(samples) != spec.window or any(not math.isfinite(x) or abs(x) > 1e9
                                              for x in samples)):
            raise ValueError('invalid source window')
        payload.append(json.dumps({'samples': samples, 'sample_rate_hz': spec.rate,
                                   'normalization': spec.normalization}, allow_nan=False))
    result = subprocess.run([str(Path(binary).resolve())], input='\n'.join(payload)+'\n',
                            text=True, capture_output=True, check=True, timeout=180)
    features = [json.loads(line) for line in result.stdout.splitlines()]
    if len(features) != len(rows):
        raise ValueError('extractor output count mismatch')
    windows = []
    for row, feature in zip(rows, features):
        if feature['version'] != 'morlet-energy-v1':
            raise ValueError('extractor version mismatch')
        cells = feature['energy_cells_experimental']
        windows.append(Window(
            row['id'], row.get('start', 0), spec.id,
            tuple((c['band'], c['start_sample'], c['end_sample'], c['support_radius'])
                  for c in cells),
            tuple(math.log1p(c['energy']) for c in cells), tuple(feature['morlet']),
            hashlib.sha256(struct.pack('<'+'d'*len(row['samples']), *row['samples'])).hexdigest(),
            tuple(sorted(row.get('metadata', {}).items()))))
    return windows


def overlap(a, b, window):
    return a.recording == b.recording and max(a.start, b.start) < min(a.start, b.start)+window


class WaveIndex:
    """Immutable bounded snapshot, built completely before it becomes queryable."""

    def __init__(self, windows, spec):
        self.spec = spec
        self.windows = tuple(windows)
        if not self.windows or len(self.windows) > MAX_WINDOWS:
            raise ValueError('window count outside 1..4096')
        self.layout = self.windows[0].layout
        if not self.layout or len(self.layout)*len(self.windows) > MAX_ENTRIES:
            raise ValueError('index-entry budget exceeded')
        self._validate_layout()
        for row in self.windows:
            self._validate(row)
        identities = [(r.recording, r.start) for r in self.windows]
        if len(identities) != len(set(identities)):
            raise ValueError('duplicate source interval')
        totals = defaultdict(int)
        for band, start, end, _ in self.layout:
            totals[band] += end-start
        self.weights = tuple((end-start)/totals[band]/len(totals)
                             for band, start, end, _ in self.layout)
        postings = defaultdict(list)
        for i, row in enumerate(self.windows):
            for key in self._keys(row):
                postings[key].append(i)
        self.postings = MappingProxyType({key: tuple(ids) for key, ids in postings.items()})
        self.generation = digest({'spec': spec.id, 'sources': sorted(
            (r.recording, r.start, r.source_sha256, r.metadata, r.layout, r.values, r.pooled)
            for r in self.windows)})

    def _validate_layout(self):
        if set(c[0] for c in self.layout) != set(range(12)):
            raise ValueError('layout must cover all twelve bands')
        previous, radii = {}, {}
        for band, start, end, radius in self.layout:
            if (any(type(x) is not int for x in (band, start, end, radius))
                    or radius < 0 or not radius <= start < end <= self.spec.window-radius
                    or end-start > 128 or start != previous.get(band, radius)
                    or radius != radii.get(band, radius)):
                raise ValueError('invalid coefficient-center layout or support')
            previous[band] = end
            radii[band] = radius
        if any(previous[b] != self.spec.window-radii[b] for b in previous):
            raise ValueError('incomplete cell coverage')

    def _validate(self, row):
        if row.spec_id != self.spec.id or row.layout != self.layout:
            raise ValueError('incompatible representation or cell layout')
        if (not isinstance(row.recording, str) or not row.recording
                or type(row.start) is not int or row.start < 0
                or row.start > 2**63-1-self.spec.window
                or len(row.values) != len(self.layout) or len(row.pooled) != 15
                or any(not math.isfinite(v) or v < 0 for v in row.values)
                or any(not math.isfinite(v) for v in row.pooled)
                or len(row.source_sha256) != 64
                or any(c not in '0123456789abcdef' for c in row.source_sha256)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in row.metadata)):
            raise ValueError('invalid indexed window')

    def _keys(self, row):
        # Local position is part of the key: unlike pooled energy, order matters.
        return [(band, start, end, math.floor(value/self.spec.bin_width))
                for (band, start, end, _), value in zip(self.layout, row.values)]

    def _eligible(self, query, metadata, exclude_source):
        self._validate(query)
        return [i for i, row in enumerate(self.windows)
                if all(dict(row.metadata).get(k) == v for k, v in metadata.items())
                and not (exclude_source and overlap(query, row, self.spec.window))]

    def _distance(self, query, row):
        return math.sqrt(sum(w*(a-b)**2 for w, a, b in zip(
            self.weights, query.values, row.values)))

    def _finish(self, query, candidates, top_k, suppress_overlaps):
        ranked = sorted(((self._distance(query, self.windows[i]), i) for i in candidates),
                        key=lambda x: (x[0], self.windows[x[1]].recording,
                                       self.windows[x[1]].start))
        selected = []
        for distance, i in ranked:
            row = self.windows[i]
            if suppress_overlaps and any(overlap(row, other, self.spec.window)
                                         for _, other in selected):
                continue
            selected.append((distance, row))
            if len(selected) == top_k:
                break
        return [{'id': r.recording, 'start': r.start, 'end': r.start+self.spec.window,
                 'distance': d, 'source_sha256': r.source_sha256}
                for d, r in selected]

    def search(self, query, *, approximate=False, candidate_budget=128,
               posting_budget=1_000_000, top_k=5, metadata=None,
               exclude_source=True, suppress_overlaps=True):
        if (type(top_k) is not int or not 1 <= top_k <= 100
                or type(candidate_budget) is not int or not 1 <= candidate_budget <= MAX_WINDOWS
                or type(posting_budget) is not int or not 1 <= posting_budget <= MAX_ENTRIES):
            raise ValueError('invalid query budget')
        eligible = self._eligible(query, {} if metadata is None else metadata, exclude_source)
        candidates, visits = eligible, 0
        if approximate:
            allowed, votes = set(eligible), defaultdict(int)
            for key in self._keys(query):
                posting = self.postings.get(key, ())
                visits += len(posting)
                if visits > posting_budget:
                    raise ValueError('posting budget exceeded; no partial top-k returned')
                for i in posting:
                    if i in allowed:
                        votes[i] += 1
            candidates = sorted(votes, key=lambda i: (-votes[i], self.windows[i].recording,
                                                      self.windows[i].start))[:candidate_budget]
        return {'mode': 'approximate' if approximate else 'exhaustive',
                'exhaustive': not approximate, 'incomplete': False,
                'spec_id': self.spec.id, 'generation_id': self.generation,
                'score': 'equal-band-duration-weighted-log-energy-rmse-v1',
                'eligible': len(eligible), 'scored': len(candidates), 'posting_visits': visits,
                'candidate_budget': candidate_budget, 'posting_budget': posting_budget,
                'candidate_intervals': [(self.windows[i].recording, self.windows[i].start)
                                        for i in candidates],
                'matches': self._finish(query, candidates, top_k, suppress_overlaps)}


def run_demo(binary, size=128):
    """Controlled order-discrimination/retrieval experiment, not field relevance."""
    if type(size) is not int or not 4 <= size <= 512:
        raise ValueError('demo size must be 4..512')
    spec = Spec()
    samples = [0.0]*spec.window
    for i in range(300):
        samples[450+i] = math.sin(.2*i)*math.sin(math.pi*i/300)**2
    rows = [{'id': 'query', 'samples': samples}, {'id': 'same', 'samples': samples},
            {'id': 'reversed', 'samples': samples[::-1]}]
    for i in range(size-3):
        frequency = .03 + .008*i
        rows.append({'id': f'tone-{i:04d}', 'samples': [math.sin(frequency*j)
                                                       for j in range(spec.window)]})
    started = time.perf_counter()
    extracted = extract_windows(rows, spec, binary)
    extraction_s = time.perf_counter()-started
    query, gallery = extracted[0], extracted[1:]
    started = time.perf_counter()
    index = WaveIndex(gallery, spec)
    build_s = time.perf_counter()-started
    pooled = math.sqrt(sum((a-b)**2 for a, b in zip(query.pooled, gallery[1].pooled))/15)
    temporal = index._distance(query, gallery[1])
    assert pooled < 1e-8 and temporal > 0.01, (pooled, temporal)
    pooled_ranking = sorted(({'id': row.recording,
                             'distance': math.sqrt(sum((a-b)**2 for a, b in zip(
                                 query.pooled, row.pooled))/15)} for row in gallery),
                            key=lambda r: (r['distance'], r['id']))
    started = time.perf_counter()
    exact = index.search(query, top_k=3)
    exact_s = time.perf_counter()-started
    assert exact['matches'][0]['id'] == 'same'
    sweeps = []
    oracle = {(m['id'], m['start']) for m in exact['matches']}
    for budget in sorted({1, 4, 16, min(128, len(gallery)), len(gallery)}):
        started = time.perf_counter()
        result = index.search(query, approximate=True, candidate_budget=budget, top_k=3)
        elapsed = time.perf_counter()-started
        found = {(m['id'], m['start']) for m in result['matches']}
        captured = {tuple(pair) for pair in result['candidate_intervals']}
        sweeps.append({'query_s': elapsed,
                       'candidate_recall_at_3': len(captured & oracle)/len(oracle),
                       'recall_at_3_against_exact': len(found & oracle)/len(oracle),
                       **result})
    return {'version': VERSION, 'synthetic': True, 'spec': asdict(spec),
            'host': platform.platform(), 'python': platform.python_version(),
            'adapter_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
            'source_windows': [{'id': r.recording, 'start': r.start,
                                'end': r.start+spec.window, 'sha256': r.source_sha256}
                               for r in extracted],
            'gallery_windows': len(gallery), 'cell_count_per_window': len(query.layout),
            'posting_keys': len(index.postings),
            'posting_entries': sum(len(v) for v in index.postings.values()),
            'pooled_top_3': pooled_ranking[:3],
            'pooled_reversal_distance': pooled, 'temporal_reversal_distance': temporal,
            'extraction_s': extraction_s, 'index_build_s': build_s,
            'exact_query_s': exact_s, 'exact': exact, 'candidate_sweep': sweeps,
            'limits': 'in-memory Python postings; Rust extraction; no persistence/SQL/RLS; '
                      'single-query timings, not a speedup or production capacity claim'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--features-bin', type=Path, default=Path('target/release/examples/features'))
    parser.add_argument('--size', type=int, default=128)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run_demo(args.features_bin, args.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'output': str(args.output), 'gallery_windows': report['gallery_windows'],
                      'pooled_reversal_distance': report['pooled_reversal_distance'],
                      'temporal_reversal_distance': report['temporal_reversal_distance']}))


if __name__ == '__main__':
    main()
