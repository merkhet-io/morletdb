#!/usr/bin/env python3
"""FD1: a full data disk during durable ingestion, without and with the steward (PROTOCOL.md, #340).

  python -I evaluation/full_disk_fd1/fd1.py ARM REP        # ARM: bare | bare-wal | steward

PostgreSQL runs on a fixed-size ext4 filesystem (600 MB image, loop-mounted in a privileged container of the image
built from ./Dockerfile) that also holds a 64 MB ballast file. A writer appends deterministic samples in durable
batches and records every acknowledged end. bare: it writes until an append fails; the container is stopped, the
ballast freed, the container started, and recovery is checked. steward: the steward runs beside PostgreSQL with a
100 MB reserve; the paced writer must be refused with SQLSTATE 53100 before the disk is full and PostgreSQL must keep
answering; the ballast is then freed while running, and appends must resume once the steward releases its hold.
After both: every acknowledged sample is stored and equals the generator, morlet_verify finds nothing, appends work.
"""
import hashlib
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "limits_l1"))
from l1 import generate  # noqa: E402  (deterministic samples by index)

IMAGE, NAME, VOL, PORT, COLL = "morletdb-fd1:local", "fd1-morletdb", "fd1-vol", 54370, "fd"
PASSWORD, STEWARD_PW = secrets.token_hex(16), secrets.token_hex(16)   # per run, never written anywhere
DSN = f"host=127.0.0.1 port={PORT} user=morlet dbname=morlet password={PASSWORD}"
INNER = f"host=localhost user=morlet_steward dbname=morlet password={STEWARD_PW}"
BATCH, CHUNK = 262_144, 4096
RESERVE = 100 << 20
PACE_S = 0.25                       # steward arm: one batch every 0.25 s, about 1 M samples/s


def sh(args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args[:4])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def connect(timeout=180):
    t = time.time()
    while time.time() - t < timeout:
        try:
            c = psycopg.connect(DSN, autocommit=True, connect_timeout=3)
            c.execute("SELECT 1")
            return c
        except psycopg.Error:
            time.sleep(0.5)
    return None


# bare-wal: no checkpoint before the disk is full (so every WAL segment needs new space); once less than 150 MB is
# free and the current 16 MB segment is past 15 MB, a filler leaves 2 MB: room for the next batch's data pages, not
# for the next WAL segment.
WAL_FIRST = ["-c", "max_wal_size=8GB", "-c", "checkpoint_timeout=1h"]
WAL_GAP, SEGMENT = 2 << 20, 16 << 20


def start_fresh(settings=()):
    sh(["docker", "rm", "-f", NAME], check=False)
    sh(["docker", "volume", "rm", VOL], check=False)
    sh(["docker", "run", "-d", "--name", NAME, "--privileged", "--cpus", "8", "--memory", "16g",
        "-v", f"{VOL}:/vol", "-p", f"127.0.0.1:{PORT}:5432", "-e", "POSTGRES_USER=morlet",
        "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", "POSTGRES_DB=morlet",
        "-e", "POSTGRES_INITDB_ARGS=--data-checksums", IMAGE, "postgres", "-c", "max_wal_size=1GB", *settings])
    c = connect()
    if c is None:
        raise RuntimeError("server did not start: " + sh(["docker", "logs", "--tail", "20", NAME], check=False))
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    c.execute(f"SELECT morlet_create_stream_collection('public','{COLL}')")
    c.execute(f"SELECT morlet_enable_chunk_summaries('{COLL}')")
    c.execute(f"SELECT morlet_start_recording('{COLL}','r',64000,'x','V','{{}}',512,256,'zscore','none',%s)",
              (100 / 32768,))
    return c


def df():
    out = sh(["docker", "exec", NAME, "df", "-B1", "/mnt/disk"], check=False).splitlines()
    if len(out) < 2:
        return None
    total, used, free = (int(x) for x in out[1].split()[1:4])
    return {"total": total, "used": used, "free": free}


