// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Adapter configuration (JSON). Keys beginning with "_" are comments and ignored. Defaults follow docs/winccoa.md,
// section Defaults: a 10 s heartbeat, 1,000 observations and 1 MiB per message, 500 datapoints.
//
// The attribute names for value, source time and quality come from the WinCC OA config reference (_original); that
// the JavaScript Manager delivers them through dpConnect or dpQueryConnectSingle is UNCONFIRMED (API.md, verify in
// phase C), so all of them are settings.

const fs = require('node:fs');
const path = require('node:path');

class ConfigError extends Error {}

const DEFAULTS = Object.freeze({
  companion: { url: 'http://127.0.0.1:8770', token_file: '', request_timeout_ms: 20000 },
  namespace: { prefix: 'Morlet', slots: ['panel1'] },
  heartbeat_interval_s: 10,
  flush_linger_ms: 200,
  time_format: 'temporal',
  startup_gap: true,
  limits: { observations_per_message: 1000, body_bytes: 1 << 20, datapoints: 500, max_queued: 100000 },
  retry: {
    initial_ms: 500, max_ms: 30000,
    investigation_attempts: 3, investigation_poll_ms: 1000, investigation_wait_ms: 60000,
  },
  subscription: {
    mode: 'connect',
    value_attribute: '_original.._value',
    stime_attribute: '_original.._stime',
    quality_attributes: { bad: '_original.._bad', uncertain: '_original.._uncertain' },
    good_when: { bad: false, uncertain: false },
    fallback_to_value_only: true,
  },
  assets: [],
  datapoints: [],
});

const NAME = /^[A-Za-z][A-Za-z0-9_]{0,31}$/;
const SLOT = /^[A-Za-z0-9_]{1,32}$/;
const ATTR = /^_[A-Za-z0-9_]+\.[A-Za-z0-9_]*\._[A-Za-z0-9_]+$/;
const VALUE_TYPES = ['bool', 'int', 'float', 'string', 'json'];

function stripComments(v) {
  if (Array.isArray(v)) return v.map(stripComments);
  if (v && typeof v === 'object') {
    const o = {};
    for (const [k, x] of Object.entries(v)) if (!k.startsWith('_')) o[k] = stripComments(x);
    return o;
  }
  return v;
}

function section(raw, defaults, where) {
  const out = { ...defaults };
  if (raw === undefined) return out;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new ConfigError(`${where} must be an object`);
  for (const [k, v] of Object.entries(raw)) {
    if (!(k in defaults)) throw new ConfigError(`${where}: unknown setting ${k}`);
    const d = defaults[k];
    if (typeof d === 'number' && !(typeof v === 'number' && Number.isFinite(v) && v > 0)) {
      throw new ConfigError(`${where}.${k} must be a positive number`);
    }
    if (typeof d === 'boolean' && typeof v !== 'boolean') throw new ConfigError(`${where}.${k} must be true or false`);
    if (typeof d === 'string' && typeof v !== 'string') throw new ConfigError(`${where}.${k} must be a string`);
    out[k] = v;
  }
  return out;
}

function loopbackUrl(u) {
  let url;
  try {
    url = new URL(u);
  } catch (_) {
    return false;
  }
  if (url.protocol !== 'http:' || url.pathname !== '/' || url.search || url.username || url.password) return false;
  return url.hostname === '127.0.0.1' || url.hostname === '[::1]' || /^127\.\d+\.\d+\.\d+$/.test(url.hostname);
}

