#!/usr/bin/env python3
"""Study B9 on the Raspberry Pi 3B+ (PROTOCOL.md section 6.B). Runs on the Pi host (Python 3.9, standard library).

  python3 -I b9pi.py speed OUT.jsonl SAMPLER_DIR MACHINE_DIR A=BIN B=BIN [--order ABBAABBA] [--passes 3] SPEC...
  python3 -I b9pi.py sample SECONDS OUT.tsv          (the sampler on its own, for the dry run)

SPEC is name=chunk file, relative to the directory mounted at /w (this script's working directory). Each pass is
`docker run --rm --network none --cpus 1` of the pinned image running `BIN speed 1 FILE`. Before every pass the
host must show: 1-minute load < 0.5, SoC temperature < 60 C, get_throttled bits 0-3 clear, no other container
running and no process above 10% CPU; the runner waits at most 30 minutes. During every pass a sampler records
temperature, ARM clock and get_throttled once a second. A pass is invalid if a sample reaches 60.0 C, any of bits
0-3 is set, or a sticky bit (16-19) appears between the readings before and after it. A block with an invalid pass
is void and redone (three attempts); a block whose start condition is not met in 30 minutes is set aside and
retried once after the others.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

IMAGE = "sha256:06001cf262e0684d505218be89ef045e23cc3270af43a127d4ab94a4a78e1344"
WAIT_MAX_S = int(os.environ.get("B9_WAIT_MAX_S", "1800"))
ATTEMPTS = 3


def sh(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True).stdout


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def temp():
    return float(sh("vcgencmd measure_temp").strip().split("=")[1].split("'")[0])


def throttled():
    return int(sh("vcgencmd get_throttled").strip().split("=")[1], 16)


def clock():
    return int(sh("vcgencmd measure_clock arm").strip().split("=")[1])


def busy():
    """Processes above 10% CPU in top's second iteration (the first is since boot)."""
    out = sh("top -b -n 2 -d 1 -w 200")
    last = out.split("PID USER")[-1].splitlines()[1:]
    res = []
    for line in last:
        p = line.split(None, 11)
        if len(p) < 12:
            continue
        try:
            cpu = float(p[8])
        except ValueError:
            continue
        if cpu > 10.0 and p[11].strip() not in ("top",):
            res.append({"pid": p[0], "cpu": cpu, "command": p[11].strip()})
    return res


def condition():
    load = float(open("/proc/loadavg").read().split()[0])
    t, th = temp(), throttled()
    containers = [c for c in sh("docker ps -q").split() if c]
    b = busy()
    c = {"load1": load, "temp": t, "throttled": hex(th), "containers": containers, "busy": b}
    c["ok"] = load < 0.5 and t < 60.0 and th & 0xF == 0 and not containers and not b
    return c


class Sampler(threading.Thread):
    def __init__(self, path):
        super().__init__(daemon=True)
        self.path, self.stop, self.rows = path, threading.Event(), []

    def run(self):
        with open(self.path, "w") as f:
            f.write("epoch\ttemp_c\tarm_hz\tthrottled\n")
            nxt = time.time()
            while not self.stop.is_set():
                row = (round(time.time(), 2), temp(), clock(), throttled())
                self.rows.append(row)
                f.write("%s\t%s\t%s\t%s\n" % (row[0], row[1], row[2], hex(row[3])))
                f.flush()
                nxt += 1.0
                self.stop.wait(max(0.0, nxt - time.time()))


def snapshot(path):
    txt = []
    for cmd in ("date", "uname -a", "uptime", "free -m", "docker ps -a --format '{{.Names}} {{.Status}}'",
                "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor", "vcgencmd measure_temp",
                "vcgencmd measure_clock arm", "vcgencmd get_throttled"):
        txt.append("$ %s\n%s" % (cmd, sh(cmd)))
    with open(path, "w") as f:
        f.write("\n".join(txt))


def wait_condition():
    t0 = time.time()
    while True:
        c = condition()
        if c["ok"]:
            c["waited_s"] = round(time.time() - t0, 1)
            return c
        if time.time() - t0 > WAIT_MAX_S:
            return None
        time.sleep(20)


def run_pass(binary, path):
    cmd = ["docker", "run", "--rm", "--network", "none", "--cpus", "1", "-v", os.getcwd() + ":/w:ro", "-w", "/w",
           IMAGE, binary, "speed", "1", path]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if p.returncode != 0:
        return None, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1]), None


