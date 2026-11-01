# Project website

`docs/index.html` is a self-contained static page: HTML, CSS, JavaScript and inline SVG, with no external dependencies or build step. Other files in this folder remain project documentation.

## GitHub Pages

After review and merge, choose **Settings → Pages → Deploy from a branch → main → /docs**. This PR does not change repository settings or enable publication.

For a local preview, run `python3 -m http.server 8080 --directory docs` from the repository root and open http://localhost:8080/.

## Content

The CNC schematic and waveform are illustrative, not real measurements. Condition monitoring is motivation; the retrieval benchmark is seismic. Research links and figures are pinned to draft 0.9 (`1d1c938`); update both together when a newer public paper is ready. The page explicitly labels the paper as not peer reviewed.
