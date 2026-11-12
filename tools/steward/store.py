"""The steward's database side (docs/steward-design.md): its schema (audit, progress, breakers, verify cursor, disk
history, status), roles and grants, collection discovery, backlog queries and the calls of the extension's builders.
Every change to collections goes through the extension's SQL functions with the steward's own privileges, which
allow building derived data and nothing that removes stored samples."""
from __future__ import annotations

import json
import time

import psycopg
from psycopg import sql

import policy as P

SCHEMA_VERSION = 1
STEWARD_ROLE, APPROVER_ROLE = "morlet_steward", "morlet_approver"

DDL = """
CREATE SCHEMA IF NOT EXISTS morlet_steward;
CREATE TABLE IF NOT EXISTS morlet_steward.meta(key text PRIMARY KEY, value jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS morlet_steward.actions(
  action_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job text NOT NULL, collection text, recording_id text, args jsonb NOT NULL DEFAULT '{}',
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  outcome text NOT NULL DEFAULT 'started'
    CHECK(outcome IN ('started','done','nothing_to_do','failed','interrupted')),
  result jsonb, error text);
CREATE INDEX IF NOT EXISTS actions_started ON morlet_steward.actions(started_at);
CREATE TABLE IF NOT EXISTS morlet_steward.progress(
  job text NOT NULL, collection text NOT NULL, recording_id text NOT NULL, done_end bigint NOT NULL,
  PRIMARY KEY(job,collection,recording_id));
CREATE TABLE IF NOT EXISTS morlet_steward.breakers(
  job text PRIMARY KEY, failures integer NOT NULL DEFAULT 0, open boolean NOT NULL DEFAULT false,
  last_error text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS morlet_steward.verify_cursor(
  collection text PRIMARY KEY, after_recording text, pass_problems integer NOT NULL DEFAULT 0,
  last_pass_problems integer, last_pass_finished_at timestamptz);
CREATE TABLE IF NOT EXISTS morlet_steward.disk_samples(
  at timestamptz PRIMARY KEY, db_bytes bigint NOT NULL, free_bytes bigint, total_bytes bigint);
CREATE TABLE IF NOT EXISTS morlet_steward.status_items(
  key text PRIMARY KEY, level text NOT NULL CHECK(level IN ('normal','attention','action_required')),
  cause text NOT NULL, consequence text NOT NULL, action text NOT NULL, who text NOT NULL,
  since timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS morlet_steward.heartbeat(
  id integer PRIMARY KEY CHECK(id=1), at timestamptz NOT NULL, tick_s float8 NOT NULL, note text);
-- What people and HMIs read: the steward's items, and an item of its own when it stopped reporting.
CREATE OR REPLACE VIEW morlet_steward.status AS
  SELECT key,level,cause,consequence,action,who,since FROM morlet_steward.status_items
  UNION ALL
  SELECT 'steward','action_required',
    CASE WHEN h.at IS NULL THEN 'the steward has never run'
      ELSE 'the steward has not reported since '||to_char(h.at,'YYYY-MM-DD HH24:MI TZ') END,
    'upkeep and disk checks have stopped','restart the morlet-steward service','controls engineer',coalesce(h.at,now())
  FROM (SELECT 1) one LEFT JOIN morlet_steward.heartbeat h ON true
  WHERE h.at IS NULL OR h.at<now()-make_interval(secs=>greatest(3*h.tick_s,120));
-- Plans (P3): anything that removes or changes stored samples, or updates the extension, is a plan the steward writes
-- and a person approves and applies. The rules are enforced here, not in the steward: the payload is tied to its hash;
-- approval needs morlet_approver and someone other than the plan's author and records the hash it approved; apply
-- runs as the person (their privileges), refuses anything unapproved, changed, expired or applied, and applies the
-- stored payload in one transaction.
CREATE TABLE IF NOT EXISTS morlet_steward.plans(
  plan_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL CHECK(kind IN ('set_level','extension_update')),
  collection text, payload jsonb NOT NULL, evidence jsonb NOT NULL DEFAULT '{}',
  sha256 text NOT NULL CHECK(sha256=encode(sha256(convert_to(payload::text,'UTF8')),'hex')),
  created_by text NOT NULL DEFAULT current_user, created_at timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz NOT NULL DEFAULT now()+interval '24 hours',
  decision text CHECK(decision IN ('approved','rejected')), decided_by text, decided_at timestamptz,
  approved_sha256 text, applied_by text, applied_at timestamptz, outcome jsonb);
CREATE OR REPLACE FUNCTION morlet_steward.decide(plan_id bigint,approve boolean)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p record;
BEGIN
  IF NOT pg_has_role(current_user,'morlet_approver','MEMBER') THEN
    RAISE EXCEPTION 'deciding plans needs the morlet_approver role';
  END IF;
  SELECT * INTO p FROM morlet_steward.plans x WHERE x.plan_id=decide.plan_id FOR UPDATE;
  IF p.plan_id IS NULL THEN RAISE EXCEPTION 'plan % does not exist',plan_id; END IF;
  IF p.decision IS NOT NULL THEN RAISE EXCEPTION 'plan % is already %',plan_id,p.decision; END IF;
  IF p.created_by=current_user THEN RAISE EXCEPTION 'a plan is decided by someone other than its author'; END IF;
  IF approve AND now()>p.valid_until THEN RAISE EXCEPTION 'plan % expired at %; its evidence is stale',plan_id,p.valid_until; END IF;
  UPDATE morlet_steward.plans x SET decision=CASE WHEN approve THEN 'approved' ELSE 'rejected' END,
    decided_by=current_user,decided_at=now(),approved_sha256=CASE WHEN approve THEN p.sha256 END
  WHERE x.plan_id=decide.plan_id;
END $$;
CREATE OR REPLACE FUNCTION morlet_steward.apply(plan_id bigint)
RETURNS jsonb LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p record; run bigint; moved jsonb; result jsonb;
BEGIN
  IF NOT pg_has_role(current_user,'morlet_approver','MEMBER') THEN
    RAISE EXCEPTION 'applying plans needs the morlet_approver role';
  END IF;
  SELECT * INTO p FROM morlet_steward.plans x WHERE x.plan_id=apply.plan_id FOR UPDATE;
  IF p.plan_id IS NULL THEN RAISE EXCEPTION 'plan % does not exist',plan_id; END IF;
  IF p.decision IS DISTINCT FROM 'approved' THEN RAISE EXCEPTION 'plan % is not approved',plan_id; END IF;
  IF p.applied_at IS NOT NULL THEN RAISE EXCEPTION 'plan % was applied at %',plan_id,p.applied_at; END IF;
  IF p.sha256 IS DISTINCT FROM p.approved_sha256 THEN RAISE EXCEPTION 'plan % changed after it was approved',plan_id; END IF;
  IF now()>p.valid_until THEN RAISE EXCEPTION 'plan % expired at %',plan_id,p.valid_until; END IF;
  IF p.kind='set_level' THEN
    run:=public.morlet_start_memory_run(p.collection::regclass,coalesce(p.payload->>'policy','plan'),
      coalesce(p.payload->>'policy_version','1'),jsonb_build_object('plan_id',p.plan_id),false);
    SELECT coalesce(jsonb_agg(to_jsonb(m)),'[]') INTO moved
      FROM public.morlet_set_level(p.collection::regclass,run,p.payload->'moves',false) m;
    PERFORM public.morlet_finish_memory_run(p.collection::regclass,run,'finished',jsonb_build_object('plan_id',p.plan_id));
    result:=jsonb_build_object('run_id',run,'moves',moved);
  ELSE
    EXECUTE format('ALTER EXTENSION pg_morlet UPDATE TO %L',p.payload->>'to');
    result:=jsonb_build_object('extension',(SELECT extversion FROM pg_extension WHERE extname='pg_morlet'));
  END IF;
  UPDATE morlet_steward.plans x SET applied_by=current_user,applied_at=now(),outcome=result WHERE x.plan_id=apply.plan_id;
  RETURN result;
END $$;
-- The plan rules hold at the table, for direct SQL as for decide() and apply() (#439): a new plan is undecided and
-- unapplied, with its author, creation time and 24-hour validity set here; a plan's identity (kind, collection,
-- payload, evidence, hash, author, validity) never changes; a decision is made once, by an approver who is not the
-- author, before expiry, and records who decided and the hash approved; an application is made once, by an approver,
-- of an approved, unchanged, unexpired plan; plans are not deleted. The table's owner (who runs init and could drop
-- this trigger) is exempt, for maintenance.
CREATE OR REPLACE FUNCTION morlet_steward.plans_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE owner name;
BEGIN
  SELECT pg_get_userbyid(c.relowner) INTO owner FROM pg_class c WHERE c.oid=TG_RELID;
  IF current_user=owner THEN
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
  END IF;
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'plans are kept: a plan is never deleted' USING ERRCODE='insufficient_privilege';
  END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.decision IS NOT NULL OR NEW.decided_by IS NOT NULL OR NEW.decided_at IS NOT NULL
       OR NEW.approved_sha256 IS NOT NULL OR NEW.applied_by IS NOT NULL OR NEW.applied_at IS NOT NULL
       OR NEW.outcome IS NOT NULL THEN
      RAISE EXCEPTION 'a new plan is undecided and unapplied' USING ERRCODE='insufficient_privilege';
    END IF;
    NEW.created_by:=current_user; NEW.created_at:=now(); NEW.valid_until:=now()+interval '24 hours';
    RETURN NEW;
  END IF;
  IF (NEW.plan_id,NEW.kind,NEW.collection,NEW.payload,NEW.evidence,NEW.sha256,NEW.created_by,NEW.created_at,NEW.valid_until)
     IS DISTINCT FROM (OLD.plan_id,OLD.kind,OLD.collection,OLD.payload,OLD.evidence,OLD.sha256,OLD.created_by,
                       OLD.created_at,OLD.valid_until) THEN
    RAISE EXCEPTION 'plan %: its kind, target, payload, evidence, author and validity do not change',OLD.plan_id
      USING ERRCODE='insufficient_privilege';
  END IF;
  IF OLD.decision IS NULL AND NEW.decision IS NOT NULL THEN
    IF NOT pg_has_role(current_user,'morlet_approver','MEMBER') THEN
      RAISE EXCEPTION 'deciding plans needs the morlet_approver role' USING ERRCODE='insufficient_privilege';
    END IF;
    IF OLD.created_by=current_user THEN
      RAISE EXCEPTION 'a plan is decided by someone other than its author' USING ERRCODE='insufficient_privilege';
    END IF;
    IF NEW.decision='approved' AND now()>OLD.valid_until THEN
      RAISE EXCEPTION 'plan % expired at %; its evidence is stale',OLD.plan_id,OLD.valid_until;
    END IF;
    NEW.decided_by:=current_user; NEW.decided_at:=now();
    NEW.approved_sha256:=CASE WHEN NEW.decision='approved' THEN OLD.sha256 END;
  ELSIF (NEW.decision,NEW.decided_by,NEW.decided_at,NEW.approved_sha256)
        IS DISTINCT FROM (OLD.decision,OLD.decided_by,OLD.decided_at,OLD.approved_sha256) THEN
    RAISE EXCEPTION 'plan %: a decision is made once',OLD.plan_id USING ERRCODE='insufficient_privilege';
  END IF;
  IF OLD.applied_at IS NULL AND NEW.applied_at IS NOT NULL THEN
    IF NOT pg_has_role(current_user,'morlet_approver','MEMBER') THEN
      RAISE EXCEPTION 'applying plans needs the morlet_approver role' USING ERRCODE='insufficient_privilege';
    END IF;
    IF NEW.decision IS DISTINCT FROM 'approved' OR OLD.sha256 IS DISTINCT FROM NEW.approved_sha256
       OR now()>OLD.valid_until THEN
      RAISE EXCEPTION 'plan % is not an approved, unchanged, unexpired plan',OLD.plan_id
        USING ERRCODE='insufficient_privilege';
    END IF;
    NEW.applied_by:=current_user; NEW.applied_at:=now();
  ELSIF (NEW.applied_by,NEW.applied_at,NEW.outcome) IS DISTINCT FROM (OLD.applied_by,OLD.applied_at,OLD.outcome) THEN
    RAISE EXCEPTION 'plan %: it is applied once',OLD.plan_id USING ERRCODE='insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS plans_guard ON morlet_steward.plans;
CREATE TRIGGER plans_guard BEFORE INSERT OR UPDATE OR DELETE ON morlet_steward.plans
  FOR EACH ROW EXECUTE FUNCTION morlet_steward.plans_guard();
CREATE OR REPLACE VIEW morlet_steward.overall AS
  SELECT coalesce((SELECT level FROM morlet_steward.status
    ORDER BY array_position(ARRAY['normal','attention','action_required'],level) DESC LIMIT 1),'normal') AS level,
    (SELECT count(*) FROM morlet_steward.status) AS items;
"""


