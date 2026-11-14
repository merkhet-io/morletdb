# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Bearer-token authentication (docs/winccoa.md decision 5).

- The token file is owned by the service user with mode 0600 (no group or other bits); the companion refuses a wider
  mode at start and on every reload.
- It holds one or two tokens, one per line (current and next, for rotation); both are accepted. Each token is at
  least 32 random bytes (64 hex digits or 43 URL-safe base64 characters).
- It is reloaded when its modification time changes.
- Tokens are compared with hmac.compare_digest. Logs carry the fingerprint (first 8 hex digits of the token's
  SHA-256), the remote address and the outcome, never the token.
- Failures are rate-limited per remote address: after `failures_per_minute` failures within a minute, every request
  from that address is refused for `backoff_s` seconds.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import stat
import threading
import time
from collections import deque

log = logging.getLogger("morlet_winccoa.auth")
MIN_TOKEN_CHARS = 43


class TokenFileError(RuntimeError):
    pass


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def read_tokens(path: str) -> tuple[list[str], float]:
    try:
        st = os.stat(path)
    except OSError as exc:
        raise TokenFileError(f"token file {path}: {exc.strerror}") from None
    if not stat.S_ISREG(st.st_mode):
        raise TokenFileError(f"token file {path} is not a regular file")
    if st.st_mode & 0o077:
        raise TokenFileError(f"token file {path} has mode {stat.S_IMODE(st.st_mode):04o}; it must be 0600 or stricter")
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise TokenFileError(f"token file {path} is not owned by the service user")
    with open(path, encoding="utf-8") as f:
        tokens = [line.strip() for line in f if line.strip()]
    if not 1 <= len(tokens) <= 2:
        raise TokenFileError(f"token file {path} must hold one or two tokens (current and next), one per line")
    for t in tokens:
        if len(t) < MIN_TOKEN_CHARS or any(c.isspace() for c in t):
            raise TokenFileError(f"a token in {path} (fingerprint {fingerprint(t)}) is shorter than 32 random bytes")
    return tokens, st.st_mtime_ns


class Authenticator:
    def __init__(self, path: str, failures_per_minute: int = 5, backoff_s: float = 60.0, clock=time.monotonic):
        self.path = path
        self.failures_per_minute = failures_per_minute
        self.backoff_s = backoff_s
        self.clock = clock
        self._lock = threading.Lock()
        self._tokens, self._mtime = read_tokens(path)
        self._failures: dict[str, deque] = {}
        self._blocked: dict[str, float] = {}
        log.info("token file loaded: %s", ", ".join(fingerprint(t) for t in self._tokens))

    def _reload(self) -> None:
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return
        try:
            self._tokens, self._mtime = read_tokens(self.path)
            log.info("token file reloaded: %s", ", ".join(fingerprint(t) for t in self._tokens))
        except TokenFileError as exc:
            # Fail closed: an unreadable or too-open file accepts no token until it is fixed.
            self._tokens, self._mtime = [], mtime
            log.error("token file rejected, no token accepted: %s", exc)

    def check(self, header: str | None, remote: str) -> str:
        """'ok', 'unauthorized' or 'backoff' for an Authorization header from `remote`."""
        now = self.clock()
        with self._lock:
            until = self._blocked.get(remote)
            if until is not None:
                if now < until:
                    log.warning("auth from %s refused: back-off", remote)
                    return "backoff"
                del self._blocked[remote]
            self._reload()
            presented = ""
            if header and header.startswith("Bearer "):
                presented = header[len("Bearer "):].strip()
            ok = False
            for t in self._tokens:      # every token is compared, so timing does not reveal which one matched
                ok |= hmac.compare_digest(presented.encode(), t.encode())
            fp = fingerprint(presented) if presented else "none"
            if ok:
                log.debug("auth ok from %s, token %s", remote, fp)
                return "ok"
            q = self._failures.setdefault(remote, deque())
            q.append(now)
            while q and q[0] <= now - 60:
                q.popleft()
            log.warning("auth failed from %s, token %s", remote, fp)
            if len(q) >= self.failures_per_minute:
                self._blocked[remote] = now + self.backoff_s
                q.clear()
                log.warning("auth from %s: %d failures within a minute, back-off %ss", remote,
                            self.failures_per_minute, self.backoff_s)
            return "unauthorized"