def speed(out, sdir, mdir, arms, order, passes, specs, label):
    for d in (sdir, mdir):
        os.makedirs(d, exist_ok=True)
    bins = {k: (v, sha_file(v)) for k, v in arms.items()}
    queue, set_aside, retried = list(specs), [], set()
    with open(out, "a") as f:
        def rec(d):
            f.write(json.dumps(d) + "\n")
            f.flush()

        while queue:
            name, path = queue.pop(0)
            attempt, measured, timed_out = 0, False, False
            while attempt < ATTEMPTS and not measured:
                attempt += 1
                tag = "%s-%s-attempt%d" % (label, name, attempt)
                snapshot(os.path.join(mdir, tag + "-before.txt"))
                void = None
                for run, arm in enumerate(order, 1):
                    for pi in range(passes):
                        c = wait_condition()
                        if c is None:
                            timed_out = True
                            break
                        sticky0 = throttled()
                        sp = os.path.join(sdir, "%s-run%d-pass%d.tsv" % (tag, run, pi))
                        s = Sampler(sp)
                        s.start()
                        t0, s0 = time.time(), now()
                        res, err = run_pass(bins[arm][0], path)
                        t1, s1 = time.time(), now()
                        s.stop.set()
                        s.join()
                        sticky1 = throttled()
                        rows = s.rows
                        ths = sorted(set(r[3] for r in rows))
                        smp = {"n": len(rows), "max_temp": max(r[1] for r in rows) if rows else None,
                               "min_arm_hz": min(r[2] for r in rows) if rows else None,
                               "throttled_values": [hex(x) for x in ths],
                               "sticky_before": hex(sticky0), "sticky_after": hex(sticky1)}
                        reason = None
                        new_sticky = (sticky1 & ~sticky0) & 0xF0000
                        if res is None:
                            reason = "harness error: %s" % err
                        elif smp["max_temp"] is not None and smp["max_temp"] >= 60.0:
                            reason = "temperature %.1f C" % smp["max_temp"]
                        elif any(x & 0xF for x in ths) or sticky1 & 0xF:
                            reason = "get_throttled bits 0-3 set: %s" % smp["throttled_values"]
                        elif new_sticky:
                            reason = "sticky bit appeared: %s -> %s" % (hex(sticky0), hex(sticky1))
                        rec({"machine": "pi", "label": label, "input": name, "file": path, "attempt": attempt,
                             "run": run, "arm": arm, "binary": bins[arm][0], "binary_sha256": bins[arm][1],
                             "pass": pi, "start": s0, "end": s1, "seconds": round(t1 - t0, 3), "condition": c,
                             "sampler": smp, "sampler_file": os.path.basename(sp), "valid": reason is None,
                             "invalid_reason": reason, "result": res})
                        if res is None and err and "round trip" in err:
                            raise SystemExit("B9 stops: round-trip mismatch on %s (%s)" % (name, arm))
                        if reason:
                            void = reason
                            break
                    if void or timed_out:
                        break
                snapshot(os.path.join(mdir, tag + "-after.txt"))
                if timed_out:
                    rec({"machine": "pi", "label": label, "input": name, "attempt": attempt, "block": "set aside",
                         "reason": "start condition not met within 30 minutes"})
                    if name in retried:
                        rec({"machine": "pi", "label": label, "input": name, "block": "not measured"})
                    else:
                        retried.add(name)
                        set_aside.append((name, path))
                    break
                if void:
                    rec({"machine": "pi", "label": label, "input": name, "attempt": attempt, "block": "void",
                         "reason": void})
                    continue
                measured = True
                rec({"machine": "pi", "label": label, "input": name, "attempt": attempt, "block": "complete"})
            if not measured and not timed_out:
                rec({"machine": "pi", "label": label, "input": name, "block": "not measured",
                     "reason": "three void attempts"})
            if not queue and set_aside:
                queue, set_aside = set_aside, []
    print("DONE")


def main():
    a = sys.argv[1:]
    if a[0] == "sample":
        s = Sampler(a[2])
        s.start()
        time.sleep(float(a[1]))
        s.stop.set()
        s.join()
        print(json.dumps({"samples": len(s.rows)}))
        return
    if a[0] != "speed":
        raise SystemExit(__doc__)
    out, sdir, mdir = a[1], a[2], a[3]
    order, passes, label, arms, rest = "ABBAABBA", 3, "b9", {}, []
    it = iter(a[4:])
    for x in it:
        if x == "--order":
            order = next(it)
        elif x == "--passes":
            passes = int(next(it))
        elif x == "--label":
            label = next(it)
        elif x[:2] in ("A=", "B="):
            arms[x[0]] = x[2:]
        else:
            rest.append(x)
    speed(out, sdir, mdir, arms, order, passes, [tuple(s.split("=", 1)) for s in rest], label)


if __name__ == "__main__":
    main()
