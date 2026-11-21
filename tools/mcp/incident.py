"""Agent-facing asset + incident time -> recording intervals (#580), over Client.resolve_incident; no mapping of its own."""
import json
import os
import re
import sys
import time

import psycopg

OFFSET = re.compile(r"(Z|[+-]\d{2}(:?\d{2})?)$", re.I)


def _config():
    """The plant's identifiers are operator configuration, not schema."""
    inv = sys.modules["investigation"]
    try:
        keys = json.loads(os.environ.get("MORLET_MCP_REFERENCE_KEYS", "{}"))
        if not isinstance(keys, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v
                                             for k, v in keys.items()):
            raise ValueError
    except ValueError:
        inv.fail("configuration_error", 'MORLET_MCP_REFERENCE_KEYS must be a JSON object such as {"work_order":"wo"}',
                 "Ask the server operator to correct the incident mapping.")
    return {"asset_key": os.environ.get("MORLET_MCP_ASSET_KEY", "asset").strip() or "asset",
            "sensor_key": os.environ.get("MORLET_MCP_SENSOR_KEY", "").strip() or None,
            "identity_keys": tuple(k.strip() for k in os.environ.get("MORLET_MCP_ASSET_IDENTITY_KEYS", "").split(",")
                                   if k.strip()),
            "reference_keys": keys}


def run(connect, checked, utc_ns_of, collection, asset, incident_time, *, before_s, after_s, sensor, metadata_filter,
        references):
    inv = sys.modules["investigation"]
    fail = inv.fail
    try:
        import morletdb
        if not hasattr(morletdb.Client, "resolve_incident"):
            raise ImportError("installed SDK lacks Client.resolve_incident")
    except ImportError:
        fail("sdk_unavailable", "The incident tool requires the SDK with Client.resolve_incident.",
             "Install this checkout's SDK with: python -m pip install ./sdk/python (from repository root).")
    if not isinstance(incident_time, str) or not OFFSET.search(incident_time.strip()):
        fail("invalid_input", "incident_time must be ISO 8601 with a UTC offset or Z.",
             "Ask for the plant's timezone rather than assuming one, for example 2026-03-01T14:32:00+01:00.")
    try:
        t_ns = utc_ns_of(incident_time)
        flt, refs = json.loads(metadata_filter or "{}"), json.loads(references or "{}")
        if not isinstance(flt, dict) or not isinstance(refs, dict):
            raise ValueError("metadata_filter and references must be JSON objects")
    except (TypeError, ValueError) as exc:
        fail("invalid_input", str(exc), 'Use an ISO 8601 time and JSON objects, for example {"plant":"north"}.')
    config = _config()
    unmapped = sorted(set(refs) - set(config["reference_keys"]))
    if unmapped:
        fail("invalid_input", "No metadata key is configured for: " + ", ".join(unmapped),
             "Omit these references, or ask the operator to add them to MORLET_MCP_REFERENCE_KEYS.")
    own = inv._limit("MORLET_MCP_INCIDENT_TIMEOUT_MS", 10000, 300000)
    server = inv.server_timeout_ms()
    budget = own if server is None else min(server, own)
    max_recordings = inv._limit("MORLET_MCP_INCIDENT_MAX_RECORDINGS", 200, 100_000)
    max_bytes = inv._limit("MORLET_MCP_INCIDENT_MAX_OUTPUT_BYTES", 64000, 2_000_000)
    deadline = time.monotonic() + budget / 1000

    def remaining():
        value = int((deadline - time.monotonic()) * 1000)
        if value <= 0:
            fail("budget_exceeded", "Incident resolution deadline exceeded.", "Narrow metadata_filter and retry.")
        return value

    try:
        with connect() as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ      # before checked() starts the snapshot
            conn.read_only = True
            conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(remaining()),))
            name = checked(conn, collection)
            report = morletdb.Client(conn).resolve_incident(
                name, asset, t_ns, before_s=before_s, after_s=after_s, sensor=sensor, metadata_filter=flt,
                references=refs, timeout_ms=remaining(), max_recordings=max_recordings, **config)
            result = report.to_dict()
            result["tool_limits"] = {"timeout_ms": budget, "max_recordings": max_recordings, "max_output_bytes": max_bytes}
            if inv.wire_size(result) > max_bytes:
                fail("response_too_large", f"Incident response exceeds {max_bytes} bytes.",
                     "Name a sensor, narrow metadata_filter, or shorten the window.")
            remaining()
            return result
    except morletdb.InvestigationLimitError as exc:
        fail("budget_exceeded", str(exc), "Name a sensor or narrow metadata_filter; this is not a negative result.")
    except (morletdb.InvalidInputError, ValueError) as exc:
        fail("invalid_input", str(exc), "Check the asset, the time window and the filters.")
    except morletdb.UnsupportedError as exc:
        fail("unsupported", str(exc), "Check the server SQL version.")
    except psycopg.errors.QueryCanceled:
        fail("budget_exceeded", "Incident resolution was cancelled or timed out.", "Narrow metadata_filter and retry.")
    except psycopg.errors.InsufficientPrivilege:
        fail("access_denied", "The database role cannot read the requested evidence.",
             "Use an authorized collection or ask its owner for access.")
    except (morletdb.MorletError, psycopg.Error) as exc:
        fail("database_error", str(exc), "Check database availability; this is not a negative result.")
