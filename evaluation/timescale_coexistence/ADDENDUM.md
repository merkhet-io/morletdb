# TC1-D1: bridge contract diagnostics

Frozen after the core run `20261128T121322Z`, before these diagnostics. These are
additional development probes, not a replacement for TC1 or a held-out study.

1. Try a changed derived-metric payload under the same unique key with the
   prototype's `ON CONFLICT DO NOTHING`. Record whether it is silently ignored.
   This tests the example adapter, not a shipped MorletDB feature or database bug.
2. Insert epoch-nanosecond timestamps separated by 3 ns as bigint alongside a
   timestamptz display projection. Expect bigint identity to remain distinct;
   document that the display representation is not the authoritative timestamp.
3. Store two identical, nonconstant-envelope, 8192-sample recordings; materialize
   envelopes, run exact interval search and read the selected match bit-exactly.
   Repeat search after restart and restore. This does not test pgvector, recall,
   realistic relevance or interactive Grafana rendering.

Repeat core cases to verify the added fixtures do not alter their outcomes. Preserve
all previous runs. Use the same image and resource limits. The runner records this
addendum's digest. No performance claims.
