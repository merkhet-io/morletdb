"""Study B9 block scheduling (PROTOCOL.md section 6.E), shared by the M5 Max runner (b9.py) and the Raspberry Pi
runner (b9pi.py). Python 3.9, standard library only.

One block per input runs ORDER (A = baseline, B = candidate), each run PASSES passes. Rules:
- A block with an invalid pass is void and redone in full from its first run; at most three attempts end void,
  then the input is "not measured".
- A block whose start condition is not met within the wait limit is set aside and retried once after the other
  blocks; if that fails too, the input is "not measured".
- Never redone for its figures: only invalid passes, interruptions and set-asides lead to another attempt.
- A round-trip mismatch (the harness aborts) or a chunk-bytes hash that differs between passes or arms of one input
  stops B9 (SystemExit).
- A step that overruns its budget (DEADLINE, epoch seconds) stops: the block under way and every block not yet
  complete are "not measured".

Restart: the output file is append-only. On start, inputs with a "complete" or "not measured" record are skipped. An
attempt that has passes but no block record was interrupted (the runner stopped mid-block, so a pass did not complete
or its block cannot be finished as one session): it is recorded as void ("interrupted"), counts as a void attempt, and
the block is redone from its first run. Earlier set-asides and voids are counted from the file.
"""
import json
import os
import time

CONFIGS = ["G1", "G1+6", "G21", "G21+6"]


class Stop(SystemExit):
    pass


def load(out, machine, label):
    rows = []
    if os.path.exists(out):
        with open(out) as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    if d.get("machine") == machine and d.get("label") == label:
                        rows.append(d)
    return rows


def run_blocks(out, machine, label, specs, order, passes, do_pass, snapshot, deadline=None, attempts=3,
               cross_arm=True):
    """specs: [(name, path)]. do_pass(name, path, arm, tag, run, pass_index, attempt) -> a pass record (dict with
    "valid", "invalid_reason", "result"), or None when the start condition was not met within the wait limit.
    snapshot(tag_suffix) takes a machine snapshot. cross_arm: the hashes must agree between the arms too (False for
    the reported c7113c9 arm, whose bytes differ by design; each arm must still agree with itself). Returns "done" or
    "budget"."""
    prev = load(out, machine, label)
    state = {}
    for name, _ in specs:
        rs = [r for r in prev if r["input"] == name]
        st = {"done": any(r.get("block") in ("complete", "not measured") for r in rs),
              "max_attempt": max([r.get("attempt", 0) for r in rs] or [0]),
              "voids": sum(1 for r in rs if r.get("block") == "void"),
              "set_aside": sum(1 for r in rs if r.get("block") == "set aside"),
              "hashes": {}, "interrupted": []}
        closed = {r["attempt"] for r in rs if r.get("block") and "attempt" in r}
        for a in sorted({r["attempt"] for r in rs if "result" in r}):
            if a not in closed:
                st["interrupted"].append(a)
        for r in rs:
            if r.get("result"):
                for c in CONFIGS:
                    st["hashes"].setdefault(c if cross_arm else (r["arm"], c), r["result"][c]["sha256"])
        state[name] = st
    paths = dict(specs)

    with open(out, "a") as f:
        def rec(d):
            d = dict(d, machine=machine, label=label)
            f.write(json.dumps(d) + "\n")
            f.flush()
            os.fsync(f.fileno())

        for name, st in state.items():
            for a in st["interrupted"]:
                rec({"input": name, "attempt": a, "block": "void",
                     "reason": "interrupted: the runner stopped mid-block (redone from its first run)"})
                st["voids"] += 1

        queue = [n for n, _ in specs if not state[n]["done"] and state[n]["set_aside"] == 0]
        retry = [n for n, _ in specs if not state[n]["done"] and state[n]["set_aside"] > 0]

        def budget_stop(current):
            rest = [current] + queue + retry if current else queue + retry
            for n in rest:
                rec({"input": n, "block": "not measured", "reason": "the step overran its budget"})
            return "budget"

        while queue or retry:
            if not queue:
                queue, retry = retry, []
            name = queue.pop(0)
            st = state[name]
            path = paths[name]
            while True:
                if st["voids"] >= attempts:
                    rec({"input": name, "block": "not measured", "reason": "three void attempts"})
                    break
                if deadline and time.time() > deadline:
                    return budget_stop(name)
                st["max_attempt"] += 1
                attempt = st["max_attempt"]
                tag = "%s-%s-attempt%d" % (label, name, attempt)
                snapshot(tag + "-before.txt")
                outcome = None
                for run, arm in enumerate(order, 1):
                    for p in range(passes):
                        if deadline and time.time() > deadline:
                            rec({"input": name, "attempt": attempt, "block": "void",
                                 "reason": "the step overran its budget mid-block"})
                            snapshot(tag + "-after.txt")
                            return budget_stop(name)
                        r = do_pass(name, path, arm, tag, run, p, attempt)
                        if r is None:
                            outcome = "timeout"
                            break
                        r.update({"input": name, "attempt": attempt, "run": run, "arm": arm, "pass": p})
                        rec(r)
                        res = r.get("result")
                        if res:
                            for c in CONFIGS:
                                want = st["hashes"].setdefault(c if cross_arm else (arm, c), res[c]["sha256"])
                                if res[c]["sha256"] != want:
                                    rec({"input": name, "attempt": attempt, "block": "STOP",
                                         "reason": "hash difference in %s: %s vs %s" % (c, res[c]["sha256"], want)})
                                    raise Stop("B9 stops: chunk-bytes hash difference on %s %s (%s)" % (name, c, arm))
                        if r.get("stop"):
                            rec({"input": name, "attempt": attempt, "block": "STOP", "reason": r["invalid_reason"]})
                            raise Stop("B9 stops: %s on %s (%s)" % (r["invalid_reason"], name, arm))
                        if not r["valid"]:
                            outcome = "void: " + str(r["invalid_reason"])
                            break
                    if outcome:
                        break
                snapshot(tag + "-after.txt")
                if outcome is None:
                    rec({"input": name, "attempt": attempt, "block": "complete"})
                    break
                if outcome == "timeout":
                    rec({"input": name, "attempt": attempt, "block": "set aside",
                         "reason": "start condition not met within the wait limit"})
                    if st["set_aside"] > 0:
                        rec({"input": name, "block": "not measured",
                             "reason": "start condition not met, also on the retry after the other blocks"})
                    else:
                        retry.append(name)
                    st["set_aside"] += 1
                    break
                rec({"input": name, "attempt": attempt, "block": "void", "reason": outcome[6:]})
                st["voids"] += 1
    return "done"