def init(conn: psycopg.Connection) -> None:
    """Schema, roles and grants (run once by the database owner). Roles are created when missing; the login and
    password of morlet_steward are the administrator's (ALTER ROLE morlet_steward LOGIN PASSWORD ...)."""
    with conn.transaction():
        for role in (STEWARD_ROLE, APPROVER_ROLE):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        conn.execute(DDL)
        conn.execute("INSERT INTO morlet_steward.meta VALUES ('schema_version',%s) ON CONFLICT (key) DO UPDATE "
                     "SET value=EXCLUDED.value", (json.dumps(SCHEMA_VERSION),))
        conn.execute(f"GRANT USAGE ON SCHEMA morlet_steward TO {STEWARD_ROLE}, {APPROVER_ROLE}, PUBLIC")
        conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA morlet_steward TO {STEWARD_ROLE}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA morlet_steward TO {APPROVER_ROLE}")
        # plans: the steward proposes and reads; only approvers decide and apply (through the functions above)
        conn.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON morlet_steward.plans FROM {STEWARD_ROLE}")
        conn.execute(f"GRANT UPDATE (decision, decided_by, decided_at, approved_sha256, applied_by, applied_at, outcome) "
                     f"ON morlet_steward.plans TO {APPROVER_ROLE}")
        conn.execute(f"GRANT INSERT ON morlet_steward.plans TO {APPROVER_ROLE}")   # people propose too; another decides
        conn.execute("REVOKE ALL ON FUNCTION morlet_steward.decide(bigint,boolean), morlet_steward.apply(bigint) FROM PUBLIC")
        conn.execute(f"GRANT EXECUTE ON FUNCTION morlet_steward.decide(bigint,boolean), morlet_steward.apply(bigint) "
                     f"TO {APPROVER_ROLE}")
        conn.execute(f"GRANT UPDATE ON morlet_steward.breakers TO {APPROVER_ROLE}")
        conn.execute("GRANT SELECT ON morlet_steward.status, morlet_steward.overall TO PUBLIC")
        conn.execute(f"GRANT pg_monitor TO {STEWARD_ROLE}")   # database and WAL sizes
        if conn.execute("SELECT to_regclass('public.morlet_ingest_hold')").fetchone()[0]:
            conn.execute(f"GRANT INSERT, UPDATE, DELETE ON public.morlet_ingest_hold TO {STEWARD_ROLE}")


