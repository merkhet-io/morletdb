# MorletDB trial runner (examples/trial): the importer and the trial, nothing else. Build context: tools/ingest.
FROM python:3.12.12-slim
WORKDIR /opt/morlet
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
RUN useradd --create-home trial && mkdir /report && chown trial /report
USER trial
ENTRYPOINT ["python", "-I", "/opt/morlet/morlet_trial.py", "--out", "/report"]
