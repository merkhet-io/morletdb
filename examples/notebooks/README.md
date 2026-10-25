# Visual waveform investigation

Open [waveform-investigation.ipynb](waveform-investigation.ipynb) to import a
synthetic archive, search a selected interval and compare raw waveforms, FFT
magnitudes, distances and acquisition provenance. A sign-flipped transient
demonstrates an intentional limitation of pooled spectral features.

This is an API/inspection example, not real-data relevance evidence. Existing
approved recordings can be investigated by changing the configuration and skipping
the demo import and synthetic assertions, as explained in the notebook.

## Local setup

From the repository root, use an isolated Docker demo database (Linux amd64):

```sh
docker compose up -d --build --wait db
docker compose exec -T db psql -U morlet -d morlet -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS pg_morlet'
python -m venv .venv-notebook
. .venv-notebook/bin/activate
python -m pip install -r examples/notebooks/requirements.txt
python -m ipykernel install --sys-prefix --name python3 --display-name "MorletDB notebook"
export DATABASE_URL=postgresql://morlet:morlet-demo-only@localhost:54329/morlet
python -m jupyterlab examples/notebooks/waveform-investigation.ipynb
```

Select the environment's Python kernel and Run All. The notebook locates the
checkout from its working directory. It uses the existing SQL client and Parquet
importer; there is no second local extractor/search engine.

The setup password is public and the database is bound to localhost. Use an
isolated instance. Extension installation requires an administrator; an existing
installation can instead grant schema/collection access to a restricted role.
No server addresses/passwords are printed by the notebook.

## Run without a browser

```sh
mkdir -p notebook-output
jupyter nbconvert --to notebook --execute examples/notebooks/waveform-investigation.ipynb --output executed.ipynb --output-dir notebook-output --ExecutePreprocessor.timeout=180
jupyter nbconvert --to html notebook-output/executed.ipynb --output investigation.html --output-dir notebook-output
```

The notebook asserts the repeated interval and sign-flipped distractor both have
near-zero feature distance, checks their exact raw values and opposite aligned
correlation, and reconstructs intervals crossing 300-sample chunk boundaries.
Rerunning uses the same importer replay contract and leaves demo data intact.

The Recording preview CI workflow executes the notebook twice against PostgreSQL
and uploads the executed notebook and HTML for visual inspection. Source notebooks
remain output-free. The diagram-like plots contain synthetic samples only; no
third-party dataset is downloaded.

## Cleanup and privacy

Close Jupyter, then `docker compose down` to stop the demo while retaining data.
`docker compose down -v` deletes the entire demo database volume.
To remove only this notebook's data, deliberately drop the isolated
`morlet_notebook` schema; never do so if it contains valuable data.

Do not commit notebook outputs, connection strings or customer recordings.
The disposable output directory and notebook environment are gitignored.
See [alpha boundaries](../../SUPPORT.md) and [recording workflow](../../docs/recording-workflow.md).