def collections(conn: psycopg.Connection) -> list[str]:
    """Stream collections with encoded chunks: a table with recording_id and checkpoint and a <name>_chunks sibling
    with an encoded column. Qualified names, sorted."""
    rows = conn.execute("""
      SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema','morlet_steward')
        AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='checkpoint' AND NOT a.attisdropped)
        AND EXISTS(SELECT 1 FROM pg_class k JOIN pg_attribute a ON a.attrelid=k.oid
                   WHERE k.relnamespace=c.relnamespace AND k.relname=c.relname||'_chunks' AND a.attname='encoded'
                     AND NOT a.attisdropped)
      ORDER BY 1""").fetchall()
    return [r[0] for r in rows]


def sibling(conn: psycopg.Connection, collection: str, suffix: str) -> str | None:
    """The sibling table of a collection (for example _envelopes, or "" for the collection itself), if it exists."""
    schema, name = split(collection)
    return conn.execute("SELECT to_regclass(format('%%I.%%I',%s::text,%s::text))::text",
                        (schema, name + suffix)).fetchone()[0]


def split(collection: str) -> tuple[str, str]:
    """Schema and table of a qualified name as format('%I.%I') prints it."""
    parts, cur, quoted, i = [], "", False, 0
    while i < len(collection):
        ch = collection[i]
        if ch == '"':
            if quoted and i + 1 < len(collection) and collection[i + 1] == '"':
                cur += '"'
                i += 1
            else:
                quoted = not quoted
        elif ch == "." and not quoted:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return (parts[0], parts[1]) if len(parts) == 2 else ("public", parts[0])


