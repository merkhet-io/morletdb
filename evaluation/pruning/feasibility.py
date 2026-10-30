#!/usr/bin/env python3
"""Pruning feasibility: how many candidates can lag-uniform lower bounds exclude?

For every window of the R1b CX 2022 collection as a query (same-station and unfiltered
workloads, top 10, source excluded), compute the reference distances, take the TRUE
10th-best distance as the threshold (an oracle: no real search knows it in advance), and
count candidates whose lower bound exceeds it. Ties are kept. Every bound is asserted to
hold for every candidate. Development data only; this decides whether summaries are worth
building, not their final performance.

  .venv-seismic/bin/python -I evaluation/pruning/feasibility.py DBNAME evaluation/pruning/results/feasibility.json

Distances use the matcher's definition (morlet_core::envelope::distance) evaluated with
numpy; summation order differs from Rust, which matters for exact ties only, not for the
counts reported here.
"""
import json
import sys

import numpy as np
import psycopg

K = 10
LAG_FRACTION = 0.075
FFT_N = 512                 # >= n + L for 4096-sample windows (n = 410, L = 30)
SPEC_BINS = 31              # compact spectral summary: 31 low bins + 1 residual energy
KEOGH_BLOCKS = 16           # compact Keogh summary: 16 blocks x (positive sum, negative sum)


def lagged_max(q, G, L):
    n = len(q)
    best = G @ q
    for lag in range(1, L + 1):
        best = np.maximum(best, G[:, :n - lag] @ q[lag:])
        best = np.maximum(best, G[:, lag:] @ q[:n - lag])
    return best


def keogh_envelope(q, L):
    qp = np.concatenate([np.zeros(L), q, np.zeros(L)])
    win = np.lib.stride_tricks.sliding_window_view(qp, 2 * L + 1)
    return win.max(1), win.min(1)


def main():
    db, out = sys.argv[1], sys.argv[2]
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
        rows = c.execute(
            "SELECT e.recording_id, r.metadata->>'station', morlet_envelope_decode(e.envelope),"
            " (SELECT morlet_envelope(samples) FROM morlet_read_interval('envcheck_2022', e.recording_id, 0, 4096))"
            " FROM envcheck_2022_envelopes e JOIN envcheck_2022 r USING (recording_id)"
            " WHERE e.window_size = 4096 ORDER BY 1").fetchall()
    station = np.array([r[1] for r in rows])
    G = np.array([r[2] for r in rows])
    Q = np.array([r[3] for r in rows])
    m, n = G.shape
    L = int(np.floor(LAG_FRACTION * n))
    assert FFT_N >= n + L

    # Summaries of the stored side.
    w = np.full(FFT_N // 2 + 1, 2.0)
    w[0] = w[-1] = 1.0                                       # rfft bins counted twice except DC and Nyquist
    Gf = np.abs(np.fft.rfft(G, FFT_N, axis=1))
    g_rest = np.sqrt((w[SPEC_BINS:] * Gf[:, SPEC_BINS:] ** 2).sum(1))
    edges = np.linspace(0, n, KEOGH_BLOCKS + 1).astype(int)
    Gpos = np.add.reduceat(np.maximum(G, 0), edges[:-1], axis=1)
    Gneg = np.add.reduceat(np.maximum(-G, 0), edges[:-1], axis=1)

    names = ("spectral_full", "keogh_full", "min_full", "spectral_32", "keogh_32", "min_32")
    out_res = {}
    for workload in ("same_station", "unfiltered"):
        frac = {k: [] for k in names}
        over = []
        for qi in range(m):
            elig = np.arange(m) != qi
            if workload == "same_station":
                elig &= station == station[qi]
            q = Q[qi]
            best = lagged_max(q, G[elig], L)
            d = 1 - best / n
            tau = np.sort(d)[K - 1]
            # Spectral: for every |lag| <= L, c(lag) <= (1/N) sum_k w_k |Q_k||G_k|.
            Qf = np.abs(np.fft.rfft(q, FFT_N))
            spec_full = (Gf[elig] * Qf * w).sum(1) / FFT_N
            q_rest = np.sqrt((w[SPEC_BINS:] * Qf[SPEC_BINS:] ** 2).sum())
            spec_32 = ((Gf[elig, :SPEC_BINS] * Qf[:SPEC_BINS] * w[:SPEC_BINS]).sum(1) + q_rest * g_rest[elig]) / FFT_N
            # Keogh: c(lag) <= sum_t max(U[t] g[t], Lo[t] g[t]) with U/Lo over the query's +-L neighbourhood.
            U, Lo = keogh_envelope(q, L)
            keogh_full = np.maximum(U * G[elig], Lo * G[elig]).sum(1)
            Umax = np.maximum.reduceat(U, edges[:-1])
            Lneg = np.maximum.reduceat(-Lo, edges[:-1])
            keogh_32 = (Gpos[elig] * Umax + Gneg[elig] * Lneg).sum(1)
            ubs = {"spectral_full": spec_full, "keogh_full": keogh_full, "min_full": np.minimum(spec_full, keogh_full),
                   "spectral_32": spec_32, "keogh_32": keogh_32, "min_32": np.minimum(spec_32, keogh_32)}
            for k, ub in ubs.items():
                assert np.all(ub >= best - 1e-9 * n), (k, qi)
                frac[k].append(float(np.mean(1 - ub / n > tau)))
            over.append(float(np.median(ubs["min_full"] / np.maximum(best, 1e-12))))
        out_res[workload] = {"queries": m, "candidates_per_query": float(np.mean([(station == s).sum() - 1 for s in station]))
                             if workload == "same_station" else m - 1,
                             "prunable_fraction_mean": {k: float(np.mean(v)) for k, v in frac.items()},
                             "prunable_fraction_p10": {k: float(np.percentile(v, 10)) for k, v in frac.items()},
                             "median_bound_over_true_correlation_min_full": float(np.median(over))}
    out_res["summary_bytes_float16"] = {"spectral_32": 2 * (SPEC_BINS + 1), "keogh_32": 2 * 2 * KEOGH_BLOCKS,
                                        "stored_envelope": 1 + 2 * n}
    json.dump(out_res, open(out, "w"), indent=1)
    for wl in ("same_station", "unfiltered"):
        print(wl, {k: round(v, 3) for k, v in out_res[wl]["prunable_fraction_mean"].items()},
              "bound/true", round(out_res[wl]["median_bound_over_true_correlation_min_full"], 2))


if __name__ == "__main__":
    main()
