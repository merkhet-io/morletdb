# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Test support: one disposable demo database (the fleet investigator image), loaded once with the real data.

The tests start the demo image (pg_morlet + Apache AGE) in Docker on a free loopback port under a container named
`fleet-test-<random>`, run load.py against it with the IMS files from MORLET_DATA, and remove the container when
the test process exits. Nothing is mocked: every test talks to that database.

    MORLET_DATA         folder with the extracted IMS runs (required; the tests fail without it, never skip)
    FLEET_TEST_IMAGE    image to start (default morletdb-fleet-db:local, built from ../Dockerfile if missing)
"""
import atexit
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
ROOT = DEMO.parents[1]
sys.path.insert(0, str(DEMO))

import checks  # noqa: E402

IMAGE = os.environ.get("FLEET_TEST_IMAGE", "morletdb-fleet-db:local")
PREFIX = "fleet-test-"


def data_dir():
    d = os.environ.get("MORLET_DATA")
    if not d:
        raise RuntimeError("set MORLET_DATA to the folder with the extracted IMS runs (see README.md)")
    return Path(d)


class Database:
    def __init__(self):
        if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
            subprocess.run(["docker", "build", "-q", "-f", str(DEMO / "Dockerfile"), "-t", IMAGE, str(DEMO)],
                           check=True, capture_output=True)
        self.name = PREFIX + uuid.uuid4().hex[:10]
        self.password = secrets.token_hex(16)
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.name, "--memory", "2g", "--cpus", "4",
                        "-e", "POSTGRES_USER=morlet", "-e", f"POSTGRES_PASSWORD={self.password}",
                        "-e", "POSTGRES_DB=morlet", "-p", "127.0.0.1::5432", IMAGE], check=True, capture_output=True)
        atexit.register(self.remove)
        out = subprocess.run(["docker", "port", self.name, "5432/tcp"], check=True, capture_output=True,
                             text=True).stdout
        self.port = int(out.strip().splitlines()[0].rsplit(":", 1)[1])
        self.db = checks.Db("127.0.0.1", self.port, "morlet", self.password)
        deadline, ok = time.monotonic() + 120, 0
        while True:   # the entrypoint restarts the server after initialisation: wait for two successes in a row
            try:
                with psycopg.connect(self.db.dsn("morlet"), connect_timeout=2) as c:
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
        self.first_load = self.load()

    def load(self):
        """Run the loader as a separate process, as `docker compose run load` does; returns its output."""
        env = {**os.environ, "DATABASE_URL": self.db.dsn("morlet"), "MORLET_DATA": str(data_dir())}
        r = subprocess.run([sys.executable, "-I", str(DEMO / "load.py")], env=env, capture_output=True, text=True,
                           timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"load.py failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def remove(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


_db = None


def database():
    global _db
    if _db is None:
        _db = Database()
    return _db


def leftover_containers():
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"name={PREFIX}", "--format", "{{.Names}}"],
                         capture_output=True, text=True, check=True).stdout
    return [n for n in out.split() if n]