def stored_end(c):
    return int(c.execute(f"SELECT (checkpoint->>'end')::bigint FROM {COLL} WHERE recording_id='r'").fetchone()[0])


def append(c, start):
    x = generate("adc", start, BATCH)
    with c.transaction():
        c.execute(f"SELECT morlet_append_chunks_raw('{COLL}','r',%s,%b,{CHUNK})", (start, x.astype("<f8").tobytes()))


def write_until_refused(c, paced, wal_first=False):
    """Append batches until one fails; returns the last acknowledged end and the error. wal_first: once less than
    150 MB is free, fill the disk up to WAL_GAP, so the next WAL segment, not a data page, finds no space."""
    acked, filled = 0, False
    while True:
        if wal_first and not filled:
            d = df()
            pos = int(c.execute("SELECT pg_current_wal_lsn() - '0/0'::pg_lsn").fetchone()[0])
            if d and d["free"] < 150 << 20 and pos % SEGMENT > SEGMENT - (1 << 20):
                sh(["docker", "exec", NAME, "fallocate", "-l", str(d["free"] - WAL_GAP), "/mnt/disk/filler"])
                filled = True
        t = time.perf_counter()
        try:
            append(c, acked)
        except psycopg.Error as e:
            return acked, {"sqlstate": getattr(e, "sqlstate", None),
                           "message": (e.diag.message_primary if getattr(e, "diag", None) else None) or str(e)[:300]}
        acked += BATCH
        if paced:
            time.sleep(max(0.0, PACE_S - (time.perf_counter() - t)))


def check_after(c, acked):
    """Every acknowledged sample stored and equal to the generator; morlet_verify clean; appends work again."""
    out, problems = {}, []
    end = stored_end(c)
    out["stored_end"] = end
    if end < acked:
        problems.append(f"lost acknowledged samples: stored end {end} < acknowledged end {acked}")
    if end % BATCH:
        problems.append(f"stored end {end} is not on a batch boundary")
    h, want = hashlib.sha256(), hashlib.sha256()
    for a in range(0, end, BATCH):
        b = min(end, a + BATCH)
        h.update(bytes(c.execute(f"SELECT morlet_read_interval_raw('{COLL}','r',%s,%s)", (a, b),
                                 binary=True).fetchone()[0]))
        want.update(generate("adc", a, b - a).astype("<f8").tobytes())
    if h.hexdigest() != want.hexdigest():
        problems.append("stored samples differ from the generator")
    bad = [r for r in c.execute(f"SELECT recording_id, problems FROM morlet_verify('{COLL}')") if r[1]]
    if bad:
        problems.append(f"morlet_verify: {bad[:3]}")
    try:
        for k in range(2):
            append(c, end + k * BATCH)
        out["appends_after"] = 2
    except psycopg.Error as e:
        problems.append(f"append after recovery failed: {str(e)[:200]}")
    out["problems"] = problems
    return out


def log_lines(words=("PANIC", "FATAL", "No space", "refused")):
    logs = subprocess.run(["docker", "logs", "--tail", "400", NAME], capture_output=True, text=True)
    return [x for x in (logs.stdout + logs.stderr).splitlines() if any(w in x for w in words)][-12:]


def arm_bare(res, wal_first=False):
    c = start_fresh(WAL_FIRST if wal_first else ())
    res["disk_at_start"] = df()
    t0 = time.time()
    acked, err = write_until_refused(c, paced=False, wal_first=wal_first)
    res.update(acked_end=acked, error=err, write_s=round(time.time() - t0, 1), disk_at_error=df())
    c.close()
    time.sleep(5)
    res["server_after_error"] = "answers" if connect(10) else "down"
    res["container_after_error"] = sh(["docker", "inspect", NAME, "--format", "{{.State.Status}}"], check=False)
    res["log_at_error"] = log_lines()
    sh(["docker", "stop", "-t", "30", NAME], check=False)
    sh(["docker", "run", "--rm", "-v", f"{VOL}:/vol", "alpine:3.20", "touch", "/vol/free-ballast"])
    t = time.time()
    sh(["docker", "start", NAME])
    c = connect(300)
    res["recovery_s"] = round(time.time() - t, 1) if c else None
    if c is None:
        res["problems"] = ["server did not come back after the ballast was freed"]
        res["log_after"] = log_lines()
        return
    res["disk_after_free"] = df()
    res.update(check_after(c, acked))


