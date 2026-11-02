"""Shared B3 machinery: containers with fixed limits, resource sampling, volume sizes, manifest checks."""
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
LIMITS = ["--cpus", "8", "--memory", "16g"]


def manifest():
    return json.loads((RESULTS / "manifest.json").read_text())


def sha(x):
    return hashlib.sha256(np.ascontiguousarray(x, dtype="<f8").tobytes()).hexdigest()


def sh(args, check=True, capture=True):
    r = subprocess.run(args, capture_output=capture, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip()[:400]}")
    return r.stdout


def fresh(name, image, volume, mount, ports, env=None, cmd=None):
    """Start a container alone on a new, empty volume, with the B3 limits."""
    sh(["docker", "rm", "-f", name], check=False)
    sh(["docker", "volume", "rm", volume], check=False)
    run(name, image, volume, mount, ports, env, cmd)


def run(name, image, volume, mount, ports, env=None, cmd=None):
    """(Re)create the container on an existing volume, with the B3 limits."""
    sh(["docker", "rm", "-f", name], check=False)
    args = ["docker", "run", "-d", "--name", name, *LIMITS, "-v", f"{volume}:{mount}"]
    for p in ports:
        host, _, inner = str(p).partition(":")
        args += ["-p", f"127.0.0.1:{host}:{inner or host}"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    sh(args + [image] + (cmd or []))


def stop(name):
    sh(["docker", "stop", "-t", "60", name], check=False)


def remove(name, volume=None):
    sh(["docker", "rm", "-f", name], check=False)
    if volume:
        sh(["docker", "volume", "rm", volume], check=False)


def volume_files(volume, image):
    """[(apparent bytes, allocated bytes, path)] of every file on a volume, read with a throwaway container of
    the same image while the server is stopped. Allocated = blocks x block size (files may be sparse)."""
    out = sh(["docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{volume}:/v", image, "-c",
              "cd /v && find . -type f -exec stat -c '%s %b %B %n' {} +"])
    rows = []
    for line in out.splitlines():
        if line.strip():
            a, b, bs, path = line.split(" ", 3)
            rows.append((int(a), int(b) * int(bs), path[2:]))
    return rows


class Stats:
    """`docker stats` every second while running: CPU percent and memory of one container."""

    def __init__(self, name):
        self.name, self.samples, self._stop = name, [], threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}};{{.MemUsage}}", self.name],
                                 capture_output=True, text=True).stdout.strip()
            if out:
                cpu, mem = out.split(";")
                used = mem.split("/")[0].strip()
                num, unit = float(used[:-3]), used[-3:]
                scale = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(unit, 1)
                self.samples.append((time.time(), float(cpu.rstrip("%")), num * scale))
            self._stop.wait(1.0)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self.t.join(timeout=5)

    def summary(self):
        if not self.samples:
            return {}
        cpu = [c for _, c, _ in self.samples]
        mem = [m for _, _, m in self.samples]
        return {"cpu_percent_mean": round(float(np.mean(cpu)), 1), "cpu_percent_p95": round(float(np.percentile(cpu, 95)), 1),
                "memory_peak_mib": round(max(mem), 1), "samples": len(self.samples)}


def pcts(xs):
    xs = np.asarray(xs, dtype=float)
    return {"n": int(xs.size), "p50_ms": round(float(np.percentile(xs, 50)) * 1000, 3),
            "p95_ms": round(float(np.percentile(xs, 95)) * 1000, 3), "max_ms": round(float(xs.max()) * 1000, 3)}


def read_plan(m, seed=20261102):
    """Fixed read intervals (PROTOCOL.md "C"): acquisition index and offset per query, chosen once by the seed."""
    rng = np.random.default_rng(seed)
    acq = m["acquisitions"]
    n_of = lambda a, c="vibration_1": a["channels"][c]["samples"]
    plan = {}
    def pairs(length, count, pool):
        out = []
        for _ in range(count):
            a = acq[int(rng.choice(pool))]
            out.append((a["k"], int(rng.integers(0, n_of(a) - length))))
        return out
    allk = list(range(len(acq)))
    plan["R1"] = {"length": 4096, "channels": ["vibration_1"], "pairs": pairs(4096, 20, allk)}
    plan["R2"] = {"length": 128000, "channels": ["vibration_1"], "pairs": pairs(128000, 20, allk)}
    plan["R3"] = {"length": 64000, "channels": ["vibration_1", "phase_current_1", "phase_current_2"],
                  "pairs": pairs(64000, 20, allk)}
    plan["R4_recent"] = {"length": 4096, "channels": ["vibration_1"], "pairs": pairs(4096, 20, allk[-5:])}
    plan["R4_older"] = {"length": 4096, "channels": ["vibration_1"], "pairs": pairs(4096, 20, allk[:5])}
    plan["R5"] = {"length": 4096, "channels": ["vibration_1"], "pairs": pairs(4096, 200, allk), "threads": 4}
    return plan