function attributes(sub, where) {
  if (!['connect', 'query'].includes(sub.mode)) throw new ConfigError(`${where}.mode must be "connect" or "query"`);
  for (const k of ['value_attribute', 'stime_attribute']) {
    if (sub[k] !== '' && !ATTR.test(sub[k])) throw new ConfigError(`${where}.${k} must look like _config.._attribute or be ""`);
  }
  if (sub.value_attribute === '' && sub.mode === 'query') throw new ConfigError(`${where}: query mode needs value_attribute`);
  const q = sub.quality_attributes;
  if (!q || typeof q !== 'object' || Array.isArray(q)) throw new ConfigError(`${where}.quality_attributes must be an object`);
  for (const [k, a] of Object.entries(q)) {
    if (!/^[a-z][a-z0-9_]{0,31}$/.test(k) || k === 'good') throw new ConfigError(`${where}.quality_attributes: bad name ${k}`);
    if (typeof a !== 'string' || !ATTR.test(a)) throw new ConfigError(`${where}.quality_attributes.${k} must look like _config.._attribute`);
  }
  const g = sub.good_when;
  if (!g || typeof g !== 'object' || Array.isArray(g)) throw new ConfigError(`${where}.good_when must be an object`);
  for (const [k, v] of Object.entries(g)) {
    if (!(k in q)) throw new ConfigError(`${where}.good_when.${k} is not a quality attribute`);
    if (typeof v !== 'boolean') throw new ConfigError(`${where}.good_when.${k} must be true or false`);
  }
  return sub;
}

