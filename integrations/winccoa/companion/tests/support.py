# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Test support: a disposable MorletDB (PostgreSQL with pg_morlet) and per-test companion environments.

By default the tests start the pinned release image in Docker on a free loopback port, under a container named
`winccoa-test-<random>`, and remove it when the test process exits. Passwords and tokens are generated at test time
and kept in a passfile with mode 0600 in a temporary directory; no DSN carries a password.

    MORLET_WINCCOA_IMAGE   image to start (default ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1)

Without Docker the database tests fail; they are never skipped.
"""
from __future__ import annotations

import atexit
import dataclasses
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE.parents[1] / "replay"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from morlet_winccoa import config as cfgmod  # noqa: E402
from morlet_winccoa import schema as schemamod  # noqa: E402
from morlet_winccoa.server import Companion, make_server  # noqa: E402

IMAGE = os.environ.get("MORLET_WINCCOA_IMAGE", "ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1")
SPEED, LOAD = "System1:Rig1.speed", "System1:Rig1.load"


class _Database:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="winccoa-test-"))
        os.chmod(self.tmp, 0o700)
        self.passfile = self.tmp / "pgpass"
        self.passfile.touch(mode=0o600)
        os.environ["PGPASSFILE"] = str(self.passfile)
        self.name = f"winccoa-test-{uuid.uuid4().hex[:10]}"
        password = secrets.token_hex(24)
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.name, "--memory", "1g", "--cpus", "2",
                        "-e", "POSTGRES_USER=morlet", "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_DB=morlet",
                        "-p", "127.0.0.1::5432", IMAGE], check=True, capture_output=True)
        atexit.register(self.remove)
        out = subprocess.run(["docker", "port", self.name, "5432/tcp"], check=True, capture_output=True,
                             text=True).stdout
        self.port = int(out.strip().splitlines()[0].rsplit(":", 1)[1])
        self.add_password("morlet", password)
        self.admin_dsn = f"host=127.0.0.1 port={self.port} dbname=morlet user=morlet"
        deadline = time.monotonic() + 90
        ok = 0
        while True:   # the entrypoint restarts the server after initialisation: wait for two successes in a row
            try:
                with psycopg.connect(self.admin_dsn, connect_timeout=2) as c:
                    c.execute("SELECT 1")
                ok += 1
                if ok >= 2:
                    break
                time.sleep(1.0)
            except psycopg.Error:
                ok = 0
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
            self.sql_version = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_morlet'").fetchone()[0]

    def add_password(self, user: str, password: str) -> None:
        with open(self.passfile, "a") as f:
            f.write(f"127.0.0.1:*:*:{user}:{password}\n")

    def dsn(self, user: str) -> str:
        return f"host=127.0.0.1 port={self.port} dbname=morlet user={user}"

    def admin(self, autocommit: bool = False) -> psycopg.Connection:
        return psycopg.connect(self.admin_dsn, autocommit=autocommit)

    def pause(self) -> None:
        subprocess.run(["docker", "pause", self.name], check=True, capture_output=True)

    def unpause(self) -> None:
        subprocess.run(["docker", "unpause", self.name], check=True, capture_output=True)

    def remove(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


_db: _Database | None = None
_lock = threading.Lock()


def database() -> _Database:
    global _db
    with _lock:
        if _db is None:
            _db = _Database()
        return _db


def new_role(db: _Database, prefix: str, login: bool = True) -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    password = secrets.token_hex(24)
    with db.admin(autocommit=True) as c:
        if login:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(name), sql.Literal(password)))
        else:
            c.execute(f'CREATE ROLE "{name}" NOLOGIN')
    if login:
        db.add_password(name, password)
    return name


class Env:
    """One collection, one companion schema with its roles, and a login role for the companion."""

    def __init__(self, db: _Database, *, collection_table: str = "rig", install: bool = True, rls: bool = False,
                 datapoints: tuple = (), clock: cfgmod.Clock | None = None, **limits):
        self.db = db
        h = uuid.uuid4().hex[:8]
        self.hex = h
        self.coll_schema = f"plant_{h}"
        self.collection = f"{self.coll_schema}.{collection_table}"
        self.login = new_role(db, "wc_login")
        dps = (cfgmod.Datapoint(SPEED, "rig-1", "speed"), cfgmod.Datapoint(LOAD, "rig-1", "load")) + tuple(datapoints)
        self.tmp = Path(tempfile.mkdtemp(prefix="winccoa-env-", dir=db.tmp))
        self.token = secrets.token_hex(32)
        self.token_file = self.tmp / "token"
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.token + "\n")
        self.cfg = cfgmod.validate(cfgmod.Config(
            dsn=db.dsn(self.login), token_file=str(self.token_file), listen_port=0, schema=f"winccoa_{h}",
            context_role=f"wc_ctx_{h}", reader_role=f"wc_rd_{h}",
            limits=dataclasses.replace(cfgmod.Limits(connect_timeout_s=2), **limits), clock=clock or cfgmod.Clock(),
            assets=(cfgmod.Asset("rig-1", self.collection, "rig-1"),), datapoints=dps))
        from writer import setup_collection
        with db.admin() as c:
            c.execute(f'CREATE SCHEMA "{self.coll_schema}"')
            c.commit()
            setup_collection(c, self.collection)
            if rls:
                c.execute(f"SELECT morlet_enable_row_security('{self.collection}'::regclass)")
            if install:
                schemamod.install(c, self.cfg, self.login)
            c.commit()
        self.companion = None
        self.server = None

    def install(self, login: str | None = None) -> None:
        with self.db.admin() as c:
            schemamod.install(c, self.cfg, login or self.login)
            c.commit()

    def writer(self) -> psycopg.Connection:
        return self.db.admin()

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.cfg.dsn, autocommit=True)

    def start(self, cfg: cfgmod.Config | None = None):
        from adapter_sim import Adapter, Client
        self.companion = Companion(cfg or self.cfg)
        self.server = make_server(self.companion)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = Client("127.0.0.1", self.port, self.token)
        return Adapter(self.client)

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.companion is not None:
            self.companion.stop()
            self.companion = None

    def derive(self, acquisition: str):
        from morlet_winccoa import derive
        with self.connect() as c:
            return derive.derive(c, self.cfg, self.collection, acquisition)

    def drop(self) -> None:
        self.stop()
        with self.db.admin(autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{self.coll_schema}" CASCADE')
            c.execute(f'DROP SCHEMA IF EXISTS "{self.cfg.schema}" CASCADE')
            for role in (self.login, self.cfg.context_role, self.cfg.reader_role):
                if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                    c.execute(f'DROP OWNED BY "{role}"')
                    c.execute(f'DROP ROLE "{role}"')


def ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def expected_samples(rate: int, offset_ns: int, t0: int, t1: int) -> tuple[int, int]:
    """morlet_time_to_samples, independently: [ceil((t0-offset)*rate/1e9), ceil((t1-offset)*rate/1e9)), clamped at 0
    (integer arithmetic; the rates here are integers)."""
    return (max(0, ceil_div((t0 - offset_ns) * rate, 1_000_000_000)),
            max(0, ceil_div((t1 - offset_ns) * rate, 1_000_000_000)))