DERIVED = ("_envelopes", "_rollups", "_ctx_values")
READ = ("", "_chunks", "_segments", "_envelopes", "_rollups", "_ctx_segments", "_ctx_values", "_episodes",
        "_kept_summaries", "_acq", "_acq_channels")


def grant(conn: psycopg.Connection) -> list[str]:
    """Privileges for the steward on every collection (run by the owner, again after new collections appear):
    read and analyze the collection; write derived data (envelopes, probe rows, roll-ups, context values) and chunk
    summaries. No DELETE or UPDATE of chunk samples. Returns the collections granted."""
    done = []
    with conn.transaction():
        for c in collections(conn):
            for suffix in READ:
                t = sibling(conn, c, suffix)
                if t:
                    # MAINTAIN (PostgreSQL 17+): ANALYZE and VACUUM, which change no row
                    conn.execute(f"GRANT SELECT, MAINTAIN ON {t} TO {STEWARD_ROLE}")
            for suffix in DERIVED:
                t = sibling(conn, c, suffix)
                if t:
                    conn.execute(f"GRANT INSERT, UPDATE, DELETE ON {t} TO {STEWARD_ROLE}")
            schema, name = split(c)
            for (t,) in conn.execute("SELECT format('%%I.%%I',schemaname,tablename) FROM pg_tables WHERE schemaname=%s "
                                     "AND tablename LIKE %s", (schema, name.replace("_", "\\_") + "\\_probes\\_%")):
                conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {STEWARD_ROLE}")
            chunks = sibling(conn, c, "_chunks")
            if conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid=%s::regclass AND attname='summary' "
                            "AND NOT attisdropped", (chunks,)).fetchone():
                conn.execute(f"GRANT UPDATE (summary) ON {chunks} TO {STEWARD_ROLE}")
            done.append(c)
    return done


