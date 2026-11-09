#!/usr/bin/env python3
"""J3 step 2: the frozen questions on J1's compact states, answered by Laya (local) or Jev (PROTOCOL.md).

  decide.py fit OUT_JSON                 the largest K in (3, 2, 1) whose states fit both Laya checkpoints
                                         (tokenizer only, no answers); writes the K and the token counts
  decide.py check ARM OUT_JSON           the control (#156): fixed non-study states, labels as shipped and swapped
  decide.py run ARM STATES_JSON OUT      one call per state, in J1's seeded order; latency per call

ARM: laya (convaiinnovations/laya), laya-td (convaiinnovations/laya-typed-decisions), jev (jev-1.14.0).
Laya runs on this machine's CPU at the pinned revisions; nothing is sent anywhere. Jev's key is read from
~/.config/typesafe/jev.key and never printed, logged or written.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "system1_j1"))

CHECKPOINTS = {   # laya 0.4.30's reviewed pins (laya/revisions.py, PINNED_REVISIONS)
    "laya": ("convaiinnovations/laya", "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"),
    "laya-td": ("convaiinnovations/laya-typed-decisions", "1a793eb568e6718f15941d08f85432581df534e3"),
}
SEED = 328
DEFS = ("Deviation is (value - earlier median) / |earlier median|; rank is the value's position among the earlier "
        "cuts of the same setting.")
QUESTIONS = {
    "replace_insert": {
        "type": "noul",
        "instructions": ("Has the cutting insert's flank wear reached 0.3 mm or more, so that it should be replaced? "
                         "Judge from how this cut's sensor values deviate from earlier cuts of the same setting made "
                         "with a sharp insert. " + DEFS),
    },
    "wear_level": {
        "type": "score",
        "instructions": ("How worn is the cutting insert after this cut? Judge from how this cut's sensor values "
                         "deviate from earlier cuts of the same setting made with a sharp insert."),
        "criteria": ["sharp: flank wear below 0.1 mm", "moderate: flank wear 0.1 to 0.3 mm",
                     "worn: flank wear 0.3 mm or more"],
    },
}
CONTROL = [   # (state, question, expected noul > 0.5)
    ("A clear day: the sky is blue and there are no clouds.", "Is the sky described as blue?", True),
    ("A grey day: the sky is overcast and it is raining.", "Is the sky described as blue?", False),
    ("The machine stopped: the spindle motor drew no current and the table did not vibrate.",
     "Was the machine cutting?", False),
    ("The machine cut steel at full feed; spindle current and vibration were high.", "Was the machine cutting?", True),
]


class Laya:
    def __init__(self, arm):
        from laya import Agent
        repo, rev = CHECKPOINTS[arm]
        t = time.perf_counter()
        self.agent = Agent(repo, device="cpu", revision=rev)
        self.load_s = time.perf_counter() - t
        self.model = f"{repo}@{rev[:12]}"

    def ask(self, state, questions):
        t = time.perf_counter()
        out = self.agent.system_one(state, questions)
        return {"http_status": 200, "wall_ms": round(1e3 * (time.perf_counter() - t), 1),
                "response": {"model": self.model, "answers": out.get("answers"), "usage": out.get("usage")}}


class Jev:
    def __init__(self, arm):
        import jev
        self.jev, self.key, self.model, self.load_s = jev, jev.key(), jev.MODEL, 0.0

    def ask(self, state, questions):
        return self.jev.call({"model": self.model, "state": state, "questions": questions}, self.key)


def backend(arm):
    return Jev(arm) if arm == "jev" else Laya(arm)


def schema_ok(resp):
    a = (resp or {}).get("answers") or {}
    n, s = a.get("replace_insert") or {}, a.get("wear_level") or {}
    return (isinstance(n.get("noul"), (int, float)) and 0 <= n["noul"] <= 1
            and isinstance(s.get("score"), (int, float)) and 0 <= s["score"] <= 2)


def fit(out):
    from laya import Agent
    from laya.common import state_room
    import compact
    rows = json.loads(compact.J1.read_text())["rows"]
    report = {}
    for k in (3, 2, 1):
        report[k] = {}
        for arm, (repo, rev) in CHECKPOINTS.items():
            ag = Agent(repo, device="cpu", revision=rev)
            room = min(state_room(ag.tok, ag._to_internal(q) if hasattr(ag, "_to_internal") else q,
                                  ag.cfg.get("max_len", 512), ag.cfg.get("head_max_len", 192))
                       for q in QUESTIONS.values())
            n = [len(ag.tok(compact.compact(r["state"], k), add_special_tokens=False)["input_ids"]) for r in rows]
            report[k][arm] = {"room": room, "max_state_tokens": max(n), "fits": max(n) <= room}
        if all(v["fits"] for v in report[k].values()):
            break
    chosen = next((k for k in (3, 2, 1) if k in report and all(v["fits"] for v in report[k].values())), None)
    Path(out).write_text(json.dumps({"k": chosen, "report": report}, indent=1) + "\n")
    print(json.dumps({"k": chosen, "report": report}))


def check(arm, out):
    b = backend(arm)
    res = []
    for labels in (None, {"false": "true", "true": "false"}, {"false": "B", "true": "A"}):
        for state, q, expect in CONTROL:
            qd = {"type": "noul", "instructions": q} | ({"labels": labels} if labels else {})
            r = b.ask(state, {"c": qd})
            p = ((r.get("response") or {}).get("answers") or {}).get("c", {}).get("noul")
            res.append({"labels": labels, "state": state, "question": q, "expected": expect, "noul": p,
                        "correct": p is not None and (p > 0.5) == expect, "wall_ms": r["wall_ms"]})
    # PASS: every control right with the shipped labels and with neutral ones (A/B); the swapped set is reported only
    passed = all(x["correct"] for x in res if x["labels"] is None or x["labels"].get("true") == "A")
    Path(out).write_text(json.dumps({"arm": arm, "model": getattr(b, "model", None), "load_s": round(b.load_s, 2),
                                     "passed": passed, "calls": res}, indent=1) + "\n")
    print(arm, "control", "PASS" if passed else "FAIL", sum(x["correct"] for x in res), "/", len(res))


def run(arm, states, out):
    rows = json.loads(Path(states).read_text())["rows"]
    order = list(range(len(rows)))
    random.Random(SEED).shuffle(order)
    out = Path(out)
    done = {json.loads(l)["row"] for l in out.read_text().splitlines()} if out.exists() else set()
    b = backend(arm)
    with out.open("a") as f:
        for i in order:
            if i in done:
                continue
            r = b.ask(rows[i]["text"], QUESTIONS)
            rec = {"row": i, "case": rows[i]["case"], "run": rows[i]["run"],
                   "schema_ok": r["http_status"] == 200 and schema_ok(r.get("response")), **r}
            f.write(json.dumps(rec) + "\n")
            f.flush()
    print(arm, "done", len(rows), "states; load", round(b.load_s, 1), "s")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "fit":
        fit(sys.argv[2])
    elif mode == "check":
        check(sys.argv[2], sys.argv[3])
    else:
        run(sys.argv[2], sys.argv[3], sys.argv[4])
