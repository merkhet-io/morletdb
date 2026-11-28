"""Bounded native read using the existing SDK; summaries have explicit sample coordinates."""
from dataclasses import asdict
import time

import numpy as np
import psycopg


def run(connect, checked, contracts, errors, request):
    try:
        import morletdb
    except ImportError:
        errors.fail("sdk_unavailable", "The read tool requires the Python SDK.", "Install this checkout's sdk/python.")
    # As with investigation, a server timeout of zero removes its statement cap, not the tool deadline.
    budget = min(errors.server_timeout_ms() or 30000, 300000)
    max_bytes = errors._limit("MORLET_MCP_READ_MAX_OUTPUT_BYTES", 256000, 2_000_000)
    deadline = time.monotonic() + budget / 1000

    def remaining(conn):
        ms = int((deadline - time.monotonic()) * 1000)
        if ms <= 0:
            errors.fail("budget_exceeded", "Read deadline exceeded.", "Request a shorter interval.")
        conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(ms),))

    q = request.query
    try:
        with connect() as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            remaining(conn)
            collection = checked(conn, q.collection)
            row = conn.execute("SELECT current_database(), current_user, current_setting('transaction_isolation'), "
                               "transaction_timestamp()::text, pg_current_snapshot()::text, "
                               "(SELECT extversion FROM pg_extension WHERE extname='pg_morlet')").fetchone()
            provenance = dict(zip(("database", "role", "isolation", "transaction_started_at", "snapshot", "extension_version"), row))
            db = morletdb.Client(conn)
            remaining(conn)
            rec = db.recording(collection, q.recording_id)
            chunks = []
            for start in range(q.start_sample, q.end_sample, 32768):
                remaining(conn)
                chunks.append(db.read(collection, q.recording_id, start, min(start + 32768, q.end_sample)).samples)
            x = np.concatenate(chunks)
            remaining(conn)
            stats = db.stats(collection, q.recording_id, q.start_sample, q.end_sample)
            if x.size <= request.max_points:
                data = {"kind": "samples", "values": x.tolist()}
            else:
                buckets, start = [], q.start_sample
                for part in np.array_split(x, request.max_points // 2):
                    end = start + part.size
                    buckets.append({"start_sample": start, "end_sample": end,
                                    "min": float(part.min()), "max": float(part.max())})
                    start = end
                data = {"kind": "min_max_summary", "buckets": buckets}
            try:
                result = contracts.ReadEvidence(ref=q, recording=asdict(rec), stats=asdict(stats), data=data,
                    provenance=provenance, tool_limits={"timeout_ms": budget, "max_output_bytes": max_bytes,
                                                      "max_points": request.max_points}, notes=[
                    "Sample coordinates are authoritative and half-open; all reads share one read-only snapshot.",
                    "min_max_summary contains derived extrema, not original samples or interpolated values.",
                    "For bit-exact binary transfer use the SDK; JSON consumers must preserve float64 and integer precision.",
                    "An interval reference does not preserve deleted evidence or grant access."])
            except ValueError:
                errors.fail("contract_error", "Read result does not match its versioned schema.", "Check SDK/server compatibility.")
            size = errors.wire_size(result.model_dump(mode="json"))
            if size > max_bytes:
                errors.fail("response_too_large", f"Read response exceeds {max_bytes} bytes.",
                            "Reduce max_points or request a shorter interval.")
            remaining(conn)
            return result
    except morletdb.NotFoundError:
        errors.fail("evidence_unavailable", "Requested recording or interval is unavailable to this role.",
                    "Inspect coverage or choose a visible interval.")
    except morletdb.UnsupportedError as exc:
        errors.fail("unsupported", str(exc), "Read statistics require SQL 0.3.3 or later.")
    except (morletdb.InvalidInputError, ValueError) as exc:
        errors.fail("invalid_input", str(exc), "Check the collection and interval.")
    except psycopg.errors.InsufficientPrivilege:
        errors.fail("access_denied", "The database role cannot read the evidence.", "Use an authorized collection.")
    except psycopg.errors.QueryCanceled:
        errors.fail("budget_exceeded", "Read SQL was cancelled or timed out.", "Request a shorter interval.")
    except (morletdb.MorletError, psycopg.Error) as exc:
        if isinstance(exc.__cause__, psycopg.errors.QueryCanceled):
            errors.fail("budget_exceeded", "Read SQL was cancelled or timed out.", "Request a shorter interval.")
        errors.fail("database_error", str(exc), "Check database availability and recording integrity.")
