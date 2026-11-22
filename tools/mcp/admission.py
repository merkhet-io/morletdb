"""Admission for MCP calls (#541): ingest comes first on a small box.

An MCP client usually starts its own server process, so a cap inside one process would not bound several agents.
Slots are PostgreSQL session advisory locks in this database: they hold across processes and are released by the server
when a client disconnects or crashes. Off by default (MORLET_MCP_MAX_CONCURRENT=0).
"""
import contextlib
import json
import math
import os
import threading
import time

import psycopg
from mcp.server.mcpserver.exceptions import ToolError

SLOT_CLASS = 0x4D4F524C      # first key of the advisory locks: "MORL"
MAX_SLOTS = 1000
MAX_QUEUE_MS = 60000
STATEMENT_MS = 5000
_state = threading.local()


def _setting(name, default, low, high):
    raw = os.environ.get(name, "").strip() or str(default)      # blank is unset
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
    """One round trip: the statement stops at the first free slot."""
    return conn.execute("SELECT s FROM generate_series(0,%s) s WHERE pg_try_advisory_lock(%s,s::int) LIMIT 1",
                        (cap - 1, SLOT_CLASS)).fetchone() is not None


def _refuse(code, message, suggestion):
    raise ToolError(json.dumps({"code": code, "message": message, "suggestion": suggestion}))


def _busy(cap):
    _refuse("busy", f"All {cap} MCP slots are in use, so this question was not answered. This is not a negative result.",
            "Retry in a few seconds, or ask fewer questions at once.")


def _release(conn):
    try:
        conn.execute("SELECT pg_advisory_unlock_all()")      # free at once, not when the backend has exited
    except psycopg.Error:
        pass      # the session is gone, and with it the lock
    conn.close()


def _attempt(cap, deadline, bounded):
    """A connection holding a slot, or None. A waiter holds no connection between attempts, so a queue of agents cannot
    use up the connections that ingest needs. With a queue budget the statement gets only what is left of it."""
    left = deadline - time.monotonic()
    timeout = max(2, math.ceil(left))      # libpq's shortest connect timeout is 2 s
    statement_ms = min(STATEMENT_MS, max(1, math.ceil(left * 1000))) if bounded else STATEMENT_MS
    try:
        conn = psycopg.connect(os.environ.get("DATABASE_URL", ""), autocommit=True, connect_timeout=timeout,
                               options=f"-c default_transaction_read_only=on -c statement_timeout={statement_ms}")
    except psycopg.OperationalError as exc:
        if exc.sqlstate == "53300" or "too many" in str(exc).lower():      # no connection free: wait like a full cap
            return None
        _refuse("database_error", "The database could not be reached to admit this call. This is not a negative result.",
                "Check database availability and retry.")
    try:
        if _take(conn, cap):
            return conn
    except psycopg.errors.QueryCanceled:
        if not bounded:
            conn.close()
            _refuse("database_error", "The admission check failed. This is not a negative result.",
                    "Check database availability and retry.")
    except psycopg.Error:
        conn.close()
        _refuse("database_error", "The admission check failed. This is not a negative result.",
                "Check database availability and retry.")
    conn.close()
    return None


@contextlib.contextmanager
def admission():
    """Hold one of the slots for a whole tool call, including its follow-up reads. A call that finds none within
    MORLET_MCP_QUEUE_MS is refused: a refusal says the question was not answered and is never an empty result."""
    cap = _setting("MORLET_MCP_MAX_CONCURRENT", 0, 0, MAX_SLOTS)
    if cap == 0 or getattr(_state, "held", False):
        yield
        return
    wait_ms = _setting("MORLET_MCP_QUEUE_MS", 2000, 0, MAX_QUEUE_MS)
    deadline = time.monotonic() + wait_ms / 1000
    bounded = wait_ms > 0      # a zero budget means exactly one attempt, however long that attempt takes
    pause = 0.1
    while (conn := _attempt(cap, deadline, bounded)) is None:
        left = deadline - time.monotonic()
        if left <= 0:
            _busy(cap)
        time.sleep(min(pause, left))
        if time.monotonic() >= deadline:      # no attempt starts after the budget
            _busy(cap)
        pause = min(pause * 2, 1.0)
    if bounded and time.monotonic() >= deadline:      # a slow database must not admit a call after its budget
        _release(conn)
        _busy(cap)
    _state.held = True
    try:
        yield
    finally:
        _state.held = False
        _release(conn)
