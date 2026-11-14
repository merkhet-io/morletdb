# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The companion schema (docs/winccoa.md decision 1, option (a)): observation, derivation and acquisition_end, with
row-level security that follows the acquisition, and the two group roles of decision 5.

Installed by the deploying administrator (who owns the tables), never by the companion's login role:

    python -m morlet_winccoa install --config companion.toml --login-role winccoa_companion

Idempotent: re-run it after adding a collection to the configuration, so that the policies and grants cover it.

The policy reads the documented companion table `<collection>_acq_channels` (docs/context-workflow.md); it does not
call the internal `morlet_acquisition_has_visible_channel`. Whether the policy may read that table directly is
core change 2 of docs/winccoa.md (proposed default, owner decision pending).
"""
from __future__ import annotations

import psycopg
from psycopg import sql

from .config import Config

POLICY = "morlet_winccoa_follows_acquisition"
TABLES = ("observation", "derivation", "acquisition_end")
REASONS = ("open", "outside_recording", "untimed", "bad_quality", "clock_exceeded", "inconsistent_start",
           "unchanged", "out_of_order", "unrepresentable", "rejected")
# Companion tables of a collection that an analyst reads (docs/access-control.md, "Roles"); probe tables are added
# by pattern.
READER_SUFFIXES = ("", "_chunks", "_segments", "_envelopes", "_ctx", "_ctx_segments", "_ctx_values", "_labels",
                   "_label_definitions", "_acq", "_acq_channels", "_rollups", "_episodes", "_ledger",
                   "_kept_summaries", "_memory_runs")

DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.observation (
  obs_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  collection text NOT NULL,
  acquisition_id text NOT NULL,
  asset text NOT NULL,
  dpe text,
  key text,
  value_json jsonb,
  value_text text,
  value_type text,
  source_ns bigint,
  source_status text NOT NULL CHECK (source_status IN ('ok','missing','unreadable')),
  quality jsonb,
  quality_status text NOT NULL CHECK (quality_status IN ('good','bad','uncertain','missing','unreadable')),
  observed_ns bigint NOT NULL,
  received_ns bigint NOT NULL,
  rtt_ns bigint CHECK (rtt_ns >= 0),
  kind text NOT NULL CHECK (kind IN ('change','snapshot','heartbeat','gap')),
  adapter_session text NOT NULL CHECK (adapter_session <> ''),
  seq bigint NOT NULL CHECK (seq >= 0),
  digest text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  recorded_by text NOT NULL DEFAULT current_user,
  UNIQUE (adapter_session, seq),
  CHECK ((kind IN ('change','snapshot')) = (key IS NOT NULL AND value_text IS NOT NULL AND value_type IS NOT NULL)),
  CHECK (kind <> 'heartbeat' OR key IS NULL),
  CHECK (quality_status <> 'missing' OR quality IS NULL),
  CHECK (quality IS NOT NULL OR quality_status IN ('missing','unreadable')),
  CHECK ((source_ns IS NULL) = (source_status <> 'ok'))
);
CREATE INDEX IF NOT EXISTS observation_acquisition ON {schema}.observation (collection, acquisition_id, obs_id);
CREATE TABLE IF NOT EXISTS {schema}.derivation (
  obs_id bigint PRIMARY KEY REFERENCES {schema}.observation (obs_id),
  collection text NOT NULL,
  acquisition_id text NOT NULL,
  key text NOT NULL,
  batch_id text,
  reason text CHECK (reason IN ({reasons})),
  time_basis text NOT NULL CHECK (time_basis IN ('source','observed')),
  t_start_ns bigint,
  t_end_ns bigint,
  closed_by bigint REFERENCES {schema}.observation (obs_id),
  channels text[] NOT NULL DEFAULT '{{}}',
  rows_added bigint NOT NULL DEFAULT 0,
  detail text NOT NULL DEFAULT '',
  derived_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((batch_id IS NULL) <> (reason IS NULL))
);
CREATE INDEX IF NOT EXISTS derivation_acquisition ON {schema}.derivation (collection, acquisition_id);
ALTER TABLE {schema}.derivation DROP CONSTRAINT IF EXISTS derivation_reason_check;
ALTER TABLE {schema}.derivation ADD CONSTRAINT derivation_reason_check CHECK (reason IN ({reasons}));
CREATE TABLE IF NOT EXISTS {schema}.acquisition_end (
  collection text NOT NULL,
  acquisition_id text NOT NULL,
  end_ns bigint NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  recorded_by text NOT NULL DEFAULT current_user,
  PRIMARY KEY (collection, acquisition_id)
);
"""