def privileged(conn: psycopg.Connection, collection: str) -> bool:
    """Whether the current role can read the collection (the minimum to maintain it)."""
    return bool(conn.execute("SELECT has_table_privilege(%s::regclass,'SELECT')", (collection,)).fetchone()[0])


def envelope_grids(conn: psycopg.Connection, collection: str) -> list[tuple[int, int]]:
    """(window, stride) pairs the collection maintains: every window size present in its envelopes, with its most
    common stride (half the window when no recording has two windows yet), and the window and stride the recordings
    declare in their checkpoints (recordings with extractor 'none' declare no envelopes). The checkpoints are what a
    new collection has before its first envelope: without them the steward would never start building (soak S1)."""
    env = sibling(conn, collection, "_envelopes")
    if not env:
        return []
    rows = conn.execute(f"""
      SELECT w, coalesce(mode() WITHIN GROUP (ORDER BY d), w/2) FROM (
        SELECT window_size w, start_sample-lag(start_sample) OVER (PARTITION BY recording_id,window_size
          ORDER BY start_sample) d FROM {env}) x
      WHERE d IS NULL OR d>0 GROUP BY w ORDER BY w""").fetchall()
    grids = {int(w): int(s) for w, s in rows}
    declared = conn.execute(f"""
      SELECT DISTINCT (checkpoint->>'window')::int, (checkpoint->>'stride')::int FROM {collection}
      WHERE checkpoint ? 'window' AND checkpoint ? 'stride'
        AND coalesce(checkpoint->>'extractor_version', '') <> 'none'""").fetchall()
    for w, s in declared:
        if w and s and s > 0:
            grids.setdefault(int(w), int(s))
    return sorted(grids.items())


def candidates(conn: psycopg.Connection, job: str, collection: str, limit: int = 20) -> list[tuple[str, int]]:
    """Recordings whose stored end changed since the job last finished them: (recording_id, end)."""
    rows = conn.execute(f"""
      SELECT r.recording_id,(r.checkpoint->>'end')::bigint FROM {collection} r
      LEFT JOIN morlet_steward.progress p ON p.job=%s AND p.collection=%s AND p.recording_id=r.recording_id
      WHERE p.done_end IS DISTINCT FROM (r.checkpoint->>'end')::bigint
      ORDER BY r.recording_id LIMIT %s""", (job, collection, limit)).fetchall()
    return [(r[0], int(r[1] or 0)) for r in rows]