def arm_steward(res):
    c = start_fresh()
    py, st = "/opt/venv/bin/python", "/opt/steward/steward.py"
    sh(["docker", "exec", "-e", f"MORLET_STEWARD_PASSWORD={STEWARD_PW}", NAME, py, st, "init",
        "--dsn", f"host=localhost user=morlet dbname=morlet password={PASSWORD}"])
    sh(["docker", "exec", NAME, py, st, "grant", "--dsn", f"host=localhost user=morlet dbname=morlet password={PASSWORD}"])
    sh(["docker", "exec", "-d", NAME, "sh", "-c",
        f"{py} {st} run --dsn '{INNER}' --tick 1 --reserve-bytes {RESERVE} --reserve-fraction 0 "
        f"--data-path /mnt/disk/pgdata > /tmp/steward.log 2>&1"])
    res["disk_at_start"] = df()
    t0 = time.time()
    acked, err = write_until_refused(c, paced=True)
    res.update(acked_end=acked, error=err, write_s=round(time.time() - t0, 1), disk_at_error=df())
    res["refused_by_hold"] = err.get("sqlstate") == "53100" and "new samples are refused" in (err.get("message") or "")
    refused, up = 0, True
    for _ in range(20):                                  # 20 s: still refused, still answering, no PANIC
        time.sleep(1)
        try:
            append(c, acked)
            acked += BATCH
        except psycopg.Error as e:
            refused += "new samples are refused" in str(e)
        try:
            c.execute("SELECT 1")
        except psycopg.Error:
            up = False
    res.update(refused_while_held=refused, answered_while_held=up, disk_while_held=df(),
               panic_in_log=any("PANIC" in x for x in log_lines()))
    sh(["docker", "exec", NAME, "rm", "-f", "/mnt/disk/ballast"])
    t = time.time()
    resumed = None
    while time.time() - t < 90:                          # the steward releases its hold once space is free
        try:
            append(c, acked)
            acked += BATCH
            resumed = round(time.time() - t, 1)
            break
        except psycopg.Error:
            time.sleep(1)
    res["resumed_after_free_s"] = resumed
    res["steward_log"] = sh(["docker", "exec", NAME, "tail", "-5", "/tmp/steward.log"], check=False).splitlines()
    res.update(check_after(c, acked))
    if not res["refused_by_hold"]:
        res["problems"].append(f"the first refusal was not the steward's hold: {err}")
    if resumed is None:
        res["problems"].append("appends did not resume within 90 s after space was freed")
    if res["panic_in_log"]:
        res["problems"].append("PostgreSQL logged a PANIC")


def main():
    arm, rep = sys.argv[1], int(sys.argv[2])
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    res = {"arm": arm, "rep": rep, "image": IMAGE,
           "image_id": sh(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"]),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        {"bare": arm_bare, "bare-wal": lambda r: arm_bare(r, wal_first=True), "steward": arm_steward}[arm](res)
    except Exception as e:  # noqa: BLE001 - a crash of the harness is recorded
        res["harness_error"] = f"{type(e).__name__}: {e}"[:500]
        res["log"] = log_lines()
    finally:
        sh(["docker", "rm", "-f", NAME], check=False)
        sh(["docker", "volume", "rm", VOL], check=False)
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (out / f"{arm}_r{rep}.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("arm", "rep", "acked_end", "stored_end", "error", "recovery_s",
                                               "resumed_after_free_s", "problems", "harness_error")}, default=str))


if __name__ == "__main__":
    main()
