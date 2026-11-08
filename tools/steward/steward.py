#!/usr/bin/env python3
"""morlet-steward: keeps a MorletDB database healthy on an edge machine without a DBA (docs/steward-design.md).

  morlet-steward init   --dsn ...            schema, roles and grants (database owner, once)
  morlet-steward grant  --dsn ...            steward privileges on every collection (owner, after new collections)
  morlet-steward run    --dsn ... [--data-path DIR] [--once]
  morlet-steward status --dsn ...            the plain status, for people
  morlet-steward check  --dsn ...            exit 0 normal, 1 attention, 2 action required (health checks)
  morlet-steward reset JOB --dsn ...         close a job's circuit breaker (approver)
  morlet-steward plans --dsn ...             plans the steward or a person proposed, and their state
  morlet-steward approve|reject|apply ID     decide or apply a plan (a person with morlet_approver, not the author)
  morlet-steward propose-levels COLLECTION MOVES.json   a memory-level plan from a policy dry run

Phase 1: automatic upkeep (builders, verify, analyze) in one budgeted loop, gated by load and disk; the disk
ladder in time remaining; an audit row for every action; circuit breakers; the status view. It runs only jobs whose
output can be rebuilt and holds no privilege that removes stored samples.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

import psycopg
import psycopg.sql

import policy as P
import store as S

BUILDERS = ("envelopes", "chunk_summaries", "rollups", "context_values")


class Steward:
    def __init__(self, conn: psycopg.Connection, cfg: P.Config, data_path: str | None):
        self.conn, self.cfg, self.data_path = conn, cfg, data_path
        self.batch: dict[str, int] = {"envelopes": 64, "chunk_summaries": 256, "rollups": 64, "context_values": 64,
                                      "verify": 20}
        self.grids: dict[str, list[tuple[int, int]]] = {}
        self.grids_at = 0.0
        self.analyzed: dict[str, float] = {}
        self.stop = False

    # -- observation ---------------------------------------------------------------------------------------------
    def disk(self) -> P.DiskState:
        free = total = None
        if self.data_path:
            try:
                st = os.statvfs(self.data_path)
                free, total = st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize
            except OSError:
                pass
        try:
            S.record_disk(self.conn, S.db_bytes(self.conn), free, total)
            growth = P.growth_per_day(S.disk_history(self.conn))
        except psycopg.Error:
            return P.disk_state(self.cfg, P.Disk(None, None, None))
        return P.disk_state(self.cfg, P.Disk(total, free, growth))

    def backlog(self, cols: list[str]) -> dict[str, list]:
        """For each job, the work found: (collection, recording, end, grid) tuples, or tables to analyze."""
        # grids of new collections at once, of all collections again every hour
        if time.time() - self.grids_at > 3600:
            self.grids, self.grids_at = {}, time.time()
        for c in cols:
            if not self.grids.get(c):
                self.grids[c] = S.envelope_grids(self.conn, c)
        work: dict[str, list] = {j: [] for j in P.JOBS}
        for c in cols:
            for w, s in self.grids.get(c, []):
                for rid, end in S.candidates(self.conn, f"envelopes:{w}:{s}", c, 5):
                    work["envelopes"].append((c, rid, end, (w, s)))
            if S.summarized(self.conn, c):
                work["chunk_summaries"] += [(c, rid, end, None) for rid, end in S.candidates(self.conn, "chunk_summaries", c, 5)]
            if S.sibling(self.conn, c, "_rollups"):
                work["rollups"] += [(c, rid, end, None) for rid, end in S.candidates(self.conn, "rollups", c, 5)]
            work["context_values"] += [(c, rid, n, None) for rid, n in S.context_candidates(self.conn, c, 5)]
        # a table analyzed in the last hour waits (ANALYZE without privileges only warns and changes nothing)
        work["analyze"] = [t for t in S.analyze_candidates(self.conn, cols) if time.time() - self.analyzed.get(t, 0) > 3600]
        return work

    # -- one tick ------------------------------------------------------------------------------------------------
    def tick(self) -> str:
        cfg, conn = self.cfg, self.conn
        disk = self.disk()
        self.ingest_hold(disk)
        all_cols = S.collections(conn)
        cols = [c for c in all_cols if S.privileged(conn, c)]
        breakers = S.load_breakers(conn, P)
        load1 = os.getloadavg()[0]
        ok, why = P.may_work(cfg, load1, os.cpu_count() or 1, disk)
        note = why
        if ok:
            work = self.backlog(cols)
            due = [c for c in cols if S.verify_due(conn, c, cfg.verify_every_days)]
            job = P.next_job({j: bool(v) for j, v in work.items()}, breakers, bool(due))
            if job is None:
                note = "idle: nothing to do"
            else:
                note = self.run_job(job, work, due, breakers)
        self.propose_extension_update()
        open_plans = [(r[0], r[1], r[5]) for r in S.plans(conn) if r[7] is None and r[5] != "rejected"
                      and r[4] is not None and r[4].timestamp() > time.time()]
        problems, ages = S.verify_summary(conn)
        for c in cols:
            ages.setdefault(c, None)
        items = P.status_items(cfg, P.Observed(disk, breakers, problems, ages,
                                               [c for c in all_cols if c not in cols], open_plans))
        S.write_status(conn, items, cfg.tick_s, note)
        return note

    def propose_extension_update(self) -> None:
        """A newer pg_morlet installed on the server becomes a plan; the steward never updates anything itself."""
        v = S.extension_versions(self.conn)
        if not v or v[0] == v[1]:
            return
        payload = {"from": v[0], "to": v[1]}
        if S.open_plan(self.conn, "extension_update", payload) is None:
            pid = S.propose(self.conn, "extension_update", None, payload,
                            {"why": f"pg_morlet {v[1]} is installed on the server; the database uses {v[0]}",
                             "how": "ALTER EXTENSION pg_morlet UPDATE, in one transaction"})
            aid = S.start_action(self.conn, "propose", None, None, {"plan_id": pid} | payload)
            S.finish_action(self.conn, aid, "done")

    def ingest_hold(self, disk: P.DiskState) -> None:
        """At the disk reserve, new appends are refused with a reason (SQLSTATE 53100) so PostgreSQL never runs out
        of disk; once space is free again the steward releases the hold it set. A hold set by someone else stays."""
        conn = self.conn
        current = S.hold(conn)
        if disk.hold_ingest:
            reason = f"{disk.cause} (set by morlet-steward)"
            if current is None or current[0] != reason:
                aid = S.start_action(conn, "ingest_hold", None, None, {"reason": reason})
                try:
                    S.set_hold(conn, reason)
                    S.finish_action(conn, aid, "done", {"held": True})
                except psycopg.Error as e:
                    S.finish_action(conn, aid, "failed", error=str(e).splitlines()[0])
        elif current is not None and current[1] == S.STEWARD_ROLE and not disk.unknown:
            aid = S.start_action(conn, "ingest_hold", None, None, {"release": True})
            try:
                S.release_hold(conn)
                S.finish_action(conn, aid, "done", {"held": False})
            except psycopg.Error as e:
                S.finish_action(conn, aid, "failed", error=str(e).splitlines()[0])

    def run_job(self, job: str, work: dict, due: list[str], breakers: dict) -> str:
        conn, cfg = self.conn, self.cfg
        b = breakers.get(job, P.Breaker())
        if job == "verify":
            c = due[0]
            aid = S.start_action(conn, job, c, None, {"batch": self.batch[job]})
            t = time.time()
            try:
                res = S.verify_step(conn, c, self.batch[job], 10 * cfg.budget_s + 30)
            except psycopg.Error as e:
                return self.failed(job, aid, b, e)
            self.batch[job] = P.next_batch(cfg, self.batch[job], time.time() - t, 1, 1000)
            S.finish_action(conn, aid, "done", res)
            b.record(cfg, True)
            S.save_breaker(conn, job, b)
            return f"verify {c}: {res['recordings']} recordings"
        if job == "analyze":
            t = work["analyze"][0]
            aid = S.start_action(conn, job, t, None, {})
            try:
                conn.execute(f"ANALYZE {t}")
            except psycopg.Error as e:
                return self.failed(job, aid, b, e)
            self.analyzed[t] = time.time()
            S.finish_action(conn, aid, "done")
            b.record(cfg, True)
            S.save_breaker(conn, job, b)
            return f"analyze {t}"
        c, rid, end, grid = work[job][0]
        key = f"envelopes:{grid[0]}:{grid[1]}" if job == "envelopes" else job
        args = {"batch": self.batch[job]} | ({"window": grid[0], "stride": grid[1]} if grid else {})
        aid = S.start_action(conn, job, c, rid, args)
        t = time.time()
        try:
            n = S.call_builder(conn, job, c, rid, self.batch[job], grid, 10 * cfg.budget_s + 30)
        except psycopg.Error as e:
            return self.failed(job, aid, b, e)
        self.batch[job] = P.next_batch(cfg, self.batch[job], time.time() - t)
        if n == 0 and end is not None:
            S.mark_done(conn, key, c, rid, end)
        S.finish_action(conn, aid, "done" if n else "nothing_to_do", {"written": n})
        b.record(cfg, True)
        S.save_breaker(conn, job, b)
        return f"{job} {c}/{rid}: {n}"

    def failed(self, job: str, aid: int, b: P.Breaker, e: Exception) -> str:
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        S.finish_action(self.conn, aid, "failed", error=msg)
        b.record(self.cfg, False, msg)
        S.save_breaker(self.conn, job, b)
        return f"{job} failed: {msg}"


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, application_name="morlet-steward")


def cmd_run(a, cfg: P.Config) -> int:
    conn = connect(a.dsn)
    if not conn.execute("SELECT pg_try_advisory_lock(hashtext('morlet-steward'))").fetchone()[0]:
        print("another morlet-steward is running on this database", file=sys.stderr)
        return 3
    n = S.mark_interrupted(conn)
    if n:
        print(f"{n} actions of an earlier run were interrupted; their jobs run again", flush=True)
    data_path = a.data_path
    if data_path is None:
        try:
            data_path = conn.execute("SHOW data_directory").fetchone()[0]
        except psycopg.Error:
            data_path = None
    st = Steward(conn, cfg, data_path)

    def stop(*_):
        st.stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not st.stop:
        t = time.time()
        try:
            note = st.tick()
        except psycopg.OperationalError as e:      # lost connection: reconnect on the next tick
            print(f"database unavailable: {e}", file=sys.stderr, flush=True)
            time.sleep(cfg.tick_s)
            try:
                conn = connect(a.dsn)
                conn.execute("SELECT pg_advisory_lock(hashtext('morlet-steward'))")
                st.conn = conn
            except psycopg.Error:
                pass
            continue
        if a.verbose:
            print(time.strftime("%H:%M:%S"), note, flush=True)
        if a.once:
            break
        # back to back while there is work, otherwise one tick per tick_s
        if not note.startswith(("idle", "disk", "load")) and "failed" not in note:
            continue
        while not st.stop and time.time() - t < cfg.tick_s:
            time.sleep(min(1.0, cfg.tick_s))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="morlet-steward")
    ap.add_argument("command", choices=["init", "grant", "run", "status", "check", "reset", "plans", "approve",
                                        "reject", "apply", "propose-levels"])
    ap.add_argument("job", nargs="?", help="job (reset), plan id (approve, reject, apply) or collection (propose-levels)")
    ap.add_argument("moves", nargs="?", help="propose-levels: JSON file with the moves (a memory policy dry run)")
    ap.add_argument("--dsn", default=os.environ.get("MORLET_STEWARD_DSN", ""))
    ap.add_argument("--data-path")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--tick", type=float, default=30.0)
    ap.add_argument("--load-per-cpu", type=float, default=0.7, help="work only below this 1-minute load per CPU")
    ap.add_argument("--reserve-bytes", type=int, default=2 << 30, help="disk kept free: the larger of this and the share")
    ap.add_argument("--reserve-fraction", type=float, default=0.05)
    a = ap.parse_args(argv)
    cfg = P.Config(tick_s=a.tick, load_per_cpu=a.load_per_cpu, reserve_bytes=a.reserve_bytes,
                   reserve_fraction=a.reserve_fraction)
    if a.command == "run":
        return cmd_run(a, cfg)
    with connect(a.dsn) as conn:
        if a.command == "init":
            S.init(conn)
            password = os.environ.get("MORLET_STEWARD_PASSWORD")
            if password:
                conn.execute(psycopg.sql.SQL("ALTER ROLE morlet_steward LOGIN PASSWORD {}").format(
                    psycopg.sql.Literal(password)))
                print("morlet_steward schema ready; morlet_steward can log in")
            else:
                print("morlet_steward schema ready; give the steward a login: ALTER ROLE morlet_steward LOGIN PASSWORD ...")
            return 0
        if a.command == "grant":
            for c in S.grant(conn):
                print("granted", c)
            return 0
        if a.command == "reset":
            if a.job not in P.JOBS:
                print(f"job must be one of {', '.join(P.JOBS)}", file=sys.stderr)
                return 2
            conn.execute("UPDATE morlet_steward.breakers SET failures=0,open=false,last_error='',updated_at=now() "
                         "WHERE job=%s", (a.job,))
            print(f"{a.job}: breaker closed")
            return 0
        if a.command == "plans":
            for pid, kind, coll, created, valid, decision, by, applied, ev in S.plans(conn):
                state = f"applied {applied:%Y-%m-%d %H:%M}" if applied else (decision or "waiting")
                print(f"{pid:>5} {kind:<17} {coll or '-':<24} {state:<20} valid until {valid:%Y-%m-%d %H:%M}  {ev}")
            return 0
        if a.command in ("approve", "reject", "apply"):
            pid = int(a.job)
            try:
                if a.command == "apply":
                    print(json.dumps(conn.execute("SELECT morlet_steward.apply(%s)", (pid,)).fetchone()[0]))
                else:
                    conn.execute("SELECT morlet_steward.decide(%s,%s)", (pid, a.command == "approve"))
                    print(f"plan {pid}: {a.command}d")
            except psycopg.Error as e:
                print(f"plan {pid}: {str(e).splitlines()[0]}", file=sys.stderr)
                return 2
            return 0
        if a.command == "propose-levels":
            # qualified, because plans are applied with search_path=pg_catalog
            a.job = conn.execute("SELECT format('%%I.%%I',n.nspname,c.relname) FROM pg_class c JOIN pg_namespace n "
                                 "ON n.oid=c.relnamespace WHERE c.oid=%s::regclass", (a.job,)).fetchone()[0]
            with open(a.moves) as f:
                spec = json.load(f)
            moves = spec["moves"] if isinstance(spec, dict) else spec
            payload = {"moves": moves, "policy": spec.get("policy", "manual") if isinstance(spec, dict) else "manual",
                       "policy_version": str(spec.get("policy_version", "1")) if isinstance(spec, dict) else "1"}
            with conn.transaction(force_rollback=True):     # evidence: what a dry run of exactly these moves frees
                run = conn.execute("SELECT morlet_start_memory_run(%s::regclass,'plan-dry-run','1','{}',true)",
                                   (a.job,)).fetchone()[0]
                freed = conn.execute("SELECT coalesce(sum(bytes_freed),0),count(*) FROM morlet_set_level(%s::regclass,"
                                     "%s,%s::jsonb,true)", (a.job, run, json.dumps(moves))).fetchone()
            pid = S.propose(conn, "set_level", a.job, payload,
                            {"moves": len(moves), "bytes_freed": int(freed[0]), "dry_run_rows": int(freed[1])})
            print(f"plan {pid}: {len(moves)} moves, {int(freed[0])} bytes freed in a dry run; waits for approval")
            return 0
        level, rows = S.status(conn)
        if a.command == "status":
            print(level.replace("_", " ").upper())
            for lvl, key, cause, conseq, action, who, since in rows:
                print(f"- [{lvl.replace('_', ' ')}] {cause}. {conseq}. Action: {action} ({who}).")
            return 0
        return {"normal": 0, "attention": 1}.get(level, 2)


if __name__ == "__main__":
    sys.exit(main())
