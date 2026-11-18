# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
#
# Tools image of the fleet investigator: loader, MCP servers, reference investigation, write-back and agent runner.
# Every Python package is pinned with its hashes (requirements.lock). Build from the repository root:
#   docker build -f examples/fleet-investigator/tools.Dockerfile -t morletdb-fleet-tools:local .
FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
WORKDIR /app
COPY examples/fleet-investigator/requirements.lock examples/fleet-investigator/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r examples/fleet-investigator/requirements.lock
COPY tools/ingest tools/ingest
COPY tools/mcp tools/mcp
COPY examples/fleet-investigator examples/fleet-investigator
ENV PYTHONUNBUFFERED=1
USER 1000:1000
