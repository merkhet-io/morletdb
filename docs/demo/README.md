# Investigation concept fixture

The homepage demo reads `investigation-v1.json` and runs entirely in the browser.
Serve `docs/` over HTTP (for example, `python3 -m http.server 8876 --bind
127.0.0.1 --directory docs` from the repository root) and open
`http://127.0.0.1:8876/#investigation`.

All signals, identities, maintenance records and outcomes are synthetic. The fixture
is a versioned presentation contract, **not** an SDK `InvestigationResult` response.
Its interval references follow the SDK's zero-based, half-open convention. Plot
arrays are illustrative display points, not the raw samples of those intervals.
No search is executed and no similarity scores or measured outcomes are claimed.

Version 1 has an incident and three scenarios: a comparable precedent, a context
mismatch, and unavailable historical waveform evidence. Each has explicit evidence
states. The lesson cards illustrate the planned reviewed-outcome workflow (#217).
Bump `fixture_version` and update the renderer when changing the contract incompatibly.

Manual verification: select all three cases; inspect the JSON disclosure; download
the fixture; check keyboard selection and narrow-screen layout. With JavaScript
unavailable, a static explanation and fixture download remain accessible.