function validate(rawIn, baseDir = process.cwd()) {
  const raw = stripComments(rawIn);
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new ConfigError('the configuration must be an object');
  const known = Object.keys(DEFAULTS);
  for (const k of Object.keys(raw)) if (!known.includes(k)) throw new ConfigError(`unknown setting ${k}`);
  const cfg = {
    companion: section(raw.companion, DEFAULTS.companion, 'companion'),
    namespace: { ...DEFAULTS.namespace, ...(raw.namespace || {}) },
    heartbeat_interval_s: raw.heartbeat_interval_s ?? DEFAULTS.heartbeat_interval_s,
    flush_linger_ms: raw.flush_linger_ms ?? DEFAULTS.flush_linger_ms,
    time_format: raw.time_format ?? DEFAULTS.time_format,
    startup_gap: raw.startup_gap ?? DEFAULTS.startup_gap,
    limits: section(raw.limits, DEFAULTS.limits, 'limits'),
    retry: section(raw.retry, DEFAULTS.retry, 'retry'),
    subscription: attributes({ ...DEFAULTS.subscription, ...(raw.subscription || {}) }, 'subscription'),
    assets: raw.assets || [],
    datapoints: raw.datapoints || [],
  };
  for (const k of Object.keys(raw.namespace || {})) {
    if (!(k in DEFAULTS.namespace)) throw new ConfigError(`namespace: unknown setting ${k}`);
  }
  for (const k of Object.keys(raw.subscription || {})) {
    if (!(k in DEFAULTS.subscription)) throw new ConfigError(`subscription: unknown setting ${k}`);
  }
  if (!loopbackUrl(cfg.companion.url)) {
    throw new ConfigError('companion.url must be http://127.0.0.1:<port>/ (loopback only; remote binding is not in phase B)');
  }
  if (!cfg.companion.token_file) throw new ConfigError('companion.token_file is required (a file with mode 0600)');
  cfg.companion.token_file = path.resolve(baseDir, cfg.companion.token_file);
  if (!(typeof cfg.heartbeat_interval_s === 'number' && cfg.heartbeat_interval_s > 0)) {
    throw new ConfigError('heartbeat_interval_s must be a positive number');
  }
  if (!(typeof cfg.flush_linger_ms === 'number' && cfg.flush_linger_ms >= 0)) {
    throw new ConfigError('flush_linger_ms must be a nonnegative number');
  }
  if (!['temporal', 'date', 'number'].includes(cfg.time_format)) {
    throw new ConfigError('time_format must be "temporal", "date" or "number"');
  }
  if (typeof cfg.startup_gap !== 'boolean') throw new ConfigError('startup_gap must be true or false');
  const ns = cfg.namespace;
  if (typeof ns.prefix !== 'string' || !NAME.test(ns.prefix)) throw new ConfigError('namespace.prefix must be a plain name (letters, digits, _)');
  if (!Array.isArray(ns.slots) || !ns.slots.every((s) => typeof s === 'string' && SLOT.test(s))) {
    throw new ConfigError('namespace.slots must be a list of plain names');
  }
  if (new Set(ns.slots).size !== ns.slots.length) throw new ConfigError('namespace.slots must be unique');
  for (const k of ['observations_per_message', 'body_bytes', 'datapoints', 'max_queued']) {
    if (!Number.isSafeInteger(cfg.limits[k])) throw new ConfigError(`limits.${k} must be an integer`);
  }
  if (cfg.limits.observations_per_message > 1000 || cfg.limits.body_bytes > (1 << 20)) {
    throw new ConfigError('limits: at most 1000 observations and 1 MiB per message (docs/winccoa.md section 6)');
  }
  if (cfg.limits.datapoints > 500) throw new ConfigError('limits.datapoints: at most 500 (docs/winccoa.md section 6)');
  if (!Array.isArray(cfg.assets) || cfg.assets.length === 0) throw new ConfigError('at least one asset is required');
  const assetNames = new Set();
  for (const a of cfg.assets) {
    if (!a || typeof a.name !== 'string' || !a.name || Object.keys(a).some((k) => k !== 'name')) {
      throw new ConfigError('every asset is {"name": "<the companion asset name>"}');
    }
    if (assetNames.has(a.name)) throw new ConfigError(`asset ${a.name} appears twice`);
    assetNames.add(a.name);
  }
  if (!Array.isArray(cfg.datapoints)) throw new ConfigError('datapoints must be a list');
  if (cfg.datapoints.length > cfg.limits.datapoints) {
    throw new ConfigError(`${cfg.datapoints.length} datapoints configured; the limit is ${cfg.limits.datapoints}`);
  }
  const seen = new Set();
  const nsPrefix = namespacePattern(ns.prefix);
  cfg.datapoints = cfg.datapoints.map((d, i) => {
    const where = `datapoints[${i}]`;
    if (!d || typeof d !== 'object') throw new ConfigError(`${where} must be an object`);
    for (const k of Object.keys(d)) {
      if (!['dpe', 'asset', 'value_type', 'subscription'].includes(k)) throw new ConfigError(`${where}: unknown setting ${k}`);
    }
    if (typeof d.dpe !== 'string' || !d.dpe || /[\s'"*?{}\[\]]/.test(d.dpe) || /\.[^:]*:/.test(d.dpe)) {
      throw new ConfigError(`${where}.dpe must be a plain datapoint element name (no wildcards or attributes)`);
    }
    if (nsPrefix.test(d.dpe)) throw new ConfigError(`${where}: ${d.dpe} lies in the adapter's own namespace; only asset datapoints are read`);
    if (seen.has(d.dpe)) throw new ConfigError(`${where}: ${d.dpe} appears twice`);
    seen.add(d.dpe);
    if (!assetNames.has(d.asset)) throw new ConfigError(`${where}: unknown asset ${d.asset}`);
    if (d.value_type !== undefined && !VALUE_TYPES.includes(d.value_type)) {
      throw new ConfigError(`${where}.value_type must be one of ${VALUE_TYPES.join(', ')}`);
    }
    for (const k of Object.keys(d.subscription || {})) {
      if (!(k in DEFAULTS.subscription) || k === 'mode') throw new ConfigError(`${where}.subscription: unknown setting ${k}`);
    }
    const sub = attributes({ ...cfg.subscription, ...(d.subscription || {}) }, `${where}.subscription`);
    return { dpe: d.dpe, asset: d.asset, value_type: d.value_type, subscription: sub };
  });
  return cfg;
}

/** Datapoint names of the adapter's namespace: <prefix>_Adapter and <prefix>_Inv_<slot>, optionally with a system. */
function namespacePattern(prefix) {
  return new RegExp(`^([A-Za-z0-9_]+:)?${prefix}_(Adapter|Inv_)`);
}

function load(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    throw new ConfigError(`cannot read the configuration ${file}: ${e.code || e.message}`);
  }
  let raw;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    throw new ConfigError(`the configuration ${file} is not JSON: ${e.message}`);
  }
  return validate(raw, path.dirname(path.resolve(file)));
}

module.exports = { ConfigError, DEFAULTS, validate, load, loopbackUrl, namespacePattern };
