"""Agent-facing presentation of the SDK investigation; no duplicate search or scoring implementation."""
import json
import os
import time

import psycopg
from mcp.server.mcpserver.exceptions import ToolError


def fail(code, message, suggestion):
    # MCP transports this as an error, not a successful result containing an empty hits list.
    raise ToolError(json.dumps({"code": code, "message": message, "suggestion": suggestion}))


def _limit(name, default, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
        if not 1 <= value <= maximum:
            raise ValueError()
        return value
    except ValueError:
        fail("configuration_error", f"{name} must be an integer in 1..{maximum}",
             "Ask the server operator to correct the investigation limits.")


def present(report, detailed_coverage):
    out = report.to_dict()
    ranges = out.pop("coverage")
    samples = {}
    for row in ranges:
        samples[row["state"]] = samples.get(row["state"], 0) + row["end_sample"] - row["start_sample"]
    out["scope"] = {
        "status": out.pop("coverage_status"), "basis": "compatible_recordings_before_source_exclusion",
        "samples_by_state": samples, "range_count": len(ranges), "ranges_included": detailed_coverage,
        "candidate_completeness": "unknown" if out["search"]["mode"] == "probes" else "all_eligible_stored_windows",
    }
    if detailed_coverage:
        out["scope"]["ranges"] = ranges
    states = ["no_temporal_cutoff"]
    if out["scope"]["status"] == "unknown":
        states.append("coverage_unknown")
    if any(state != "searchable" for state in samples):
        states.append("unsearched_ranges")
    if out["search"]["mode"] == "probes":
        states.append("approximate_candidates_possible")
    if not out["search"]["matches"]:
        states.append("no_matches_returned")
    ctx = out["context"]
    if ctx is None:
        states.append("context_not_requested")
    else:
        if any(row["hist_n"] < out["settings"]["min_history"] for row in ctx["comparisons"]):
            states.append("insufficient_history")
        defined = {row["label"] for row in ctx["label_definitions"]}
        labels = {label["label"] for neighbor in ctx["neighbors"] for label in neighbor["labels"]}
        if labels - defined:
            states.append("undefined_labels")
        states.append("labelled_history_not_exhaustively_searched")
        for neighbor in ctx["neighbors"]:
            neighbor["ref"] = {"collection": ctx["ref"]["collection"],
                               "recording_id": neighbor["recording_id"],
                               "start_sample": neighbor["start_sample"], "end_sample": neighbor["end_sample"]}
    out["evidence_states"] = states
    return out


def run(connect, checked, collection, recording_id, start_sample, end_sample, *, metadata_filter,
        top_k, segment_id, match_keys, exclude_labels, detailed_coverage, max_candidates):
    try:
        import morletdb
        if not hasattr(morletdb.Client, "investigate"):
            raise ImportError("installed SDK lacks Client.investigate")
    except ImportError:
        fail("sdk_unavailable", "The investigation tool requires the SDK with Client.investigate.",
             "Install this checkout's SDK with: python -m pip install ./sdk/python (from repository root).")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
        fail("invalid_input", "top_k must be an integer in 1..20", "Request at most 20 matches; default is 5.")
    cap = _limit("MORLET_MCP_INVESTIGATION_MAX_CANDIDATES", 10000, 1_000_000)
    if max_candidates is None:
        max_candidates = cap
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= cap:
        fail("invalid_input", f"max_candidates must be in 1..{cap}", "Narrow metadata_filter to fit the server budget.")
    budget = min(_limit("MORLET_MCP_TIMEOUT_MS", 30000, 300000),
                 _limit("MORLET_MCP_INVESTIGATION_TIMEOUT_MS", 10000, 300000))
    max_bytes = _limit("MORLET_MCP_INVESTIGATION_MAX_OUTPUT_BYTES", 64000, 2_000_000)
    try:
        flt = json.loads(metadata_filter)
        if not isinstance(flt, dict):
            raise ValueError("metadata_filter must be a JSON object")
    except (TypeError, ValueError) as exc:
        fail("invalid_input", str(exc), 'Use a JSON object, for example {"asset":"pump-7"}.')
    deadline = time.monotonic() + budget / 1000

    def remaining():
        value = int((deadline - time.monotonic()) * 1000)
        if value <= 0:
            fail("budget_exceeded", "Investigation deadline exceeded.", "Narrow metadata_filter and retry.")
        return value

    try:
        with connect() as conn:
            # checked() queries the catalogue and begins the snapshot: set isolation BEFORE calling it.
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(remaining()),))
            name = checked(conn, collection)
            report = morletdb.Client(conn).investigate(name, recording_id, start_sample, end_sample,
                metadata_filter=flt, top_k=top_k, segment_id=segment_id,
                match_keys=tuple(k.strip() for k in match_keys.split(",") if k.strip()),
                exclude_labels=tuple(k.strip() for k in exclude_labels.split(",") if k.strip()),
                max_candidates=max_candidates, max_history=max_candidates,
                timeout_ms=remaining(), max_output_bytes=2_000_000)
            result = present(report, detailed_coverage)
            result["tool_limits"] = {"timeout_ms": budget, "max_output_bytes": max_bytes,
                                     "max_candidates": max_candidates}
            # Bound the final MCP payload, including wrapper fields. Match the MCP JSON serializer's
            # non-compact formatting conservatively; SDK's full intermediate result has its own cap.
            size = len(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8"))
            if size > max_bytes:
                fail("response_too_large", f"Investigation response exceeds {max_bytes} bytes.",
                     "Reduce top_k, narrow metadata_filter, or set detailed_coverage=false.")
            remaining()
            return result
    except morletdb.InvestigationLimitError as exc:
        fail("budget_exceeded", str(exc), "Narrow metadata_filter or reduce top_k; do not interpret this as no match.")
    except (morletdb.InvalidInputError, ValueError) as exc:
        fail("invalid_input", str(exc), "Check the collection, interval/window size and optional context segment.")
    except morletdb.NotFoundError as exc:
        fail("evidence_unavailable", str(exc), "Choose a visible recording with readable samples; use coverage to inspect availability.")
    except morletdb.UnsupportedError as exc:
        fail("unsupported", str(exc), "Check the server SQL version and enabled collection features.")
    except psycopg.errors.QueryCanceled:
        fail("budget_exceeded", "Investigation SQL was cancelled or timed out.", "Narrow metadata_filter and retry.")
    except psycopg.errors.InsufficientPrivilege:
        fail("access_denied", "The database role cannot read the requested evidence.", "Use an authorized collection or ask its owner for access.")
    except (morletdb.MorletError, psycopg.Error) as exc:
        fail("database_error", str(exc), "Check database availability and recording integrity; this is not a negative search result.")
