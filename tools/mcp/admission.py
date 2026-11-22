"""Admission for MCP calls (#541): ingest comes first on a small box.

An MCP client usually starts its own server process, so a cap inside one process would not bound several agents.
Slots are PostgreSQL session advisory locks in this database: they hold across processes and are released by the server
when a client disconnects or crashes. Off by default (MORLET_MCP_MAX_CONCURRENT=0).
"""
import contextlib
import json
import os
import threading
import time

import psycopg
from mcp.server.mcpserver.exceptions import ToolError

SLOT_CLASS = 0x4D4F524C      # first key of the advisory locks: "MORL"
MAX_SLOTS = 1000
MAX_QUEUE_MS = 60000
_state = threading.local()


def _setting(name, default, low, high):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
        if not low <= value <= high:
            raise ValueError
    except ValueError:
        raise ToolError(json.dumps({"code": "configuration_error",
                                    "message": f"{name} must be an integer in {low}..{high}",
                                    "suggestion": "Ask the server operator to correct the MCP load limits."})) from None
    return value


def _take(conn, cap):
    return any(conn.execute("SELECT pg_try_advisory_lock(%s,%s)", (SLOT_CLASS, slot)).fetchone()[0]
               for slot in range(cap))


@contextlib.contextmanager
def admission():
    """Hold one of the slots for a whole tool call, including its follow-up reads. A call that finds none within
    MORLET_MCP_QUEUE_MS is refused: a refusal says the question was not answered and is never an empty result."""
    cap = _setting("MORLET_MCP_MAX_CONCURRENT", 0, 0, MAX_SLOTS)
    wait_ms = _setting("MORLET_MCP_QUEUE_MS", 2000, 0, MAX_QUEUE_MS)
    if cap == 0 or getattr(_state, "held", False):
        yield
        return
    deadline = time.monotonic() + wait_ms / 1000
    with psycopg.connect(os.environ.get("DATABASE_URL", ""), autocommit=True,
                         options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        while not _take(conn, cap):
            left = deadline - time.monotonic()
            if left <= 0:
                raise ToolError(json.dumps({
                    "code": "busy",
                    "message": f"All {cap} MCP slots are in use, so this question was not answered. This is not a "
                               "negative result.",
                    "suggestion": "Retry in a few seconds, or ask fewer questions at once."}))
            time.sleep(min(0.05, left))
        _state.held = True
        try:
            yield
        finally:
            _state.held = False
