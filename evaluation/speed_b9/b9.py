#!/usr/bin/env python3
"""Study B9 runner and analysis (PROTOCOL.md sections 4, 6 and 7). Standard library only.

  python3 -I b9.py verify WORK OUT.json                  section 4: every chunk file against B6's chunk_file_sha256
  python3 -I b9.py mac-speed OUT.jsonl MACHINE_DIR A=BIN B=BIN [--order ABBAABBA] [--passes 3] SPEC...
                                                          section 6.C/6.D: one block per SPEC (name=chunk file), in
                                                          the given order; start condition before every pass
  python3 -I b9.py analyse SPEED.jsonl [...]             section 7: ratios per machine, input, configuration, op
  python3 -I b9.py report OUT.json OUT.md LABEL SPEED.jsonl...   section 7's tables (H1, H2, aggregates, diagnostics)

A block runs ORDER (A = the first binary, B = the second), each run PASSES passes, each pass its own process
(`b9codec speed 1 FILE`). One JSON line per pass. A block with an invalid pass is void and redone from its first run,
at most three attempts; a block whose start condition is not met within 30 minutes is set aside and retried once
after the other blocks (section 6.E). A round-trip mismatch (the harness aborts) stops everything.
"""
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

CONFIGS = ["G1", "G1+6", "G21", "G21+6"]
OPS = [("encode", "encode_s"), ("decode", "decode_s")]
WAIT_MAX_S = int(os.environ.get("B9_WAIT_MAX_S", "1800"))
ATTEMPTS = 3
# Docker Desktop's own processes (allowed by the start condition: "no process other than Docker's").
DOCKER = re.compile(r"docker|qemu|vpnkit|com\.apple\.Virtua|containerd", re.I)
# Build, test, fuzz and benchmark processes: one found during a pass makes it invalid (section 6.E).
FORBIDDEN = {"cargo", "rustc", "rustdoc", "cargo-fuzz", "cargo-kani", "kani-driver", "kani-compiler", "cbmc",
             "kissat", "goto-cc", "goto-instrument", "decode_raw", "decode_sealed", "decode_v5", "decode_v6",
             "roundtrip", "decode_frame", "b6codec", "b8codec", "g21_bench", "pytest", "clang", "cc1", "xcodebuild",
             "swift-build", "pg_regress", "make", "ninja", "cmake"}


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- section 4: inputs
def verify(work, out):
    here = Path(__file__).resolve().parent
    b6 = json.loads((here.parent / "storage_b6/results/b6_inputs.json").read_text())
    b8 = json.loads((here.parent / "storage_b8/results/bench/b6_inputs.json").read_text())
    want = {r["input"]: r for r in b6["inputs"]}
    want8 = {r["input"]: r["chunk_file_sha256"] for r in b8["inputs"]}
    built = {r["input"]: r for r in json.loads((Path(work) / "inputs.json").read_text())["inputs"]}
    rows, ok = [], True
    for name, w in want.items():
        p = Path(work) / w["chunk_file"]
        got = sha_file(p) if p.exists() else None
        match = got == w["chunk_file_sha256"]
        ok &= match
        rows.append({"input": name, "set": w["set"], "samples": w["samples"], "chunks": w["chunks"],
                     "chunk_file": w["chunk_file"], "sha256": got, "b6_chunk_file_sha256": w["chunk_file_sha256"],
                     "b8_chunk_file_sha256": want8.get(name), "match": match,
                     "manifest_sha256_matches": built.get(name, {}).get("chunk_file_sha256") == got})
    res = {"study": "B9 inputs (PROTOCOL.md section 4)", "checked": now(), "work": str(work), "all_match": ok,
           "inputs": rows}
    Path(out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({"inputs": len(rows), "all_match": ok,
                      "mismatches": [r["input"] for r in rows if not r["match"]]}))
    if not ok:
        raise SystemExit("B9 stops: a chunk file differs from B6's (section 4)")


# ---------------------------------------------------------------- section 6.C: the Mac
def load1():
    return float(sh("sysctl -n vm.loadavg").split()[1])


