"""Agent-facing explanation of one search match (#573), over Client.explain_match; no scoring of its own."""
import sys

import psycopg

# The lag tolerance of every search this server runs: another value would explain another score.
SEARCH_MAX_LAG_FRACTION = 0.075
MAX_BINS = 64
INTERPRETATION = ("Contributions explain the similarity score, not a fault's cause. Envelopes are normalized: compare "
                  "amplitude with query_stats and match_stats. Timing is no finer than one envelope step (step_samples).")


def run(connect, checked, collection, query_recording_id, query_start, query_end, match_recording_id, match_start,
        contribution_bins, query_shift_samples=0):
    inv = sys.modules["investigation"]
    fail = inv.fail
    try:
        import morletdb
        if not hasattr(morletdb.Client, "explain_match"):
            raise ImportError("installed SDK lacks Client.explain_match")
    except ImportError:
        fail("sdk_unavailable", "The explanation tool requires the SDK with Client.explain_match.",
             "Install this checkout's SDK with: python -m pip install ./sdk/python (from repository root).")
    if isinstance(contribution_bins, bool) or not isinstance(contribution_bins, int) or \
            not 1 <= contribution_bins <= MAX_BINS:
        fail("invalid_input", f"contribution_bins must be an integer in 1..{MAX_BINS}.", "Use the default of 16.")
    query_start, query_end = query_start + query_shift_samples, query_end + query_shift_samples
    if query_start < 0 or query_end <= query_start or match_start < 0:
        fail("invalid_input", "Sample intervals must be zero-based and half-open.",
             "Pass the query interval and the start_sample of a returned match.")
    length = query_end - query_start
    try:
        with connect() as conn:
            name = checked(conn, collection)
            result = morletdb.Client(conn).explain_match(
                morletdb.IntervalRef(name, query_recording_id, query_start, query_end),
                morletdb.IntervalRef(name, match_recording_id, match_start, match_start + length),
                max_lag_fraction=SEARCH_MAX_LAG_FRACTION, contribution_bins=contribution_bins).to_dict()
    except morletdb.NotFoundError as exc:
        fail("evidence_unavailable", str(exc), "Use a current match from a search, on samples this role can read; "
             "this says nothing about the signal.")
    except (morletdb.InvalidInputError, ValueError) as exc:
        fail("invalid_input", str(exc), "Use the same interval length, rate, channel and unit as the search.")
    except morletdb.UnsupportedError as exc:
        fail("unsupported", str(exc), "Update the extension to SQL 0.3.16 or later.")
    except psycopg.errors.QueryCanceled:
        fail("budget_exceeded", "The explanation timed out.", "Retry later; this is not a negative result.")
    except psycopg.errors.InsufficientPrivilege:
        fail("access_denied", "The database role cannot read the requested evidence.",
             "Use an authorized collection or ask its owner for access.")
    except (morletdb.MorletError, psycopg.Error) as exc:
        fail("database_error", str(exc), "Check database availability; this is not a negative result.")
    result.pop("query_envelope", None)
    result.pop("stored_envelope", None)
    result["interpretation"] = INTERPRETATION
    return result
