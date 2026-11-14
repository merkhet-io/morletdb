# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
#
# The WinCC OA companion and the replay, on the public MorletDB Python SDK. Build from the repository root:
#   docker build -f integrations/winccoa/Dockerfile -t morletdb-winccoa:local .
FROM python:3.12.10-slim-bookworm
WORKDIR /app
COPY sdk/python sdk/python
RUN pip install --no-cache-dir ./sdk/python
COPY integrations/winccoa/companion integrations/winccoa/companion
RUN pip install --no-cache-dir ./integrations/winccoa/companion
COPY integrations/winccoa/replay integrations/winccoa/replay
COPY integrations/winccoa/deploy integrations/winccoa/deploy
ENV PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "-m", "morlet_winccoa", "serve", "--config", "/etc/morlet-winccoa/companion.toml"]