def busy_processes():
    """Processes above half a core in top's second sample (the first has no CPU figures)."""
    out = sh("top -l 2 -s 1 -n 25 -o cpu -stats pid,cpu,command")
    sample = out.split("PID")[-1].splitlines()[1:]
    busy = []
    for line in sample:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            cpu = float(parts[1])
        except ValueError:
            continue
        if cpu > 50.0:
            busy.append({"pid": parts[0], "cpu": cpu, "command": parts[2].strip(),
                         "docker": bool(DOCKER.search(parts[2]))})
    return busy


def forbidden_running():
    names = set()
    for line in sh("ps -axo comm").splitlines()[1:]:
        names.add(os.path.basename(line.strip()))
    return sorted(names & FORBIDDEN)


def mac_condition():
    batt = sh("pmset -g batt")
    clam = sh("ioreg -r -k AppleClamshellState -d 4 | grep AppleClamshellState")
    busy = busy_processes()
    c = {"load1": load1(), "ac_power": "AC Power" in batt, "clamshell_closed": "= Yes" in clam,
         "busy_non_docker": [b for b in busy if not b["docker"]], "busy_docker": [b for b in busy if b["docker"]],
         "forbidden": forbidden_running()}
    c["ok"] = c["load1"] < 3 and c["ac_power"] and not c["busy_non_docker"] and not c["forbidden"]
    return c


def pmset_events(t0, t1):
    """Sleep or battery events in pmset's log between two epoch times."""
    ev = []
    for line in sh("pmset -g log | tail -n 600").splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(\S+)", line)
        if not m:
            continue
        t = time.mktime(time.strptime(m[1], "%Y-%m-%d %H:%M:%S %z"))
        if t0 - 1 <= t <= t1 + 1 and (m[2] in ("Sleep", "DarkWake", "Wake") or "Using Batt" in line):
            ev.append(line.strip())
    return ev


def mac_snapshot(path):
    txt = []
    for cmd in ("date", "uptime", "pmset -g batt", "pmset -g therm", "docker ps --format '{{.Names}} {{.Status}}'",
                "top -l 2 -s 1 -n 12 -o cpu -stats pid,cpu,mem,command | tail -n 14"):
        txt.append(f"$ {cmd}\n{sh(cmd)}")
    Path(path).write_text("\n".join(txt))


def wait_condition(cond):
    t0 = time.time()
    while True:
        c = cond()
        if c["ok"]:
            c["waited_s"] = round(time.time() - t0, 1)
            return c
        if time.time() - t0 > WAIT_MAX_S:
            return None
        time.sleep(15)


def run_pass(binary, path):
    p = subprocess.run([binary, "speed", "1", str(path)], capture_output=True, text=True)
    if p.returncode != 0:
        return None, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1]), None


def mac_speed(out, machine_dir, arms, order, passes, specs, label):
    machine_dir = Path(machine_dir)
    machine_dir.mkdir(parents=True, exist_ok=True)
    bins = {k: (v, sha_file(v)) for k, v in arms.items()}
    queue = list(specs)
    set_aside = []
    retried = set()
    with open(out, "a") as f:
        def rec(d):
            f.write(json.dumps(d) + "\n")
            f.flush()

        while queue:
            name, path = queue.pop(0)
            attempt, measured = 0, False
            while attempt < ATTEMPTS and not measured:
                attempt += 1
                tag = f"{label}-{name}-attempt{attempt}"
                mac_snapshot(machine_dir / f"{tag}-before.txt")
                void, timed_out = None, False
                for run, arm in enumerate(order, 1):
                    for pi in range(passes):
                        c = wait_condition(mac_condition)
                        if c is None:
                            timed_out = True
                            break
                        t0, s0 = time.time(), now()
                        res, err = run_pass(bins[arm][0], path)
                        t1, s1 = time.time(), now()
                        events = pmset_events(t0, t1)
                        after = {"forbidden": forbidden_running(), "ac_power": "AC Power" in sh("pmset -g batt")}
                        reason = None
                        if res is None:
                            reason = f"harness error: {err}"
                        elif events:
                            reason = f"sleep or battery: {events}"
                        elif after["forbidden"] or not after["ac_power"]:
                            reason = f"after the pass: {after}"
                        rec({"machine": "mac", "label": label, "input": name, "file": str(path), "attempt": attempt,
                             "run": run, "arm": arm, "binary": bins[arm][0], "binary_sha256": bins[arm][1],
                             "pass": pi, "start": s0, "end": s1, "seconds": round(t1 - t0, 3), "condition": c,
                             "after": after, "valid": reason is None, "invalid_reason": reason, "result": res})
                        if res is None and err and "round trip" in err:
                            raise SystemExit(f"B9 stops: round-trip mismatch on {name} ({arm})")
                        if reason:
                            void = reason
                            break
                    if void or timed_out:
                        break
                mac_snapshot(machine_dir / f"{tag}-after.txt")
                if timed_out:
                    rec({"machine": "mac", "label": label, "input": name, "attempt": attempt, "block": "set aside",
                         "reason": "start condition not met within 30 minutes"})
                    if name in retried:
                        rec({"machine": "mac", "label": label, "input": name, "block": "not measured"})
                    else:
                        retried.add(name)
                        set_aside.append((name, path))
                    break
                if void:
                    rec({"machine": "mac", "label": label, "input": name, "attempt": attempt, "block": "void",
                         "reason": void})
                    continue
                measured = True
                rec({"machine": "mac", "label": label, "input": name, "attempt": attempt, "block": "complete"})
            if not measured and not timed_out:
                rec({"machine": "mac", "label": label, "input": name, "block": "not measured",
                     "reason": "three void attempts"})
            if not queue and set_aside:
                queue, set_aside = set_aside, []


