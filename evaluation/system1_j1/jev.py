#!/usr/bin/env python3
"""J1 step 2: send each run's decision state to Jev with the frozen questions (PROTOCOL.md).

  jev.py check OUT_DIR             one connectivity call with a fixed non-study state; records the model id
  jev.py run STATES_JSON OUT_DIR   the study: CALLS calls per scored run, in a seeded random order

The API key is read from a file (JEV_KEY_FILE, default ~/.config/typesafe/jev.key) and is never printed, logged
or written. Endpoint: JEV_URL (default https://api.typesafe.ai/v1/systemone); model: JEV_MODEL.
Every response is stored as returned (answers, model, usage) with the call's wall time.
"""
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-1.14.0")   # amendment 1
KEY_FILE = Path(os.environ.get("JEV_KEY_FILE", Path.home() / ".config/typesafe/jev.key"))
CALLS, SEED = 3, 328
TOKEN_CAP = 2_000_000          # input tokens over the whole study; the run stops before exceeding it

QUESTIONS = {
    "replace_insert": {
        "type": "noul",
        "instructions": (
            "Has the cutting insert's flank wear reached 0.3 mm or more, so that it should be replaced? Judge from "
            "how this cut's sensor values deviate from earlier cuts of the same setting made with a sharp insert. "
            "relative_deviation is (value - history_median) / |history_median|; history_quantile is the value's "
            "position among those earlier cuts."),
    },
    "wear_level": {
        "type": "score",
        "instructions": (
            "How worn is the cutting insert after this cut? Judge from how this cut's sensor values deviate from "
            "earlier cuts of the same setting made with a sharp insert."),
        "criteria": ["sharp: flank wear below 0.1 mm", "moderate: flank wear 0.1 to 0.3 mm",
                     "worn: flank wear 0.3 mm or more"],
    },
}
CHECK = {"state": "A clear day: the sky is blue and there are no clouds.",
         "questions": {"sky_blue": {"type": "noul", "instructions": "Is the sky described as blue?"}}}


def key():
    k = KEY_FILE.read_text().strip()
    if not k:
        raise SystemExit(f"{KEY_FILE} is empty")
    return k


def call(body, api_key):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
            status = r.status
    except urllib.error.HTTPError as e:
        out, status = {"error_body": e.read().decode(errors="replace")[:2000]}, e.code
    except (urllib.error.URLError, TimeoutError) as e:
        out, status = {"error": str(e)[:500]}, None
    return {"http_status": status, "wall_ms": round(1e3 * (time.perf_counter() - t), 1), "response": out}


def schema_ok(resp):
    a = (resp or {}).get("answers") or {}
    n, s = a.get("replace_insert") or {}, a.get("wear_level") or {}
    return (isinstance(n.get("noul"), (int, float)) and 0 <= n["noul"] <= 1
            and isinstance(s.get("score"), (int, float)) and 0 <= s["score"] <= 2)


def main():
    mode, args = sys.argv[1], sys.argv[2:]
    api_key = key()
    if mode == "check":
        out = Path(args[0])
        out.mkdir(parents=True, exist_ok=True)
        res = call({"model": MODEL, **CHECK}, api_key)
        (out / "check.json").write_text(json.dumps({"url": URL, "model_requested": MODEL, **res}, indent=1))
        print(json.dumps({"url": URL, "model_requested": MODEL, **res}, indent=1))
        return
    states, out = json.loads(Path(args[0]).read_text()), Path(args[1])
    out.mkdir(parents=True, exist_ok=True)
    rows = states["rows"]
    order = [(i, k) for i in range(len(rows)) for k in range(CALLS)]
    random.Random(SEED).shuffle(order)
    log = out / "calls.jsonl"
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
            body = {"model": MODEL, "state": rows[i]["state"], "questions": QUESTIONS}
            res = call(body, api_key)
            tokens += ((res["response"] or {}).get("usage") or {}).get("input_tokens") or 0
            rec = {"row": i, "case": rows[i]["case"], "run": rows[i]["run"], "repeat": k,
                   "schema_ok": schema_ok(res["response"]), **res}
            f.write(json.dumps(rec) + "\n")
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
