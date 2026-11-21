# Shipping recordings from the edge, byte for byte

An edge database (a Raspberry Pi, a machine PC) stores recordings as compressed, checksummed chunks. To bring
them to a central database, `tools/ingest/morlet_ship.py` copies those chunks **as they are**, without decoding
and re-encoding them:

```sh
python tools/ingest/morlet_ship.py --source-dsn "postgresql://...@edge/db" --target-dsn "postgresql://...@central/db" \
    --collection public.line3 --metadata-filter '{"machine": "M1"}'
```

- **What travels** is the stored chunk size, not float64: on calibrated data a fraction of the raw size
  (Grizzbit; see the storage studies).
- **Verified on arrival:** the target decodes every chunk (`morlet_append_encoded_chunk`) and runs it through the
  same checks as any append: it must start at the recording's end, an identical resend is a no-op, and different
  samples at a stored offset are refused. The stored bytes are the given ones; the chunk table's CHECK decodes
  them again.
- **Resumable:** each run continues from the target's current end, so an interrupted transfer (a lost connection
  at the machine) goes on without duplicates.
- **Checked before writing (#444):** the target's existing chunks must equal the source's first chunks, byte for byte
  and on the same chunk boundaries, and the target must not be longer than the source. Otherwise the recording is
  refused and the target is left unchanged; no source chunk is appended onto a different prefix. One chunk more
  or fewer on either side counts as a difference, for example a final chunk removed by episodic memory on the
  target or the source (#495). A chunk missing in the source after the target's end stops the run before the batch
  that would need it is written.
- **Up to a fixed end:** a run ships up to the source's end as read when it starts, so a source that keeps
  recording meanwhile is shipped by the next run.
- **Checked at the end:** the SHA-256 over the chunk bytes up to that end must be equal on both sides. The
  encoder writes the same bytes on every platform (study B6), so equal bytes mean equal samples.
- **The target recording** is created with the source's parameters (rate, channel, unit, metadata, quantum,
  window, stride, normalization, extractor) when it does not exist yet. If it exists, it must have exactly these
  parameters (`morlet_ensure_recording`); otherwise the recording is refused before any sample is written, and the
  error names the differing fields (#443). Context, labels and search envelopes are
  not shipped; build envelopes on the target as usual.
- **Time anchors ship with the recording (#564).** A recording's [time anchors](clock-drift.md) are its time axis.
  The source's active anchors and their `morlet_time_axis_version` are read in one statement when the run starts,
  and go to the target in one transaction before the chunks. A target recording without anchors gets them (the
  anchor table is enabled if needed). A target whose anchors are exactly one earlier shipment, unchanged, gets them replaced when the
  source's changed since, for example after a revision at the edge; the earlier rows stay, retracted. "Exactly one
  earlier shipment" is checked, not taken from the batch name: the target's time-axis version must equal the one
  that shipment recorded. A
  target with anchors of its own (added or revised there) that differ from the source's is refused before anything
  is written: revise or retract them first (`morlet_revise_time_anchor`). An anchored source also needs SQL 0.3.13
  on the target. Only the current anchors travel, not their revision history.
- **Anchor edits wait for a shipment:** while a recording ships, the shipper holds its time-anchor lock on the
  target, so anchor additions and revisions of that recording from other sessions wait until its chunks are in.
- **The time axis is checked twice:** after the anchors and before the first chunk, the target's
  `morlet_time_axis_version` must equal the source's as read at the start; at the end both sides are compared
  again. The report gives it as `time_axis`, with `anchors_sent`, and `identical` covers chunks and time axis. A
  revision at the source during the run makes the final check fail with "run again"; the chunks shipped by then lie
  on the time axis read at the start until the next run replaces it.

Both servers need pg_morlet SQL 0.3.9 or later (`morlet_encoded_chunks`, `morlet_append_encoded_chunk`).