def mark_done(conn: psycopg.Connection, job: str, collection: str, recording_id: str, end: int) -> None:
    conn.execute("""INSERT INTO morlet_steward.progress VALUES (%s,%s,%s,%s)
      ON CONFLICT (job,collection,recording_id) DO UPDATE SET done_end=EXCLUDED.done_end""",
                 (job, collection, recording_id, end))


def context_candidates(conn: psycopg.Connection, collection: str, limit: int = 20) -> list[tuple[str, int]]:
    """Recordings with segments that have no values: (recording_id, segment count). A recording the builder could
    not complete (for example compacted samples) is skipped until its segment count changes."""
    seg, val = sibling(conn, collection, "_ctx_segments"), sibling(conn, collection, "_ctx_values")
    if not seg or not val:
        return []
    rows = conn.execute(f"""SELECT x.recording_id,x.n FROM (
        SELECT s.recording_id,(SELECT count(*) FROM {seg} a WHERE a.recording_id=s.recording_id) n
        FROM {seg} s WHERE NOT s.stale AND NOT EXISTS(SELECT 1 FROM {val} v WHERE v.segment_id=s.segment_id)
        GROUP BY s.recording_id) x
      LEFT JOIN morlet_steward.progress p ON p.job='context_values' AND p.collection=%s AND p.recording_id=x.recording_id
      WHERE p.done_end IS DISTINCT FROM x.n ORDER BY 1 LIMIT %s""", (collection, limit)).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def summarized(conn: psycopg.Connection, collection: str) -> bool:
    return bool(conn.execute("SELECT summarized FROM morlet_stream_relations(%s::regclass)", (collection,)).fetchone()[0])


def analyze_candidates(conn: psycopg.Connection, collections_: list[str]) -> list[str]:
    """Collection tables changed by more than 10% (and 1,000 rows) since their last analyze."""
    names = []
    for c in collections_:
        schema, name = split(c)
        names += [(schema, name + s) for s in READ]
    if not names:
        return []
    rows = conn.execute("""SELECT format('%%I.%%I',schemaname,relname) FROM pg_stat_user_tables
      WHERE (schemaname,relname) IN (SELECT * FROM unnest(%s::text[],%s::text[]))
        AND n_mod_since_analyze>greatest(1000,0.1*n_live_tup) ORDER BY n_mod_since_analyze DESC""",
                        ([n[0] for n in names], [n[1] for n in names])).fetchall()
    return [r[0] for r in rows]


# ---- actions -------------------------------------------------------------------------------------------------------

def start_action(conn, job: str, collection: str | None, recording_id: str | None, args: dict) -> int:
    return conn.execute("INSERT INTO morlet_steward.actions(job,collection,recording_id,args) VALUES (%s,%s,%s,%s) "
                        "RETURNING action_id", (job, collection, recording_id, json.dumps(args))).fetchone()[0]


def finish_action(conn, action_id: int, outcome: str, result: dict | None = None, error: str | None = None) -> None:
    conn.execute("UPDATE morlet_steward.actions SET finished_at=now(),outcome=%s,result=%s,error=%s "
                 "WHERE action_id=%s", (outcome, None if result is None else json.dumps(result), error, action_id))


def mark_interrupted(conn) -> int:
    """Actions left 'started' by a steward that died; their jobs are idempotent and simply run again."""
    return conn.execute("UPDATE morlet_steward.actions SET outcome='interrupted',finished_at=now() "
                        "WHERE outcome='started'").rowcount


def call_builder(conn, job: str, collection: str, recording_id: str, batch: int, grid: tuple[int, int] | None,
                 timeout_s: float) -> int:
    """One bounded builder call in its own transaction; returns what it wrote (0: nothing left)."""
    with conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
        if job == "envelopes":
            w, s = grid
            q, args = "SELECT morlet_build_envelopes(%s::regclass,%s,%s,%s,%s)", (collection, recording_id, w, s, batch)
        elif job == "chunk_summaries":
            q, args = "SELECT morlet_build_chunk_summaries(%s::regclass,%s,%s)", (collection, recording_id, batch)
        elif job == "rollups":
            q, args = "SELECT morlet_build_rollups(%s::regclass,%s,%s)", (collection, recording_id, batch)
        elif job == "context_values":
            q, args = "SELECT morlet_build_context_values(%s::regclass,%s,%s)", (collection, recording_id, batch)
        else:
            raise ValueError(job)
        return int(conn.execute(q, args).fetchone()[0] or 0)


