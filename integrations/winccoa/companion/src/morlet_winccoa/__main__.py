# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Command line of the companion.

    python -m morlet_winccoa install --config companion.toml [--login-role ROLE] [--dsn DSN]   # as the administrator
    python -m morlet_winccoa token FILE                     # write a new token file (mode 0600); prints its fingerprint
    python -m morlet_winccoa serve --config companion.toml  # the loopback service
    python -m morlet_winccoa derive --config companion.toml [--acquisition COLLECTION/ACQUISITION ...]
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import sys

import psycopg

from . import config as config_mod
from . import derive, schema
from .auth import TokenFileError, fingerprint


def _token(path: str) -> int:
    token = secrets.token_hex(32)       # 32 random bytes
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    print(f"wrote {path} (mode 0600), token fingerprint {fingerprint(token)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="morlet_winccoa")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("install", "serve", "derive"):
        s = sub.add_parser(name)
        s.add_argument("--config", required=True)
        if name == "install":
            s.add_argument("--login-role", help="the companion's login role, granted both group roles")
            s.add_argument("--dsn", help="administrator connection (default: the configured DSN)")
        if name == "derive":
            s.add_argument("--acquisition", action="append", default=[], help="COLLECTION/ACQUISITION")
    t = sub.add_parser("token")
    t.add_argument("file")
    a = p.parse_args(argv)
    logging.basicConfig(level=os.environ.get("MORLET_WINCCOA_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.cmd == "token":
        return _token(a.file)
    try:
        cfg = config_mod.load(a.config)
    except (OSError, config_mod.ConfigError) as exc:
        print(f"configuration: {exc}", file=sys.stderr)
        return 2
    if a.cmd == "install":
        with psycopg.connect(a.dsn or cfg.dsn) as conn:
            schema.install(conn, cfg, a.login_role)
        print(f"schema {cfg.schema} installed for {', '.join(cfg.collections()) or 'no collections'}")
        return 0
    if a.cmd == "derive":
        targets = [tuple(x.split("/", 1)) for x in a.acquisition] or None
        with psycopg.connect(cfg.dsn, autocommit=True) as conn:
            for s in derive.derive_all(conn, cfg, targets):
                print(f"{s.collection}/{s.acquisition_id}: {s.added_batches} batches, {s.rows_added} rows, "
                      f"reasons {s.reasons}, mismatches {s.mismatches}"
                      + (f", inconsistent start: {s.inconsistent_start}" if s.inconsistent_start else ""))
        return 0
    from .server import Companion, make_server
    try:
        companion = Companion(cfg)
    except TokenFileError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    srv = make_server(companion)
    companion.start_deriver()
    def _term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    logging.getLogger("morlet_winccoa").info("listening on %s:%s (%s)", cfg.listen_host, cfg.listen_port,
                                             config_mod.SCHEMA_VERSION)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        companion.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
