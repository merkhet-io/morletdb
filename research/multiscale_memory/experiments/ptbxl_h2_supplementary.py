import json, sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score
sys.path.insert(0, "experiments")
import ptbxl_h2 as h
out = Path(sys.argv[1])
x, y, patient, fold, _ = h.load(out)
test = np.where(fold == 10)[0]; yt, pt = y[test], patient[test]
probs = {a: np.stack([np.load(r) for r in sorted((out/"runs").glob(f"{a}_seed*/test_probs.npy"))]).mean(0) for a in h.ARMS}
classes = ["NORM", "MI", "STTC", "CD", "HYP"]
table = {a: {"per_class_auroc": dict(zip(classes, [float(roc_auc_score(yt[:, c], p[:, c])) for c in range(5)])),
             "macro_auprc": float(np.mean([average_precision_score(yt[:, c], p[:, c]) for c in range(5)]))}
         for a, p in probs.items()}
rng = np.random.default_rng(11)
pats = np.unique(pt); mem = {q: np.where(pt == q)[0] for q in pats}
draws = [np.concatenate([mem[q] for q in rng.choice(pats, len(pats))]) for _ in range(1000)]
def boot(a, b):
    d = [h.macro_auroc(yt[i], probs[a][i]) - h.macro_auroc(yt[i], probs[b][i]) for i in draws
         if not (yt[i].sum(0) == 0).any() and not (yt[i].min(0).max() == 1)]
    return {"diff": h.macro_auroc(yt, probs[a]) - h.macro_auroc(yt, probs[b]),
            "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}
explor = {f"{a} vs A0": boot(a, "A0") for a in ["C32n", "C64n", "C128n", "W64n", "R64n", "C64"]}
rep = {"note": "Supplementary, computed after the single pre-registered evaluation from the same test predictions. "
               "Per-class AUROC and macro AUPRC are protocol secondary metrics omitted by evaluate(); "
               "the 'vs A0' comparisons are exploratory (not pre-registered).",
       "arms": table, "exploratory_comparisons": explor, "provenance": h.provenance()}
(out / "supplementary.json").write_text(json.dumps(rep, indent=1))
for a in ["A0", "A1", "A3", "A3n", "R64n", "W64n", "C64n"]:
    print(a, {k: round(v, 3) for k, v in table[a]["per_class_auroc"].items()}, "AUPRC", round(table[a]["macro_auprc"], 4))
for k, v in explor.items(): print(k, f"{v['diff']:+.4f}", [f"{c:+.4f}" for c in v["ci95"]])