def verify_step(conn, collection: str, batch: int, timeout_s: float) -> dict:
    """One verify batch from the stored cursor; at the end of a pass the cursor restarts and the pass is recorded."""
    row = conn.execute("SELECT after_recording,pass_problems FROM morlet_steward.verify_cursor WHERE collection=%s",
                       (collection,)).fetchone()
    after, problems = (row[0], row[1]) if row else (None, 0)
    with conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
        rows = conn.execute("SELECT recording_id,cardinality(problems),problems FROM morlet_verify(%s::regclass,%s,%s)",
                            (collection, after, batch)).fetchall()
    split = [(r[0], *P.integrity_problems(r[2] or [])) for r in rows]
    bad = [(rid, damage[:5]) for rid, damage, _pending in split if damage]
    pending = sum(n for _rid, _damage, n in split)   # summaries not built yet: backlog, not damage
    problems += len(bad)
    finished = len(rows) < batch
    if finished:
        conn.execute("""INSERT INTO morlet_steward.verify_cursor VALUES (%s,NULL,0,%s,now())
          ON CONFLICT (collection) DO UPDATE SET after_recording=NULL,pass_problems=0,last_pass_problems=%s,
            last_pass_finished_at=now()""", (collection, problems, problems))
    else:
        conn.execute("""INSERT INTO morlet_steward.verify_cursor(collection,after_recording,pass_problems) VALUES (%s,%s,%s)
          ON CONFLICT (collection) DO UPDATE SET after_recording=EXCLUDED.after_recording,
            pass_problems=EXCLUDED.pass_problems""", (collection, rows[-1][0], problems))
    return {"recordings": len(rows), "with_problems": bad, "summaries_pending": pending, "pass_finished": finished}


def verify_due(conn, collection: str, every_days: float) -> bool:
    row = conn.execute("SELECT after_recording,last_pass_finished_at FROM morlet_steward.verify_cursor "
                       "WHERE collection=%s", (collection,)).fetchone()
    if row is None or row[0] is not None or row[1] is None:
        return True
    return bool(conn.execute("SELECT %s < now()-make_interval(secs=>%s)", (row[1], every_days * 86400)).fetchone()[0])


def verify_summary(conn) -> tuple[dict[str, int], dict[str, float | None]]:
    rows = conn.execute("SELECT collection,coalesce(last_pass_problems,0),extract(epoch FROM now()-last_pass_finished_at)"
                        "/86400 FROM morlet_steward.verify_cursor").fetchall()
    return {r[0]: int(r[1]) for r in rows}, {r[0]: None if r[2] is None else float(r[2]) for r in rows}


# ---- plans (#287, P3) ----------------------------------------------------------------------------------------------

def propose(conn, kind: str, collection: str | None, payload: dict, evidence: dict) -> int:
    """Writes a plan; the hash ties approval to exactly this payload. Returns its id."""
    return conn.execute("""INSERT INTO morlet_steward.plans(kind,collection,payload,evidence,sha256)
      SELECT %s,%s,p,%s,encode(sha256(convert_to(p::text,'UTF8')),'hex') FROM (SELECT %s::jsonb p) x
      RETURNING plan_id""", (kind, collection, json.dumps(evidence), json.dumps(payload))).fetchone()[0]


def open_plan(conn, kind: str, payload: dict) -> int | None:
    """An undecided or approved, unapplied, unexpired plan with this payload."""
    r = conn.execute("""SELECT plan_id FROM morlet_steward.plans WHERE kind=%s AND payload=%s::jsonb
      AND applied_at IS NULL AND decision IS DISTINCT FROM 'rejected' AND now()<=valid_until""",
                     (kind, json.dumps(payload))).fetchone()
    return r[0] if r else None


def plans(conn) -> list[tuple]:
    return conn.execute("""SELECT plan_id,kind,collection,created_at,valid_until,decision,decided_by,applied_at,evidence
      FROM morlet_steward.plans ORDER BY plan_id DESC LIMIT 50""").fetchall()


def extension_versions(conn) -> tuple[str, str] | None:
    """(installed, available default) of pg_morlet."""
    r = conn.execute("SELECT installed_version,default_version FROM pg_available_extensions WHERE name='pg_morlet'").fetchone()
    return (r[0], r[1]) if r and r[0] else None


