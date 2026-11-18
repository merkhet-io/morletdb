"""Context placement for the OPC UA collector, without dependencies (tested in test_context_rows.py).

- The value of a context row is the state alone (the tag's value), so the same state in two cycles gives the same
  segment context: segment context is built from whole values (morlet_build_context_segments), and status or
  timestamps inside a value would make every observation distinct (#480).
- A change at source time t holds from the first sample at or after t: ceil((t - start) * rate), computed exactly
  (whole microseconds times the rate as a fraction), never rounded to the nearest sample.
- A status other than good is kept as its own event, key "<key>.status", at the same sample, so it is neither lost
  nor mixed into the state.
"""
import math
from fractions import Fraction


def first_sample(t, start, rate):
    """Index of the first sample at or after time t, for a recording whose sample 0 is at start (datetimes)."""
    d = t - start
    us = (d.days * 86400 + d.seconds) * 1_000_000 + d.microseconds
    return math.ceil(Fraction(us, 1_000_000) * Fraction(rate))


def context_rows(recording_id, key, value, status, sample, good="Good"):
    """The context rows for one observation: the state, and its status when that is not good."""
    rows = [{"recording_id": recording_id, "key": key, "value": value, "start_sample": sample}]
    if status is not None and status != good:
        rows.append({"recording_id": recording_id, "key": key + ".status", "value": status, "start_sample": sample})
    return rows
