# Project website

`docs/index.html` is a self-contained static page: HTML, CSS, JavaScript and inline SVG, with no external dependencies or build step. Other files in this folder remain project documentation.

## GitHub Pages

After review and merge, choose **Settings → Pages → Deploy from a branch → main → /docs**. This PR does not change repository settings or enable publication.

For a local preview, run `python3 -m http.server 8080 --directory docs` from the repository root and open http://localhost:8080/.

## Content

The CNC schematic and waveform are illustrative, not real measurements. Condition monitoring is motivation. The paper's retrieval evaluation is seismic; study N1 (`evaluation/nilm_n1`) added appliance identification from current transients (PLAID), where H1 passed (exact envelope search names the appliance about as well as a current-feature 1-NN) and H2 failed (the distance to the nearest stored event does not flag unknown appliances). The Paper and Supplement links open `docs/morletdb.pdf` and `docs/morletdb-supplement.pdf` in a new tab. They are copies of the canonical `research/paper/morletdb.pdf` and `research/paper/supplement.pdf`: after rebuilding the report, run `research/paper/sync_site.sh`; CI runs `research/paper/sync_site.sh --check` and fails if a copy differs.

The release and report versions on the pages are generated: `python3 tools/site_versions.py` copies `VERSION`
and the report version from `research/paper/RELEASE_MANIFEST.json` into every `<span data-version="…">`
in `docs/*.html` and `README.md`.
CI runs it with `--check`, so a PR that changes either version must also update the pages.
