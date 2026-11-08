#!/usr/bin/env python3
"""J2 step 1: Jev with labelled examples (PROTOCOL.md). For every run of a held-out insert (case), the state holds
four labelled cuts from the other four inserts, chosen once per held-out insert, then the current cut.

  fewshot.py preview STATES_JSON           print the example choice and request sizes (no call)
  fewshot.py run STATES_JSON OUT_DIR       the calls: 3 per run, seeded random order, resumable

Uses J1's client (endpoint, key file, model jev-1.14.0); the key is never printed or written.
"""
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "system1_j1"))
import jev  # noqa: E402

WORN_VB, SEED, CALLS, TOKEN_CAP = 0.3, 330, 3, 5_000_000
QUESTION = {"replace_insert": {
    "type": "noul",
    "instructions": jev.QUESTIONS["replace_insert"]["instructions"] + (
        " The state also holds labelled examples: cuts from other inserts, each with its measured flank wear. Judge "
        "the current_cut, using the examples to see what deviations worn and sharp inserts produce."),
}}


def outcome(vb):
    return (f"worn: flank wear {vb:.2f} mm, insert replaced" if vb >= WORN_VB
            else f"not worn: flank wear {vb:.2f} mm, insert kept")


def examples_for(rows, case):
    """Two worn and two not-worn cuts from the other inserts, fixed per held-out insert (seed SEED + case)."""
    rng = random.Random(SEED + case)
    other = [r for r in rows if r["case"] != case]
    worn = sorted((r for r in other if r["vb"] >= WORN_VB), key=lambda r: (r["case"], r["run"]))
    sharp = sorted((r for r in other if r["vb"] < WORN_VB), key=lambda r: (r["case"], r["run"]))
    pick = rng.sample(worn, 2) + rng.sample(sharp, 2)
    rng.shuffle(pick)
    return pick


def request(rows, i, ex):
    return {"model": jev.MODEL, "questions": QUESTION,
            "state": {"examples": [{"cut": e["state"], "outcome": outcome(e["vb"])} for e in ex],
                      "current_cut": rows[i]["state"]}}


def main():
    mode, rows = sys.argv[1], json.loads(Path(sys.argv[2]).read_text())["rows"]
    cases = sorted({r["case"] for r in rows})
    ex = {c: examples_for(rows, c) for c in cases}
    if mode == "preview":
        for c in cases:
            print(c, [(e["case"], e["run"], e["vb"]) for e in ex[c]])
        sizes = [len(json.dumps(request(rows, i, ex[rows[i]["case"]]))) for i in range(len(rows))]
        print("request chars: max", max(sizes), "median", sorted(sizes)[len(sizes) // 2])
        return
    out = Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    api_key = jev.key()
    order = [(i, k) for i in range(len(rows)) for k in range(CALLS)]
    random.Random(SEED).shuffle(order)
    log = out / "fewshot_calls.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text().splitlines():
            c = json.loads(line)
            if c["http_status"] == 200:
                done.add((c["row"], c["repeat"]))
    tokens = 0
    with log.open("a") as f:
        for i, k in order:
            if (i, k) in done:
                continue
            res = jev.call(request(rows, i, ex[rows[i]["case"]]), api_key)
            tokens += ((res["response"] or {}).get("usage") or {}).get("input_tokens") or 0
            a = ((res["response"] or {}).get("answers") or {}).get("replace_insert") or {}
            ok = isinstance(a.get("noul"), (int, float)) and 0 <= a["noul"] <= 1
            f.write(json.dumps({"row": i, "case": rows[i]["case"], "run": rows[i]["run"], "repeat": k,
                                "examples": [[e["case"], e["run"]] for e in ex[rows[i]["case"]]],
                                "schema_ok": ok, **res}) + "\n")
            f.flush()
            if tokens > TOKEN_CAP:
                print("STOPPED: token cap reached")
                break
            if res["http_status"] in (401, 403):
                print(f"STOPPED: authorization failed ({res['http_status']})")
                break
    print(f"calls written to {log}; input tokens this session {tokens}")


if __name__ == "__main__":
    main()