# ---------------------------------------------------------------- section 7: analysis
def load_passes(paths):
    rows = []
    for p in paths:
        for line in open(p):
            if line.strip():
                rows.append(json.loads(line))
    return rows


def analyse_rows(rows, order="ABBAABBA"):
    """Per (machine, label, input): the complete attempt's passes -> run speeds -> commit speeds -> ratio."""
    complete = {(r["machine"], r["label"], r["input"]): r["attempt"] for r in rows if r.get("block") == "complete"}
    out = []
    for (machine, label, name), attempt in complete.items():
        ps = [r for r in rows if r.get("result") and r["machine"] == machine and r["label"] == label
              and r["input"] == name and r["attempt"] == attempt]
        runs = {}
        for r in ps:
            runs.setdefault(r["run"], []).append(r)
        if len(runs) != len(order):
            print(f"note: {machine}/{label}/{name}: {len(runs)} runs (not {len(order)})", file=sys.stderr)
        samples = ps[0]["result"]["samples"]
        for cfg in CONFIGS:
            for op, key in OPS:
                run_speed = {}
                for run, rp in runs.items():
                    speeds = [samples / p["result"][cfg][key] / 1e6 for p in rp]
                    run_speed[run] = (rp[0]["arm"], max(speeds), speeds)
                a = [v[1] for k, v in sorted(run_speed.items()) if v[0] == "A"]
                b = [v[1] for k, v in sorted(run_speed.items()) if v[0] == "B"]
                pairs = []
                ai = [k for k in sorted(run_speed) if run_speed[k][0] == "A"]
                bi = [k for k in sorted(run_speed) if run_speed[k][0] == "B"]
                # pair i: the i-th B run with the i-th A run (A1 B1 B2 A2 A3 B3 B4 A4)
                for x, y in zip(ai, bi):
                    pairs.append(run_speed[y][1] / run_speed[x][1])
                hashes = {p["arm"]: set() for p in ps}
                for p in ps:
                    hashes[p["arm"]].add(p["result"][cfg]["sha256"])
                out.append({"machine": machine, "label": label, "input": name, "samples": samples,
                            "config": cfg, "op": op, "attempt": attempt,
                            "baseline": max(a), "candidate": max(b), "ratio": max(b) / max(a),
                            "pair_ratios": pairs, "median_ratio": statistics.median(b) / statistics.median(a),
                            "spread_baseline": (max(a) - min(a)) / max(a), "spread_candidate": (max(b) - min(b)) / max(b),
                            "runs_baseline": a, "runs_candidate": b,
                            "bytes_equal": len(hashes.get("A", set()) | hashes.get("B", set())) == 1,
                            "sha256": sorted(hashes.get("A", set()) | hashes.get("B", set()))})
    return out


