# SeedLink collector as a container (examples/collectors/seedlink). The spool lives in /spool: mount a volume there so
# a restart replays what was received but not yet stored.
#   docker build -f examples/collectors/seedlink/Dockerfile -t morlet-seedlink .
FROM python:3.12.10-slim-bookworm
COPY examples/collectors/seedlink/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY examples/collectors/seedlink/collector.py examples/collectors/seedlink/verify_fdsn.py /opt/seedlink/
WORKDIR /opt/seedlink
RUN useradd -u 1000 -m collector && mkdir /spool && chown collector /spool
USER collector
ENTRYPOINT ["python", "collector.py", "--spool", "/spool"]
