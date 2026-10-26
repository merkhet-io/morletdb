# MorletDB viewer

A local, read-only web page for looking at recordings the way the context workflow
sees them:
- the waveform, with zoom, pan and min/max per pixel computed in the database;
- context, segment and label lanes;
- a click on a segment compares it with history (`morlet_compare_summary` and
  per-value `morlet_compare_to_history`);
- a selected window searches for similar windows (`morlet_search_envelope_interval`).

```sh
python -m pip install -r tools/viewer/requirements.txt
DATABASE_URL=postgresql://user@localhost/db python tools/viewer/viewer.py   # http://127.0.0.1:8765
```

To try it on synthetic data, run `python tools/viewer/demo.py` against a scratch
database first. It creates `demo_spindle`: 8 parts with program/block/tool/part
context, an air cut in part 6 (labelled) and chatter in part 7.

## Safety

- **Local only.** The server binds to 127.0.0.1.
- **Read-only.** Every session starts with `default_transaction_read_only=on` and
  a 20 s statement timeout, so the viewer cannot write. It uses the caller's
  role, so permissions and row-level security apply unchanged.
- **Discovered names only.** Collections are accepted only if they are
  discovered stream collections the role can read. All values go through query
  parameters.
- **Self-contained page.** It loads nothing from the network: no CDN, no fonts,
  and a restrictive Content-Security-Policy.

## Reading the comparison

- **Score:** the largest relative deviation over duration, std, crest and
  kurtosis. This is the score evaluated in C2 and C3. Dimmed values are shown but
  do not count.
- **Rank against history:** "exceeds k of n history segments" ranks the score
  against leave-one-out scores of the same context.
  - Status badges ("within history", "elevated", "unusual for this context")
    appear only with at least 20 history segments.
  - Below that, the rank is too coarse: exceeding all of 7 happens by chance one
    time in eight.
- **History excludes labelled segments** by default (checkbox), so a known air
  cut does not become "normal".
- **Tool age.** For gradual wear, time in use tracks nearly as well as sensor
  values (C1). Read scores next to tool age.

## Limits

- Overview requests cover at most 2^22 samples (zoom in for longer recordings).
- One channel at a time; multichannel acquisitions are not implemented yet.
- No editing: labels and context are added through SQL.
