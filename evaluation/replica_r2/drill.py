#!/usr/bin/env python3
"""Replica and failover drill R2 (#192): a streaming replica, then the primary killed during ingestion and the
replica promoted. Runs with Docker on any machine (and in CI):

  python evaluation/replica_r2/drill.py --image ghcr.io/merkhet-io/morletdb:<tag> --out result.json

Twice, once with a synchronous and once with an asynchronous standby (plain PostgreSQL streaming replication, the
setup in docs/operations.md; no cluster manager):

1. A primary (the image, data checksums) and a replica made with `pg_basebackup -R` on its own data volume.
2. Phase A: batches of deterministic samples with one context row each (`power_cut_p1.writer`), envelopes built.
   When the replica has replayed everything, it must hold exactly what the primary holds: every sample, the
   interval statistics, the context rows, `morlet_verify` without problems, and envelope search returning the same
   matches and distances on both.
3. Failover: a writer keeps committing batches on the primary and counts the acknowledged ones; after a random
   moment the primary container is killed (SIGKILL) and the replica promoted with `pg_promote()`.
4. The new primary is checked against what was acknowledged: synchronous, every acknowledged batch must be there
   (pass criterion); asynchronous, the number of acknowledged batches lost is measured and reported (the loss
   window), not judged. Either way what is there must be consistent (same checks as in 2), and ingestion must
   continue on the new primary from its stored end.
Exit status 1 on any problem.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "power_cut_p1"))
sys.path.insert(0, str(HERE.parents[1] / "restore_r1"))
from drill import BATCH, C, check, connect, docker, write  # noqa: E402

P = "r2drill"
NET = f"{P}-net"
PRIMARY_PORT, REPLICA_PORT = 55442, 55443
SEARCH = ("SELECT recording_id, start_sample, round(distance::numeric, 9) "
          f"FROM morlet_search_envelope_interval('{C}', 'pc', %s, %s, '{{}}', 10)")


def run_db(name: str, image: str, port: int, extra: list[str]) -> None:
    docker("run", "-d", "--name", f"{P}-{name}", "--network", NET, "--network-alias", name,
           "-e", "POSTGRES_USER=morlet", "-e", "POSTGRES_PASSWORD=drill", "-e", "POSTGRES_DB=morlet",
           "-e", "POSTGRES_INITDB_ARGS=--data-checksums", "-v", f"{P}-{name}:/var/lib/postgresql/data",
           "-p", f"127.0.0.1:{port}:5432", image, "postgres", "-c", "max_wal_size=256MB", "-c", "wal_compression=on",
           *extra, capture=True)


def make_replica(image: str) -> None:
    docker("volume", "create", f"{P}-replica", capture=True)
    docker("run", "--rm", "--network", NET, "-e", "PGPASSWORD=drill", "-v", f"{P}-replica:/d",
           "--entrypoint", "sh", image, "-c",
           "pg_basebackup -h primary -U morlet -D /d/pgdata -X stream -R -c fast"
           " && chown -R postgres:postgres /d && chmod 700 /d/pgdata", capture=True)
    # The image keeps PGDATA at /var/lib/postgresql/data; the backup sits in pgdata below the volume root.
    docker("run", "-d", "--name", f"{P}-replica", "--network", NET, "--user", "postgres",
           "-e", "PGDATA=/var/lib/postgresql/data/pgdata", "-v", f"{P}-replica:/var/lib/postgresql/data",
           "-p", f"127.0.0.1:{REPLICA_PORT}:5432", image, "postgres", capture=True)


def caught_up(primary, replica, timeout: float = 120) -> None:
    lsn = primary.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
    t0 = time.time()
    while time.time() - t0 < timeout:
        if replica.execute("SELECT pg_last_wal_replay_lsn() >= %s::pg_lsn", (lsn,)).fetchone()[0]:
            return
        time.sleep(0.5)
    raise RuntimeError("replica did not catch up")


def stored_batches(conn) -> int:
    end = conn.execute(f"SELECT (checkpoint->>'end')::bigint FROM {C} WHERE recording_id='pc'").fetchone()[0]
    return int(end) // BATCH


def scenario(image: str, mode: str, batches: int, rng: random.Random) -> dict:
    from writer import setup
    cleanup()
    docker("network", "create", NET, capture=True)
    run_db("primary", image, PRIMARY_PORT, [])
    primary = connect(PRIMARY_PORT)
    docker("exec", f"{P}-primary", "sh", "-c",
           'echo "host replication morlet all scram-sha-256" >> "$PGDATA/pg_hba.conf"')
    primary.execute("SELECT pg_reload_conf()")
    setup(primary, C)
    version = primary.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
    make_replica(image)
    replica = connect(REPLICA_PORT)
    if mode == "sync":   # only now: the image's init server would wait for a standby that does not exist yet
        primary.execute("ALTER SYSTEM SET synchronous_standby_names = '*'")
        primary.execute("SELECT pg_reload_conf()")
    for _ in range(60):
        st = primary.execute("SELECT sync_state FROM pg_stat_replication").fetchone()
        if st and st[0] == ("sync" if mode == "sync" else "async"):
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"replica not streaming in {mode} mode")
    res: dict = {"mode": mode}

    # 2. The replica holds exactly what the primary holds.
    b = write(primary, 0, batches)
    while primary.execute(f"SELECT morlet_build_envelopes('{C}','pc',2048,512,1000)").fetchone()[0]:
        pass
    caught_up(primary, replica)
    res["replica_problems"] = check(replica, b, version)
    windows = [(s, s + 2048) for s in (0, BATCH * batches // 3, BATCH * batches - 4096)]
    diffs = [w for w in windows
             if primary.execute(SEARCH, w).fetchall() != replica.execute(SEARCH, w).fetchall()]
    if diffs:
        res["replica_problems"].append(f"search differs for {diffs}")
    res["replica_batches"] = b

    # 3. Failover during ingestion.
    acked = [b]
    stop = threading.Event()

    def writer() -> None:
        conn = connect(PRIMARY_PORT)
        n = acked[0]
        try:
            while not stop.is_set():
                write(conn, n, 1)
                n += 1
                acked[0] = n       # acknowledged: COMMIT returned
        except Exception:  # noqa: BLE001 - the primary dies under the writer
            pass

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    time.sleep(rng.uniform(5, 15))
    docker("kill", f"{P}-primary", capture=True)
    t_kill = time.time()
    stop.set()
    t.join(30)
    primary.close()
    replica.execute("SELECT pg_promote(true, 60)")
    while replica.execute("SELECT pg_is_in_recovery()").fetchone()[0]:
        time.sleep(0.2)
    res["promote_s"] = round(time.time() - t_kill, 2)

    # 4. The new primary against what was acknowledged.
    have = stored_batches(replica)
    res.update(acked_batches=acked[0], stored_batches=have, lost_acknowledged=max(0, acked[0] - have))
    res["failover_problems"] = check(replica, have, version)
    if mode == "sync" and have < acked[0]:
        res["failover_problems"].append(f"{acked[0] - have} acknowledged batches lost with a synchronous standby")
    after = write(replica, have, 20)
    res["continued_problems"] = check(replica, after, version)
    res["continued_batches"] = after
    replica.close()
    res["pass"] = not (res["replica_problems"] or res["failover_problems"] or res["continued_problems"])
    return res


def cleanup() -> None:
    for c in ("primary", "replica"):
        docker("rm", "-fv", f"{P}-{c}", check=False, capture=True)
        docker("volume", "rm", "-f", f"{P}-{c}", check=False, capture=True)
    docker("network", "rm", NET, check=False, capture=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--batches", type=int, default=120)
    ap.add_argument("--seed", type=int, default=192)
    ap.add_argument("--out")
    ap.add_argument("--keep", action="store_true", help="leave the containers and volumes for inspection")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    out: dict = {"image": a.image, "batches": a.batches, "seed": a.seed}
    t0 = time.time()
    try:
        for mode in ("sync", "async"):
            out[mode] = scenario(a.image, mode, a.batches, rng)
    except Exception as exc:  # noqa: BLE001 - reported
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        out["total_s"] = round(time.time() - t0, 1)
        if not a.keep:
            cleanup()
    out["pass"] = "error" not in out and all(out.get(m, {}).get("pass") for m in ("sync", "async"))
    print(json.dumps(out, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
