#!/usr/bin/env python3
"""S1 (PROTOCOL.md): explainable sleep staging by retrieval of similar epochs.

  s1.py split SUMS OUT_JSON       subject split from the file names in SHA256SUMS.txt (reads no recording)
  s1.py features DATA OUT         epochs, labels and Morlet/FFT features for every night (pg_morlet for Morlet)
  s1.py check OUT                 product check: 20 random epochs recomputed in separate SQL calls
  s1.py select OUT                k and C on development subjects only (leave-subjects-out CV)
  s1.py evaluate OUT              reads held-out subjects once; writes OUT/s1.json

Seismic environment (numpy, scipy, psycopg), python -I. DSN from MORLET_DSN.
"""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

SEED = 7
RATE = 100.0
EPOCH = 3000                      # 30 s at 100 Hz
CHANNEL = "EEG Fpz-Cz"
EXTRACTOR = "morlet-energy-lowrate-v1"
STAGES = ("W", "N1", "N2", "N3", "REM")
MAP = {"W": 0, "1": 1, "2": 2, "3": 3, "4": 3, "R": 4}   # M and ? dropped
TRIM_EPOCHS = 60                  # 30 min either side of the sleep period
KS = (5, 10, 20, 40)
CS = (0.01, 0.1, 1.0, 10.0)
FOLDS = 5
RESAMPLES = 2000
DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_sleep")
NAME = re.compile(r"sleep-cassette/SC4(\d\d)(\d)\w\w-PSG\.edf$")


# -- split ---------------------------------------------------------------------------------
def cmd_split(sums, out):
    subjects = set()
    for line in Path(sums).read_text().splitlines():
        m = NAME.search(line)
        if m:
            subjects.add(int(m.group(1)))
    subjects = sorted(subjects)
    order = [int(s) for s in np.random.default_rng(SEED).permutation(subjects)]
    n_dev = len(order) // 2
    split = {"protocol": "S1", "seed": SEED, "subjects": len(order),
             "development": order[:n_dev], "held_out": order[n_dev:]}
    Path(out).write_text(json.dumps(split, indent=1) + "\n")
    print(len(order), "subjects:", n_dev, "development,", len(order) - n_dev, "held-out")


def load_split():
    return json.loads((HERE / "split.json").read_text())


HERE = Path(__file__).resolve().parent


# -- EDF -----------------------------------------------------------------------------------
def edf_header(raw):
    ns = int(raw[252:256])
    h = {"start": (raw[168:176].decode(), raw[176:184].decode()), "header_bytes": int(raw[184:192]),
         "records": int(raw[236:244]), "duration": float(raw[244:252]), "ns": ns}
    off = 256
    fields = (("label", 16), ("transducer", 80), ("unit", 8), ("pmin", 8), ("pmax", 8), ("dmin", 8),
              ("dmax", 8), ("prefilter", 80), ("n", 8), ("reserved", 32))
    for name, width in fields:
        h[name] = [raw[off + i * width: off + (i + 1) * width].decode("latin-1").strip() for i in range(ns)]
        off += width * ns
    h["n"] = [int(x) for x in h["n"]]
    return h


def read_signal(path, label):
    raw = Path(path).read_bytes()
    h = edf_header(raw)
    j = h["label"].index(label)
    rec = sum(h["n"])
    data = np.frombuffer(raw, dtype="<i2", offset=h["header_bytes"], count=rec * h["records"]).reshape(h["records"], rec)
    start = sum(h["n"][:j])
    d = data[:, start:start + h["n"][j]].astype(np.float64).ravel()
    pmin, pmax, dmin, dmax = (float(h[k][j]) for k in ("pmin", "pmax", "dmin", "dmax"))
    rate = h["n"][j] / h["duration"]
    return (d - dmin) * (pmax - pmin) / (dmax - dmin) + pmin, rate, h["unit"][j], h["start"]


TAL = re.compile(rb"\+(\d+(?:\.\d+)?)\x15(\d+(?:\.\d+)?)\x14([^\x14]*)\x14")


def read_hypnogram(path):
    raw = Path(path).read_bytes()
    h = edf_header(raw)
    body = raw[h["header_bytes"]:]
    return h["start"], [(float(a), float(b), c.decode("latin-1")) for a, b, c in TAL.findall(body)]


