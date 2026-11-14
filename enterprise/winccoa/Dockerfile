# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
#
# The WinCC OA companion and the replay, on the public MorletDB Python SDK. Build from the repository root:
#   docker build -f enterprise/winccoa/Dockerfile -t morletdb-winccoa:local .
FROM python:3.12.10-slim-bookworm
WORKDIR /app
COPY sdk/python sdk/python
RUN pip install --no-cache-dir ./sdk/python
COPY enterprise/winccoa/companion enterprise/winccoa/companion
RUN pip install --no-cache-dir ./enterprise/winccoa/companion
COPY enterprise/winccoa/replay enterprise/winccoa/replay
COPY enterprise/winccoa/deploy enterprise/winccoa/deploy
ENV PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "-m", "morlet_winccoa", "serve", "--config", "/etc/morlet-winccoa/companion.toml"]