def aggregate(res, machine, label, cfg, op, names):
    rs = [r for r in res if r["machine"] == machine and r["label"] == label and r["config"] == cfg
          and r["op"] == op and r["input"] in names]
    if len(rs) != len(names):
        return None
    tot = sum(r["samples"] for r in rs)
    return {"baseline": tot / sum(r["samples"] / r["baseline"] for r in rs),
            "candidate": tot / sum(r["samples"] / r["candidate"] for r in rs)}


TWELVE = ["cx", "ims", "sleep", "ecg", "pad-c-b6", "pad-p-b6", "b3-vibration_1", "b3-phase_current_1",
          "b3-phase_current_2", "b3-force", "b3-speed", "b3-torque"]
BOUND = {"decode": 0.97, "encode": 0.95}


def report(paths, out_json, out_md, label="b9"):
    """Section 7's tables from every pass record: H1 (Pi, G21+6, decode, >= 1.30 per input), H2 (both machines, 12
    inputs x 4 configurations x encode/decode against 0.97 / 0.95), aggregates, diagnostics, every other row."""
    rows = load_passes(paths)
    res = analyse_rows(rows)
    md = []
    blocks = [r for r in rows if r.get("block")]
    status = {}
    for r in blocks:
        status.setdefault((r["machine"], r["label"], r["input"]), []).append(r)

    def find(machine, name, cfg, op, lab=label):
        for r in res:
            if (r["machine"], r["label"], r["input"], r["config"], r["op"]) == (machine, lab, name, cfg, op):
                return r
        return None

    # H1
    md.append("### H1: Raspberry Pi 3B+, G21+6 decode, at least 1.30 on each of the 12 inputs\n")
    md.append("| input | samples | baseline M/s | candidate M/s | ratio | H1 |")
    md.append("|---|---:|---:|---:|---:|---|")
    h1 = []
    for n in TWELVE:
        r = find("pi", n, "G21+6", "decode")
        if r is None:
            md.append(f"| {n} | | | | not measured | not demonstrated |")
            h1.append(None)
            continue
        ok = r["ratio"] >= 1.30
        h1.append(ok)
        md.append(f"| {n} | {r['samples']:,} | {r['baseline']:.3f} | {r['candidate']:.3f} | {r['ratio']:.4f} | "
                  f"{'holds' if ok else 'fails'} |")
    ag = aggregate(res, "pi", label, "G21+6", "decode", TWELVE)
    if ag:
        md.append(f"| aggregate (sample-weighted harmonic, no criterion) | | {ag['baseline']:.3f} | "
                  f"{ag['candidate']:.3f} | {ag['candidate'] / ag['baseline']:.4f} | |")
    h1v = "not demonstrated" if None in h1 else ("holds" if all(h1) else "fails")
    md.append(f"\n**H1: {h1v}.**\n")
    # H2
    md.append("### H2: no input slower (decode >= 0.97, encode >= 0.95), 192 comparisons\n")
    md.append("| machine | input | config | op | baseline M/s | candidate M/s | ratio | bound | |")
    md.append("|---|---|---|---|---:|---:|---:|---:|---|")
    h2, below1, missing = [], [], 0
    for machine in ("mac", "pi"):
        for n in TWELVE:
            for cfg in CONFIGS:
                for op, _ in OPS:
                    r = find(machine, n, cfg, op)
                    if r is None:
                        missing += 1
                        md.append(f"| {machine} | {n} | {cfg} | {op} | | | not measured | {BOUND[op]} | |")
                        continue
                    ok = r["ratio"] >= BOUND[op]
                    mark = "**SLOWER (fails)**" if not ok else ("within tolerance" if r["ratio"] < 1.0 else "")
                    if r["ratio"] < 1.0:
                        below1.append((machine, n, cfg, op, r["ratio"], ok))
                    h2.append(ok)
                    md.append(f"| {machine} | {n} | {cfg} | {op} | {r['baseline']:.3f} | {r['candidate']:.3f} | "
                              f"{r['ratio']:.4f} | {BOUND[op]} | {mark} |")
    h2v = "not demonstrated" if missing else ("holds" if all(h2) else "fails")
    md.append(f"\n**H2: {h2v}** ({len(h2)} comparisons measured, {missing} not measured, "
              f"{sum(1 for x in h2 if not x)} below their bound, {len(below1)} below 1.00).\n")
    # aggregates
    md.append("### Aggregates over the 12 inputs (sample-weighted harmonic mean, no criterion)\n")
    md.append("| machine | config | op | baseline M/s | candidate M/s | ratio |")
    md.append("|---|---|---|---:|---:|---:|")
    for machine in ("mac", "pi"):
        for cfg in CONFIGS:
            for op, _ in OPS:
                ag = aggregate(res, machine, label, cfg, op, TWELVE)
                if ag:
                    md.append(f"| {machine} | {cfg} | {op} | {ag['baseline']:.3f} | {ag['candidate']:.3f} | "
                              f"{ag['candidate'] / ag['baseline']:.4f} |")
    # diagnostics
    md.append("\n### Diagnostics (decide nothing): pair ratios on the other side of a bound from the block ratio\n")
    md.append("| machine | label | input | config | op | ratio | pair ratios | median-run ratio | spread A | spread B |")
    md.append("|---|---|---|---|---|---:|---|---:|---:|---:|")
    for r in res:
        bounds = [BOUND[r["op"]]] + ([1.30] if (r["machine"], r["config"], r["op"]) == ("pi", "G21+6", "decode")
                                     else [])
        cross = any((p >= b) != (r["ratio"] >= b) for b in bounds for p in r["pair_ratios"])
        if cross:
            md.append(f"| {r['machine']} | {r['label']} | {r['input']} | {r['config']} | {r['op']} | {r['ratio']:.4f} | "
                      f"{', '.join(f'{p:.4f}' for p in r['pair_ratios'])} | {r['median_ratio']:.4f} | "
                      f"{r['spread_baseline']:.3f} | {r['spread_candidate']:.3f} |")
    # every row, including the reported-only ones
    md.append("\n### Every ratio (reported-only rows included)\n")
    md.append("| machine | label | input | config | decode A | decode B | decode ratio | encode A | encode B | "
              "encode ratio | bytes equal |")
    md.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|")
    keys = []
    for r in res:
        k = (r["machine"], r["label"], r["input"], r["config"])
        if k not in keys:
            keys.append(k)
    for k in keys:
        d = find(k[0], k[2], k[3], "decode", k[1])
        e = find(k[0], k[2], k[3], "encode", k[1])
        md.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {d['baseline']:.3f} | {d['candidate']:.3f} | "
                  f"{d['ratio']:.4f} | {e['baseline']:.3f} | {e['candidate']:.3f} | {e['ratio']:.4f} | "
                  f"{d['bytes_equal']} |")
    md.append("\n### Blocks\n")
    md.append("| machine | label | input | attempts and outcome |")
    md.append("|---|---|---|---|")
    for (machine, lab, name), bs in status.items():
        md.append(f"| {machine} | {lab} | {name} | " + "; ".join(
            f"attempt {b.get('attempt', '-')}: {b['block']}" + (f" ({b['reason']})" if b.get("reason") else "")
            for b in bs) + " |")
    Path(out_json).write_text(json.dumps({"h1": h1v, "h2": h2v, "below_1": below1, "rows": res}, indent=1) + "\n")
    Path(out_md).write_text("\n".join(md) + "\n")
    print(json.dumps({"h1": h1v, "h2": h2v, "h2_measured": len(h2), "h2_missing": missing,
                      "below_bound": [b for b in below1 if not b[5]], "below_1": len(below1)}))


def main():
    a = sys.argv[1:]
    if a[0] == "report":
        # report OUT.json OUT.md LABEL SPEED.jsonl...
        report(a[4:], a[1], a[2], a[3])
        return
    if a[0] == "verify":
        verify(a[1], a[2])
    elif a[0] == "mac-speed":
        out, mdir = a[1], a[2]
        order, passes, label, rest = "ABBAABBA", 3, "b9", []
        it = iter(a[3:])
        arms = {}
        for x in it:
            if x == "--order":
                order = next(it)
            elif x == "--passes":
                passes = int(next(it))
            elif x == "--label":
                label = next(it)
            elif re.match(r"^[AB]=", x):
                arms[x[0]] = x[2:]
            else:
                rest.append(x)
        specs = [(s.split("=", 1)[0], s.split("=", 1)[1]) for s in rest]
        mac_speed(out, mdir, arms, order, passes, specs, label)
    elif a[0] == "analyse":
        res = analyse_rows(load_passes(a[1:]))
        print(json.dumps(res, indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
