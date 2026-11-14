# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""MorletDB WinCC OA companion (experimental; phase A of docs/winccoa.md, awaiting validation in WinCC OA).

Stores operating-context observations from the WinCC OA adapter, derives ordinary MorletDB context rows from them
and runs bounded similarity searches. It uses only the public core: the MorletDB Python SDK and documented SQL.
"""
from .config import SCHEMA_VERSION, Config, ConfigError, load

__all__ = ["SCHEMA_VERSION", "Config", "ConfigError", "load"]
__version__ = "0.1.0a1"
