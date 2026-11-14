#!/bin/sh
# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
#
# One-shot setup of the compose deployment, as the database administrator: the replay's collection, the companion's
# login role and the companion schema (idempotent). Credentials come from the passfile; no password is on a command
# line.
set -eu
admin="host=127.0.0.1 port=54340 dbname=morlet user=morlet"
python - <<'PY'
import sys
sys.path.insert(0, "/app/enterprise/winccoa/replay")
import psycopg
from psycopg import sql
from writer import setup_collection
pw = open("/run/secrets/companion_password").read().strip()
with psycopg.connect("host=127.0.0.1 port=54340 dbname=morlet user=morlet") as c:
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    c.execute("CREATE SCHEMA IF NOT EXISTS plant")
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname = 'winccoa_companion'").fetchone() is None:
        c.execute(sql.SQL("CREATE ROLE winccoa_companion LOGIN PASSWORD {}").format(sql.Literal(pw)))
    c.commit()
    setup_collection(c, "plant.rig")
PY
python -m morlet_winccoa install --config /etc/morlet-winccoa/companion.toml --login-role winccoa_companion \
  --dsn "$admin"