def night_files(data, subject):
    d = Path(data)
    out = []
    for psg in sorted(d.glob(f"SC4{subject:02d}?*-PSG.edf")):
        night = int(psg.name[5])
        hyp = sorted(d.glob(psg.name[:7] + "*-Hypnogram.edf"))
        if len(hyp) != 1:
            raise SystemExit(f"{psg.name}: expected one hypnogram, found {len(hyp)}")
        out.append((night, psg, hyp[0]))
    return out


def epochs_of_night(psg, hyp):
    """Labelled epochs (index, stage) within 30 min of the sleep period; and the EEG signal."""
    x, rate, unit, start = read_signal(psg, CHANNEL)
    if rate != RATE:
        raise SystemExit(f"{psg.name}: rate {rate}")
    hstart, ann = read_hypnogram(hyp)
    if hstart != start:
        raise SystemExit(f"{psg.name}: hypnogram start {hstart} != PSG start {start}")
    n = len(x) // EPOCH
    label = np.full(n, -1)
    for onset, dur, text in ann:
        if not text.startswith("Sleep stage "):
            continue
        stage = text[len("Sleep stage "):]
        if onset % 30 or dur % 30:
            raise SystemExit(f"{hyp.name}: annotation not on the 30-s grid ({onset}, {dur})")
        if stage in MAP:
            a = int(onset // 30)
            label[a:min(n, a + int(dur // 30))] = MAP[stage]
    sleep = np.flatnonzero(label >= 1)
    lo, hi = max(0, sleep[0] - TRIM_EPOCHS), min(n - 1, sleep[-1] + TRIM_EPOCHS)
    idx = np.array([i for i in range(lo, hi + 1) if label[i] >= 0])
    return x, unit, idx, label[idx]


# -- features ------------------------------------------------------------------------------
def morlet_night(con, x, idx):
    with con.cursor() as cur:
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS s1_epochs (i integer, samples float8[])")
        cur.execute("TRUNCATE s1_epochs")
        with cur.copy("COPY s1_epochs (i, samples) FROM STDIN") as cp:
            for i in idx:
                cp.write_row((int(i), x[i * EPOCH:(i + 1) * EPOCH].tolist()))
        cur.execute("SELECT i, public.morlet_features(samples, %s, %s) FROM s1_epochs ORDER BY i",
                    (RATE, EXTRACTOR))
        rows = cur.fetchall()
    if [r[0] for r in rows] != [int(i) for i in idx]:
        raise SystemExit("epoch order mismatch")
    return np.array([r[1] for r in rows])


def centres():
    return RATE * (1 / 64) * (0.4 * 64) ** (np.arange(12) / 11)


def layout_edges():
    c = centres()
    r = c[1] / c[0]
    return np.concatenate([[c[0] / np.sqrt(r)], np.sqrt(c[:-1] * c[1:]), [c[-1] * np.sqrt(r)]])


CLASSIC = np.array([0.5, 4, 8, 12, 16, 30])


def welch(epoch, seg=400):
    w = np.hanning(seg)
    step = seg // 2
    segs = [epoch[s:s + seg] for s in range(0, len(epoch) - seg + 1, step)]
    p = np.mean([np.abs(np.fft.rfft((s - s.mean()) * w)) ** 2 for s in segs], axis=0)
    p /= RATE * np.sum(w ** 2)
    p[1:-1] *= 2
    return np.fft.rfftfreq(seg, 1 / RATE), p


def band_powers(f, p, edges):
    df = f[1] - f[0]
    return np.array([np.log1p(np.sum(p[(f >= lo) & (f < hi)]) * df) for lo, hi in zip(edges[:-1], edges[1:])])


def fft_night(x, idx):
    same, classic = [], []
    for i in idx:
        f, p = welch(x[i * EPOCH:(i + 1) * EPOCH])
        same.append(band_powers(f, p, layout_edges()))
        classic.append(band_powers(f, p, CLASSIC))
    return np.array(same), np.array(classic)


def subject_features(con, data, subject):
    out = []
    for night, psg, hyp in night_files(data, subject):
        x, unit, idx, y = epochs_of_night(psg, hyp)
        m = morlet_night(con, x, idx)
        fs, fc = fft_night(x, idx)
        stats = m[:, 12:]
        out.append({"subject": subject, "night": night, "unit": unit, "epoch": idx, "y": y,
                    "morlet": m, "fft": np.hstack([fs, stats]), "fft_classic": np.hstack([fc, stats])})
    return out


def save(out_dir, nights):
    d = Path(out_dir) / "features"
    d.mkdir(parents=True, exist_ok=True)
    for n in nights:
        np.savez(d / f"SC4{n['subject']:02d}{n['night']}.npz", **{k: v for k, v in n.items() if k != "unit"},
                 unit=n["unit"])


def load(out_dir, subjects):
    nights = []
    for s in subjects:
        for p in sorted((Path(out_dir) / "features").glob(f"SC4{s:02d}?.npz")):
            z = np.load(p)
            nights.append({k: z[k] for k in z.files})
    return nights


def cmd_features(data, out, which="development"):
    import psycopg
    split = load_split()
    if which != "development":
        raise SystemExit("only development features are extracted here; held-out ones are read by evaluate")
    with psycopg.connect(DSN, autocommit=True) as con:
        for s in split["development"]:
            nights = subject_features(con, data, s)
            save(out, nights)
            print(s, [(int(n["night"]), len(n["y"]), str(n["unit"])) for n in nights], flush=True)


def cmd_check(out, data):
    import psycopg
    split = load_split()
    rng = np.random.default_rng(SEED)
    nights = load(out, split["development"])
    with psycopg.connect(DSN, autocommit=True) as con:
        worst, picks = 0.0, []
        for _ in range(20):
            n = nights[rng.integers(len(nights))]
            k = int(rng.integers(len(n["epoch"])))
            files = {night: (psg, hyp) for night, psg, hyp in night_files(data, int(n["subject"]))}
            x, _, _, _ = epochs_of_night(*files[int(n["night"])])
            i = int(n["epoch"][k])
            v = con.execute("SELECT public.morlet_features(%s, %s, %s)",
                            (x[i * EPOCH:(i + 1) * EPOCH].tolist(), RATE, EXTRACTOR)).fetchone()[0]
            worst = max(worst, float(np.max(np.abs(np.array(v) - n["morlet"][k]))))
            picks.append([int(n["subject"]), int(n["night"]), i])
    res = {"epochs": picks, "max_abs_difference": worst, "pass": worst == 0.0}
    (Path(out) / "product_check.json").write_text(json.dumps(res, indent=1))
    print(res["max_abs_difference"], res["pass"])


# -- models --------------------------------------------------------------------------------
def stack(nights, key, context=False):
    xs, ys, groups = [], [], []
    for n in nights:
        f = n[key]
        if context:
            pos = {int(e): j for j, e in enumerate(n["epoch"])}
            prev = [pos.get(int(e) - 1, j) for j, e in enumerate(n["epoch"])]
            nxt = [pos.get(int(e) + 1, j) for j, e in enumerate(n["epoch"])]
            f = np.hstack([f[prev], f, f[nxt]])
        xs.append(f)
        ys.append(n["y"])
        groups.append(np.full(len(n["y"]), int(n["subject"])))
    return np.vstack(xs), np.concatenate(ys), np.concatenate(groups)


def standardizer(x):
    mu, sd = x.mean(0), x.std(0)
    sd[sd == 0] = 1
    return lambda z: (z - mu) / sd


def knn_neighbours(gallery, queries, k, chunk=1024):
    g2 = np.sum(gallery ** 2, 1)
    out = np.empty((len(queries), k), dtype=np.int64)
    for s in range(0, len(queries), chunk):
        q = queries[s:s + chunk]
        with np.errstate(all="ignore"):   # Accelerate BLAS raises spurious FP flags in matmul
            d = g2[None, :] - 2 * q @ gallery.T + np.sum(q ** 2, 1)[:, None]
        if not np.all(np.isfinite(d)):
            raise SystemExit("non-finite distance")
        part = np.argpartition(d, k - 1, axis=1)[:, :k]
        dd = np.take_along_axis(d, part, 1)
        order = np.lexsort((part, dd), axis=1)
        out[s:s + chunk] = np.take_along_axis(part, order, 1)
    return out


def vote(labels):
    """Majority vote over neighbours ordered nearest first; ties go to the class of the nearest tied neighbour."""
    pred = np.empty(len(labels), dtype=np.int64)
    for i, row in enumerate(labels):
        counts = np.bincount(row, minlength=len(STAGES))
        best = np.flatnonzero(counts == counts.max())
        pred[i] = next(c for c in row if c in best)
    return pred


def lr_fit(x, y, c):
    from scipy.optimize import minimize
    n, d = x.shape
    k = len(STAGES)
    xb = np.hstack([x, np.ones((n, 1))])
    counts = np.bincount(y, minlength=k)
    w = (n / (k * np.maximum(counts, 1)))[y]
    onehot = np.eye(k)[y]

    def loss(theta):
        W = theta.reshape(d + 1, k)
        z = xb @ W
        z -= z.max(1, keepdims=True)
        lse = np.log(np.exp(z).sum(1))
        p = np.exp(z - lse[:, None])
        val = np.sum(w * (lse - np.sum(onehot * z, 1))) + np.sum(W[:-1] ** 2) / (2 * c)
        grad = xb.T @ (w[:, None] * (p - onehot))
        grad[:-1] += W[:-1] / c
        return val, grad.ravel()

    r = minimize(loss, np.zeros((d + 1) * k), jac=True, method="L-BFGS-B",
                 options={"gtol": 1e-6, "maxiter": 1000})
    return r.x.reshape(d + 1, k), bool(r.success), int(r.nit)


def lr_predict(W, x):
    return np.argmax(np.hstack([x, np.ones((len(x), 1))]) @ W, 1)


def kappa(y, p):
    k = len(STAGES)
    m = np.zeros((k, k))
    np.add.at(m, (y, p), 1)
    n = m.sum()
    po = np.trace(m) / n
    pe = np.sum(m.sum(0) * m.sum(1)) / n ** 2
    return float((po - pe) / (1 - pe)) if pe < 1 else float("nan")


def f1s(y, p):
    out = []
    for c in range(len(STAGES)):
        tp = np.sum((y == c) & (p == c))
        fp = np.sum((y != c) & (p == c))
        fn = np.sum((y == c) & (p != c))
        out.append(float(2 * tp / (2 * tp + fp + fn)) if tp + fp + fn else float("nan"))
    return out


def per_subject_kappa(y, p, groups):
    return {int(s): kappa(y[groups == s], p[groups == s]) for s in np.unique(groups)}


ARMS = {"M-KNN": ("morlet", "knn"), "F-KNN": ("fft", "knn"), "M-LR": ("morlet", "lr"),
        "F5-KNN": ("fft_classic", "knn")}


def cv_select(nights, order):
    """Leave-subjects-out CV on development subjects only (PROTOCOL.md)."""
    folds = {s: i % FOLDS for i, s in enumerate(order)}
    res = {}
    for arm, (key, model) in ARMS.items():
        for context in (False, True):
            x, y, g = stack(nights, key, context)
            x = standardizer(x)(x)
            fold = np.array([folds[int(s)] for s in g])
            grid = KS if model == "knn" else CS
            scores = {}
            for v in grid:
                ks = []
                for f in range(FOLDS):
                    tr, te = fold != f, fold == f
                    if model == "knn":
                        nb = knn_neighbours(x[tr], x[te], v)
                        p = vote(y[tr][nb])
                    else:
                        W, _, _ = lr_fit(x[tr], y[tr], v)
                        p = lr_predict(W, x[te])
                    ks += list(per_subject_kappa(y[te], p, g[te]).values())
                scores[v] = float(np.mean(ks))
            best = max(grid, key=lambda v: (scores[v], -v))
            res[f"{arm}{' context' if context else ''}"] = {"scores": {str(k): s for k, s in scores.items()},
                                                            "chosen": best}
            print(arm, "context" if context else "", scores, "->", best, flush=True)
    return res


def cmd_select(out):
    split = load_split()
    nights = load(out, split["development"])
    res = cv_select(nights, split["development"])
    (Path(out) / "selection.json").write_text(json.dumps(res, indent=1))


# -- evaluate (held-out, once) ---------------------------------------------------------------
def bootstrap(a, b):
    a, b = np.asarray(a), np.asarray(b)
    idx = np.random.default_rng(SEED).integers(0, len(a), (RESAMPLES, len(a)))
    d = (a[idx] - b[idx]).mean(1)
    return float((a - b).mean()), [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def decide_q1(delta, ci):
    if delta >= 0.02 and ci[0] > 0:
        return "supported"
    return "not supported" if ci[1] < 0.02 else "inconclusive"


def decide_q2(delta, ci):
    if ci[0] > -0.05:
        return "supported"
    return "not supported" if ci[1] < -0.05 else "inconclusive"


def predict(arm, sel, dev, held, context=False, raw=False):
    key, model = ARMS[arm]
    xd, yd, gd = stack(dev, key, context)
    xh, yh, gh = stack(held, key, context)
    if not raw:
        z = standardizer(xd)
        xd, xh = z(xd), z(xh)
    v = sel[f"{arm}{' context' if context else ''}"]["chosen"]
    extra = {}
    if model == "knn":
        nb = knn_neighbours(xd, xh, v)
        p = vote(yd[nb])
        lab = yd[nb]
        extra = {"neighbour_agreement": float(np.mean(lab == p[:, None])),
                 "single_subject_neighbourhoods": float(np.mean([len(set(r)) == 1 for r in gd[nb]]))}
    else:
        W, ok, nit = lr_fit(xd, yd, v)
        p = lr_predict(W, xh)
        extra = {"converged": ok, "iterations": nit}
    return yh, p, gh, extra


def summarize(y, p, g, extra):
    ks = per_subject_kappa(y, p, g)
    return {"per_subject_kappa": ks, "mean_kappa": float(np.mean(list(ks.values()))),
            "macro_f1": float(np.nanmean(f1s(y, p))), "f1": dict(zip(STAGES, f1s(y, p))), **extra}


def personal(sel, dev, held):
    """Exploratory: each held-out subject's night 2 staged from their own night 1 (M-KNN)."""
    xd, _, _ = stack(dev, "morlet")
    z = standardizer(xd)
    k = sel["M-KNN"]["chosen"]
    out = {}
    by = {}
    for n in held:
        by.setdefault(int(n["subject"]), {})[int(n["night"])] = n
    for s, nn in by.items():
        if 1 in nn and 2 in nn:
            g, q = z(nn[1]["morlet"]), z(nn[2]["morlet"])
            p = vote(nn[1]["y"][knn_neighbours(g, q, min(k, len(g)))])
            out[s] = kappa(nn[2]["y"], p)
    return {"subjects": len(out), "mean_kappa": float(np.mean(list(out.values()))), "per_subject_kappa": out}


def cmd_evaluate(data, out):
    import psycopg
    split = load_split()
    sel = json.loads((Path(out) / "selection.json").read_text())
    if not json.loads((Path(out) / "product_check.json").read_text())["pass"]:
        raise SystemExit("product check has not passed")
    with psycopg.connect(DSN, autocommit=True) as con:
        for s in split["held_out"]:
            nights = subject_features(con, data, s)
            save(out, nights)
            print("held-out", s, [(int(n["night"]), len(n["y"])) for n in nights], flush=True)
    dev, held = load(out, split["development"]), load(out, split["held_out"])
    arms = {arm: summarize(*predict(arm, sel, dev, held)) for arm in ARMS}
    ctx = {arm: summarize(*predict(arm, sel, dev, held, context=True)) for arm in ARMS}
    raw = summarize(*predict("M-KNN", sel, dev, held, raw=True))
    subjects = sorted(arms["M-KNN"]["per_subject_kappa"])

    def q(a, b, rule):
        ka = [arms[a]["per_subject_kappa"][s] for s in subjects]
        kb = [arms[b]["per_subject_kappa"][s] for s in subjects]
        d, ci = bootstrap(ka, kb)
        return {"candidate": a, "comparator": b, "delta": d, "ci95": ci, "verdict": rule(d, ci)}

    res = {"protocol": "S1", "held_out_subjects": len(subjects),
           "epochs": {"development": int(sum(len(n["y"]) for n in dev)), "held_out": int(sum(len(n["y"]) for n in held))},
           "Q1": q("M-KNN", "F-KNN", decide_q1), "Q2": q("M-KNN", "M-LR", decide_q2),
           "arms": arms, "context": ctx, "M-KNN raw product distance": raw,
           "personal_gallery": personal(sel, dev, held), "selection": sel}
    (Path(out) / "s1.json").write_text(json.dumps(res, indent=1))
    for k in ("Q1", "Q2"):
        r = res[k]
        print(f"{k} {r['candidate']} vs {r['comparator']}: delta {r['delta']:+.3f} CI [{r['ci95'][0]:+.3f}, {r['ci95'][1]:+.3f}] -> {r['verdict']}")
    for name, group in (("single epoch", arms), ("context", ctx)):
        print(name, {a: round(v["mean_kappa"], 3) for a, v in group.items()})
    print("raw distance M-KNN", round(raw["mean_kappa"], 3), "personal", round(res["personal_gallery"]["mean_kappa"], 3))


if __name__ == "__main__":
    cmds = {"split": lambda: cmd_split(*sys.argv[2:4]),
            "features": lambda: cmd_features(*sys.argv[2:5]),
            "check": lambda: cmd_check(sys.argv[2], sys.argv[3]),
            "select": lambda: cmd_select(sys.argv[2]),
            "evaluate": lambda: cmd_evaluate(sys.argv[2], sys.argv[3])}
    cmds[sys.argv[1]]()
