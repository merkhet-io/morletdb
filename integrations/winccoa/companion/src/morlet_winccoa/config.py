# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Companion configuration (TOML). Every limit of docs/winccoa.md decision 6 is a setting here.

The defaults were decided on 2026-11-14 (docs/winccoa.md, section Defaults), with WinCC OA's own archiver defaults
as the comparison. The limits are starting values, not yet measured under load.
"""
from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "morlet-winccoa/1"
SUPPORTED_SCHEMAS = (SCHEMA_VERSION,)
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class ConfigError(ValueError):
    """The configuration cannot be used as given."""


@dataclass(frozen=True)
class Limits:
    # Decision 6: starting values, decided 2026-11-14, docs/winccoa.md section Defaults; to be checked by a load measurement.
    observations_per_message: int = 1000
    body_bytes: int = 1 << 20
    datapoints: int = 500
    top_k_default: int = 10
    top_k_max: int = 50                      # the server allows 100 (streams.sql:1505)
    investigations_total: int = 4            # plus one per slot: a new request supersedes the slot's old one
    response_bytes: int = 256 * 1024
    overview_buckets: int = 16
    statement_timeout_ms: int = 10_000
    http_deadline_ms: int = 15_000
    max_candidates: int = 1_000_000
    window_min: int = 512
    window_max: int = 32_768
    window_default: int = 4096
    writer_batch_samples: int = 65_536       # per channel and transaction, including build_search_index
    auth_failures_per_minute: int = 5        # decision 5
    auth_backoff_s: int = 60
    connect_timeout_s: int = 5
    finished_requests_kept: int = 256        # investigation results kept for GET and identical retries


@dataclass(frozen=True)
class Clock:
    # Decision 3: decided 2026-11-14, docs/winccoa.md section Defaults (both hosts on NTP or PTP).
    bound_ms: float = 50.0
    # The companion-DAQ bound, declared by the operator when the companion is not on the DAQ host (it cannot be
    # measured by the companion). 0: the companion runs on the DAQ host.
    daq_bound_ms: float = 0.0
    # Decision 2: decided 2026-11-14, docs/winccoa.md section Defaults. Bounds how much state is lost at a disconnect; the health
    # report calls the adapter stale after three intervals without a heartbeat.
    heartbeat_interval_s: float = 10.0
    # Decision 3: the opt-in observed-time basis is not allowed (decided 2026-11-14, docs/winccoa.md section Defaults).
    allow_observed_time_basis: bool = False


@dataclass(frozen=True)
class Asset:
    name: str
    collection: str      # schema.table of a stream collection with acquisitions and context enabled
    machine: str         # the acquisitions' `machine`


@dataclass(frozen=True)
class Datapoint:
    dpe: str
    asset: str
    key: str
    time_basis: str = "source"            # "source" (WinCC OA source time) or "observed" (opt-in, see Clock)
    # Whether a value without quality attributes may become context. docs/winccoa.md section 3 stores such values
    # with quality_status 'missing' and derives no context (bad_quality); this keeps that as the default.
    # Decided 2026-11-14, docs/winccoa.md section Defaults (quality attribute names in the JavaScript API are open question 2).
    accept_missing_quality: bool = False


@dataclass(frozen=True)
class Config:
    dsn: str
    token_file: str
    listen_host: str = "127.0.0.1"
    listen_port: int = 8770
    # Decision 1: option (a), decided 2026-11-14, docs/winccoa.md section Defaults.
    schema: str = "morlet_winccoa"
    # Decision 5: one login role with two NOLOGIN group roles, decided 2026-11-14, docs/winccoa.md section Defaults.
    context_role: str = "winccoa_context"
    reader_role: str = "winccoa_reader"
    viewer_base_url: str = ""
    derive_interval_s: float = 5.0
    limits: Limits = field(default_factory=Limits)
    clock: Clock = field(default_factory=Clock)
    assets: tuple[Asset, ...] = ()
    datapoints: tuple[Datapoint, ...] = ()

    # --- lookups ------------------------------------------------------------------------------------------------
    def asset(self, name: str) -> Asset | None:
        return next((a for a in self.assets if a.name == name), None)

    def datapoint(self, dpe: str) -> Datapoint | None:
        return next((d for d in self.datapoints if d.dpe == dpe), None)

    def collections(self) -> list[str]:
        return sorted({a.collection for a in self.assets})

    def keys_of(self, asset: str) -> list[str]:
        return sorted({d.key for d in self.datapoints if d.asset == asset})


def _section(cls, raw: dict[str, Any] | None, where: str):
    raw = dict(raw or {})
    names = {f.name: f for f in fields(cls)}
    unknown = set(raw) - set(names)
    if unknown:
        raise ConfigError(f"[{where}]: unknown setting(s) {', '.join(sorted(unknown))}")
    obj = cls(**raw)
    for f in fields(cls):
        default = f.default
        value = getattr(obj, f.name)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"[{where}] {f.name} must be true or false")
        elif isinstance(default, (int, float)) and (isinstance(value, bool) or not isinstance(value, (int, float))
                                                      or value < 0):
            raise ConfigError(f"[{where}] {f.name} must be a nonnegative number")
    return obj


def _loopback_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme != "http" or not u.hostname:
            return False
        return u.hostname == "localhost" or ipaddress.ip_address(u.hostname).is_loopback
    except ValueError:
        return False


def _has_password(dsn: str) -> bool:
    if "://" in dsn:
        u = urlparse(dsn)
        return u.password is not None or "password=" in (u.query or "")
    return re.search(r"(^|\s)password\s*=", dsn) is not None


def validate(cfg: Config) -> Config:
    if _has_password(cfg.dsn):
        raise ConfigError("the DSN must not hold a password: use a libpq passfile or service file with mode 0600")
    try:
        if not ipaddress.ip_address(cfg.listen_host).is_loopback:
            raise ConfigError("the companion binds to a loopback address only (remote binding is not in phase A)")
    except ValueError as exc:
        raise ConfigError(f"listen host must be a loopback IP address: {exc}") from None
    for name in (cfg.schema, cfg.context_role, cfg.reader_role):
        if not _IDENT.match(name):
            raise ConfigError(f"{name!r} is not a plain lower-case SQL identifier")
    if cfg.viewer_base_url and not _loopback_url(cfg.viewer_base_url):
        raise ConfigError("viewer_base_url must be an http URL on a loopback address (docs/winccoa.md section 7)")
    lim = cfg.limits
    if not lim.window_min <= lim.window_default <= lim.window_max:
        raise ConfigError("limits: window_min <= window_default <= window_max")
    if not 1 <= lim.top_k_default <= lim.top_k_max <= 100:
        raise ConfigError("limits: 1 <= top_k_default <= top_k_max <= 100 (the server's maximum)")
    if not 1 <= lim.overview_buckets <= 1024:
        raise ConfigError("limits: overview_buckets must be 1..1024")
    if lim.investigations_total < 1 or lim.observations_per_message < 1:
        raise ConfigError("limits: investigations_total and observations_per_message must be at least 1")
    names = [a.name for a in cfg.assets]
    if len(set(names)) != len(names):
        raise ConfigError("asset names must be unique")
    for a in cfg.assets:
        if not a.name or not a.machine or "." not in a.collection:
            raise ConfigError(f"asset {a.name!r}: name, machine and collection (schema.table) are required")
        for part in a.collection.split(".", 1):
            if not _IDENT.match(part):
                raise ConfigError(f"asset {a.name!r}: collection {a.collection!r} is not schema.table of plain "
                                  "lower-case identifiers")
    if len(cfg.datapoints) > lim.datapoints:
        raise ConfigError(f"{len(cfg.datapoints)} datapoints configured; the limit is {lim.datapoints}")
    dpes = [d.dpe for d in cfg.datapoints]
    if len(set(dpes)) != len(dpes):
        raise ConfigError("datapoint elements must be unique")
    seen = set()
    for d in cfg.datapoints:
        if cfg.asset(d.asset) is None:
            raise ConfigError(f"datapoint {d.dpe!r}: unknown asset {d.asset!r}")
        if not d.key or not d.dpe:
            raise ConfigError("every datapoint needs dpe and key")
        if (d.asset, d.key) in seen:
            raise ConfigError(f"asset {d.asset!r} maps key {d.key!r} twice")
        seen.add((d.asset, d.key))
        if not isinstance(d.accept_missing_quality, bool):
            raise ConfigError(f"datapoint {d.dpe!r}: accept_missing_quality must be true or false")
        if d.time_basis not in ("source", "observed"):
            raise ConfigError(f"datapoint {d.dpe!r}: time_basis must be 'source' or 'observed'")
        if d.time_basis == "observed" and not cfg.clock.allow_observed_time_basis:
            raise ConfigError(f"datapoint {d.dpe!r}: the observed-time basis is not allowed "
                              "(clock.allow_observed_time_basis)")
    return cfg


def from_dict(raw: dict[str, Any]) -> Config:
    raw = dict(raw)
    service = dict(raw.pop("service", {}))
    limits = _section(Limits, raw.pop("limits", None), "limits")
    clock = _section(Clock, raw.pop("clock", None), "clock")
    viewer = dict(raw.pop("viewer", {}))
    try:
        assets = tuple(Asset(**a) for a in raw.pop("assets", []))
        dps = tuple(Datapoint(**d) for d in raw.pop("datapoints", []))
    except TypeError as exc:
        raise ConfigError(f"[[assets]] or [[datapoints]]: {exc}") from None
    if raw:
        raise ConfigError(f"unknown section(s) {', '.join(sorted(raw))}")
    listen = service.pop("listen", "127.0.0.1:8770")
    host, _, port = listen.rpartition(":")
    try:
        cfg = Config(listen_host=host, listen_port=int(port), viewer_base_url=viewer.pop("base_url", ""),
                     limits=limits, clock=clock, assets=assets, datapoints=dps, **service)
    except TypeError as exc:
        raise ConfigError(f"[service]: {exc}") from None
    if viewer:
        raise ConfigError(f"[viewer]: unknown setting(s) {', '.join(sorted(viewer))}")
    return validate(cfg)


def load(path: str | Path) -> Config:
    with open(path, "rb") as f:
        return from_dict(tomllib.load(f))


def with_changes(cfg: Config, **changes: Any) -> Config:
    return validate(replace(cfg, **changes))
