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
  refused and the target is left unchanged; no source chunk is appended onto a different prefix.
- **Up to a fixed end:** a run ships up to the source's end as read when it starts, so a source that keeps
  recording meanwhile is shipped by the next run.
- **Checked at the end:** the SHA-256 over the chunk bytes up to that end must be equal on both sides. The
  encoder writes the same bytes on every platform (study B6), so equal bytes mean equal samples.
- **The target recording** is created with the source's parameters (rate, channel, unit, metadata, quantum,
  window, stride, normalization, extractor) when it does not exist yet. If it exists, it must have exactly these
  parameters (`morlet_ensure_recording`); otherwise the recording is refused before any sample is written, and the
  error names the differing fields (#443). Context, labels and search envelopes are
  not shipped; build envelopes on the target as usual.

Both servers need pg_morlet SQL 0.3.9 or later (`morlet_encoded_chunks`, `morlet_append_encoded_chunk`).
