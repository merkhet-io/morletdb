"""Shifted inner-product probes for the bounded-lag envelope scorer (PROTOCOL.md, family A).

c(l) = sum over the overlap of q[t+l] * g[t] (morlet_core::envelope::distance) equals <s_l, g> for the
zero-padded shifted query s_l[t] = q[t+l] (0 <= t+l < n), else 0.
"""
import numpy as np

WINDOW, TOP, MAX_LAG = 4096, 10, 30
GRIDS = {"G7": list(range(-30, 31, 10)), "G13": list(range(-30, 31, 5)), "G61": list(range(-30, 31))}


def shifted(q, lag):
    n = len(q)
    s = np.zeros(n, dtype=q.dtype)
    if lag >= 0:
        s[:n - lag] = q[lag:]
    else:
        s[-lag:] = q[:n + lag]
    return s


def probe_matrix(q, lags):
    return np.stack([shifted(q, l) for l in lags], axis=1)          # n x P


def lagged_sum(q, g, lag):
    """Reference: the Rust scorer's lag term (positive: q[t+lag] g[t]; negative: g[t-lag] q[t])."""
    n = len(q)
    if lag >= 0:
        return float(np.dot(q[lag:], g[:n - lag]))
    return float(np.dot(g[-lag:], q[:n + lag]))


def distance(q, g, max_lag=MAX_LAG):
    return 1.0 - max(lagged_sum(q, g, l) for l in range(-max_lag, max_lag + 1)) / len(q)


def zscore(v):
    v = np.asarray(v, dtype=np.float64)
    s = v.std()
    return (v - v.mean()) / (s if s > 0 else 1.0)


def decode(envelope):
    """morlet_core::envelope::decode: version byte 1, float16 little-endian values, then z-score."""
    b = bytes(envelope)
    assert b[0] == 1 and len(b) % 2 == 1
    return zscore(np.frombuffer(b[1:], dtype="<f2").astype(np.float64))


def topk(scores, k, keys):
    """Indices of the k largest scores, ordered by (score desc, key asc); keys: integer tie-break order."""
    k = min(k, len(scores))
    part = np.argpartition(-scores, k - 1)[:k]
    # include every row tied with the k-th score so that the tie-break is deterministic
    kth = scores[part].min()
    cand = np.flatnonzero(scores >= kth)
    order = np.lexsort((keys[cand], -scores[cand]))
    return cand[order][:k]


def union_probes(G, Q, k, keys):
    """Exact per-probe top-k over gallery G (N x n) for probes Q (n x P); returns the deduplicated union
    in first-seen order (probe order, then rank)."""
    S = G @ Q
    seen, out = set(), []
    for j in range(Q.shape[1]):
        for i in topk(S[:, j], k, keys):
            if i not in seen:
                seen.add(i)
                out.append(int(i))
    return out


def suppress(ranked, rid, start, top=TOP, window=WINDOW):
    """pg_morlet's exclusion and greedy overlap suppression on (recording, start, distance) in rank order."""
    out = []
    for r, s, _ in ranked:
        if r == rid and s < start + window and s + window > start:
            continue
        if any(r == pr and s < ps + window and s + window > ps for pr, ps in out):
            continue
        out.append((r, s))
        if len(out) == top:
            break
    return out