def _visible(cfg: Config, table: str) -> sql.Composable:
    """The policy condition: the row's acquisition has a channel the caller can see in its collection."""
    parts = []
    for coll in cfg.collections():
        s, t = coll.split(".", 1)
        parts.append(sql.SQL("({tbl}.collection = {name} AND EXISTS (SELECT 1 FROM {ch} c "
                             "WHERE c.acquisition_id = {tbl}.acquisition_id))").format(
            tbl=sql.Identifier(table), name=sql.Literal(coll), ch=sql.Identifier(s, t + "_acq_channels")))
    return sql.SQL(" OR ").join(parts) if parts else sql.SQL("false")


def _roles(conn: psycopg.Connection, cfg: Config, login_role: str | None) -> None:
    for role in (cfg.context_role, cfg.reader_role):
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    if login_role:
        conn.execute(sql.SQL("GRANT {}, {} TO {}").format(sql.Identifier(cfg.context_role),
                                                          sql.Identifier(cfg.reader_role),
                                                          sql.Identifier(login_role)))


def _collection_grants(conn: psycopg.Connection, cfg: Config, coll: str) -> None:
    s, t = coll.split(".", 1)
    ctx, rd = sql.Identifier(cfg.context_role), sql.Identifier(cfg.reader_role)
    for name in (f"{s}.{t}", f"{s}.{t}_acq", f"{s}.{t}_acq_channels", f"{s}.{t}_ctx"):
        if conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is None:
            raise RuntimeError(f"{name} does not exist: create the collection and run morlet_enable_context and "
                               "morlet_enable_acquisitions first")
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}, {}").format(sql.Identifier(s), ctx, rd))
    # morlet_add_context / morlet_add_acquisition_context / morlet_revise_context run as the caller: they read the
    # recordings, acquisitions and context, insert context rows and mark affected segments stale.
    for suffix in ("", "_acq", "_acq_channels", "_ctx", "_ctx_segments"):
        conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(s, t + suffix), ctx))
    conn.execute(sql.SQL("GRANT INSERT ON {} TO {}").format(sql.Identifier(s, t + "_ctx"), ctx))
    conn.execute(sql.SQL("GRANT UPDATE ON {} TO {}").format(sql.Identifier(s, t + "_ctx_segments"), ctx))
    # The analyst grants (docs/access-control.md): read and search, nothing else.
    rows = conn.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r' AND (c.relname = ANY(%s) OR c.relname ~ %s)""",
                        (s, [t + x for x in READER_SUFFIXES], "^" + t + r"_probes_[0-9]+$")).fetchall()
    for (rel,) in rows:
        conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(s, rel), rd))


def install(conn: psycopg.Connection, cfg: Config, login_role: str | None = None) -> None:
    """Create or update the schema, its policies and grants, in the caller's transaction (commit to keep)."""
    schema = sql.Identifier(cfg.schema)
    reasons = sql.SQL(",").join(sql.Literal(r) for r in REASONS)
    # Serialize concurrent installs.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('morlet-winccoa-install', 0))")
    conn.execute(sql.SQL(DDL).format(schema=schema, reasons=reasons))
    _roles(conn, cfg, login_role)
    ctx, rd = sql.Identifier(cfg.context_role), sql.Identifier(cfg.reader_role)
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM PUBLIC").format(schema))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}, {}").format(schema, ctx, rd))
    # Append-only for the companion: no UPDATE or DELETE on observations; derivation rows move from 'open' to their
    # outcome and record the channels a batch reached, so they need UPDATE.
    conn.execute(sql.SQL("GRANT SELECT, INSERT ON {}.observation, {}.acquisition_end TO {}").format(schema, schema, ctx))
    conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON {}.derivation TO {}").format(schema, ctx))
    conn.execute(sql.SQL("GRANT SELECT ON {}.observation, {}.derivation, {}.acquisition_end TO {}").format(
        schema, schema, schema, rd))
    for table in TABLES:
        ident = sql.Identifier(cfg.schema, table)
        cond = _visible(cfg, table)
        conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(ident))
        conn.execute(sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(sql.Identifier(POLICY), ident))
        conn.execute(sql.SQL("CREATE POLICY {} ON {} USING ({}) WITH CHECK ({})").format(
            sql.Identifier(POLICY), ident, cond, cond))
    for coll in cfg.collections():
        _collection_grants(conn, cfg, coll)


def installed(conn: psycopg.Connection, cfg: Config) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL",
                        (f"{cfg.schema}.observation",)).fetchone()[0]