# ---- ingest hold (#287, P2) ---------------------------------------------------------------------------------------

def hold(conn) -> tuple[str, str] | None:
    """(reason, set_by) of the current ingest hold, or None; None also when the extension has no hold yet."""
    if not conn.execute("SELECT to_regclass('public.morlet_ingest_hold')").fetchone()[0]:
        return None
    r = conn.execute("SELECT reason,set_by FROM public.morlet_ingest_hold").fetchone()
    return (r[0], r[1]) if r else None


def set_hold(conn, reason: str) -> bool:
    return bool(conn.execute("SELECT morlet_set_ingest_hold(%s)", (reason,)).fetchone()[0])


def release_hold(conn) -> bool:
    return bool(conn.execute("SELECT morlet_release_ingest_hold()").fetchone()[0])


# ---- disk, breakers, status ----------------------------------------------------------------------------------------

def db_bytes(conn) -> int:
    n = int(conn.execute("SELECT pg_database_size(current_database())").fetchone()[0])
    try:
        with conn.transaction():
            n += int(conn.execute("SELECT coalesce(sum(size),0) FROM pg_ls_waldir()").fetchone()[0])
    except psycopg.Error:
        pass   # without pg_monitor the WAL is not counted
    return n


def record_disk(conn, db: int, free: int | None, total: int | None, every_s: float = 3600) -> None:
    conn.execute("""INSERT INTO morlet_steward.disk_samples
      SELECT now(),%s,%s,%s WHERE NOT EXISTS(SELECT 1 FROM morlet_steward.disk_samples
        WHERE at>now()-make_interval(secs=>%s))""", (db, free, total, every_s))
    conn.execute("DELETE FROM morlet_steward.disk_samples WHERE at<now()-interval '7 days'")


def disk_history(conn) -> list[tuple[float, int]]:
    return [(float(r[0]), int(r[1])) for r in conn.execute(
        "SELECT extract(epoch FROM at),db_bytes FROM morlet_steward.disk_samples ORDER BY at").fetchall()]


def load_breakers(conn, policy) -> dict:
    return {r[0]: policy.Breaker(r[1], r[2], r[3]) for r in conn.execute(
        "SELECT job,failures,open,last_error FROM morlet_steward.breakers").fetchall()}


def save_breaker(conn, job: str, b) -> None:
    conn.execute("""INSERT INTO morlet_steward.breakers VALUES (%s,%s,%s,%s,now())
      ON CONFLICT (job) DO UPDATE SET failures=EXCLUDED.failures,open=EXCLUDED.open,last_error=EXCLUDED.last_error,
        updated_at=now()""", (job, b.failures, b.open, b.last_error))


def write_status(conn, items, tick_s: float, note: str = "") -> None:
    """Replaces the status items in one transaction; an item that stays keeps its 'since'."""
    with conn.transaction():
        keys = [i.key for i in items]
        conn.execute("DELETE FROM morlet_steward.status_items WHERE NOT key=ANY(%s)", (keys,))
        for i in items:
            conn.execute("""INSERT INTO morlet_steward.status_items(key,level,cause,consequence,action,who)
              VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (key) DO UPDATE SET
                since=CASE WHEN morlet_steward.status_items.level=EXCLUDED.level THEN morlet_steward.status_items.since
                  ELSE now() END,
                level=EXCLUDED.level,cause=EXCLUDED.cause,consequence=EXCLUDED.consequence,action=EXCLUDED.action,
                who=EXCLUDED.who,updated_at=now()""", (i.key, i.level, i.cause, i.consequence, i.action, i.who))
        conn.execute("""INSERT INTO morlet_steward.heartbeat VALUES (1,now(),%s,%s)
          ON CONFLICT (id) DO UPDATE SET at=now(),tick_s=EXCLUDED.tick_s,note=EXCLUDED.note""", (tick_s, note))


def status(conn) -> tuple[str, list[tuple]]:
    level = conn.execute("SELECT level FROM morlet_steward.overall").fetchone()[0]
    rows = conn.execute("SELECT level,key,cause,consequence,action,who,since FROM morlet_steward.status "
                        "ORDER BY array_position(ARRAY['action_required','attention','normal'],level),key").fetchall()
    return level, rows


def now() -> float:
    return time.time()
