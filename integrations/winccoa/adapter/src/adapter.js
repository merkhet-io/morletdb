// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The adapter: reads the configured asset datapoints, sends observations to the companion (protocol
// `morlet-winccoa/1`), runs investigations from its namespace datapoints and reports its status there.
//
// Behaviour, matching replay/adapter_sim.py and docs/winccoa.md sections 2-4:
// - One session per start; one seq per observation; resend until acknowledged (an identical resend is a no-op).
// - A 409 (conflicting retry), a 400 (malformed, or an unsupported schema version) or another refusal halts the
//   observation flow in an error state: it is surfaced, never retried in a loop. 401, 429, 5xx and connection
//   failures are retried with exponential backoff.
// - The current acquisition of each asset comes from GET /v1/health. Before one is known, values are only cached.
// - Initial values (update type Answer) and values after a REDU switch or DIST connection (Refresh) are sent as
//   `snapshot`, never as history. A Refresh is preceded by a gap for that datapoint (changes may have been missed).
// - Gaps: at start (the adapter was not watching before), when the outgoing queue overflows (then snapshots of the
//   cached current values follow once the queue has drained), at a clean shutdown, and for a datapoint whose
//   value cannot be represented or whose subscription reports an error.
// - Heartbeats every `heartbeat_interval_s` (default 10 s), created just before sending, with the RTT of a
//   GET /v1/clock of the same tick (no database work in the companion, so database latency is not counted as clock
//   offset). A companion without /v1/clock (404) is older: the RTT of the health request is used instead, which is
//   larger and so errs on the safe side, and this is logged once.

const crypto = require('node:crypto');
const { Client, TokenFile, TokenError, SCHEMA, fingerprint } = require('./client');
const { Outbox } = require('./outbox');
const { Namespace } = require('./namespace');
const { Subscription, subscribe, unsubscribe, errText } = require('./subscriptions');
const { Investigations } = require('./investigations');
const { encodeValue, timeToNs, nowNs, valueTypeFor, Unrepresentable } = require('./values');

const VERSION = require('../package.json').version;

class Adapter {
  /**
   * @param {object} o
   * @param {object} o.api     the loaded `winccoa-manager` module (enums and classes)
   * @param {object} o.winccoa a WinccoaManager instance
   * @param {object} o.config  validated configuration (config.js)
   * @param {object} o.log     makeLog(...)
   * @param {object} [o.client] a Client (default: built from the configuration)
   * @param {Function} [o.now] BigInt wall-clock nanoseconds
   */
  constructor({ api, winccoa, config, log, client, tokenFile, now = nowNs, timers = globalThis }) {
    this.api = api;
    this.winccoa = winccoa;
    this.cfg = config;
    this.log = log;
    this.now = now;
    this.timers = timers;
    this.tokenFile = tokenFile || new TokenFile(config.companion.token_file);
    this.tokenFile.onLoad = (token, fp) => {        // at start and after every rotation of the file
      this.log.setSecrets([token]);
      this.log.info(`token loaded from ${config.companion.token_file}, fingerprint ${fp}`);
    };
    this.client = client || new Client({ url: config.companion.url, tokenFile: this.tokenFile,
      timeoutMs: config.companion.request_timeout_ms });
    this.ns = new Namespace(config.namespace.prefix, config.namespace.slots);
    this.outbox = new Outbox({ maxPerMessage: config.limits.observations_per_message,
      maxBytes: config.limits.body_bytes, maxQueued: config.limits.max_queued });
    this.assets = new Map(config.assets.map((a) => [a.name, { acquisition: null, startupGap: config.startup_gap,
      overflow: false, missing: false }]));
    this.subs = [];
    this.cache = new Map();          // dpe -> encoded fields of the latest value, or {unrepresentable}
    this.valueTypes = new Map();
    this.degraded = new Map();       // reason key -> text
    this.session = null;
    this.halted = null;              // {companion, detail} once the observation flow is stopped
    this.companion = 'unknown';
    this.stopped = false;
    this.lastAckSeq = -1;
    this.lastRttNs = null;
    this.clockEndpoint = null;       // GET /v1/clock: null untried, true answered, false absent (older companion)
    this.lastHeartbeat = '';
    this.health = null;
    this.gaps = 0;
    this.backoffMs = config.retry.initial_ms;
    this.timer = null;
    this.flushTimer = null;
    this.retryTimer = null;
    this.flushing = null;
    this.flushAgain = false;
    this.flushHeartbeat = false;
    this.investigations = new Investigations({ winccoa, namespace: this.ns, client: this.client, config, log,
      sleep: (ms) => new Promise((r) => this.timers.setTimeout(r, ms)), isoNow: () => iso(this.now()),
      onLate: () => this.writeStatus() });
  }

  // --- start and stop ---------------------------------------------------------------------------------------------
  async start() {
    this.session = `winccoa-${crypto.randomUUID()}`;
    this.applyOptions();
    this.version = this.versionText();
    this.readToken();
    this.log.info(`starting: session ${this.session}, ${this.version}, companion ${this.cfg.companion.url}, `
      + `token fingerprint ${this.tokenFile.fingerprint() || 'unavailable'}`);
    await this.writeStatus('starting');
    this.resolveValueTypes();
    for (const dp of this.cfg.datapoints) {
      if (!this.valueTypes.has(dp.dpe)) continue;
      const sub = new Subscription(dp);
      try {
        const full = subscribe(this.winccoa, sub, (s, u) => this.onUpdate(s, u), this.log);
        if (!full) this.degraded.set(`fallback:${dp.dpe}`, `${dp.dpe}: value only (no source time or quality)`);
        this.subs.push(sub);
      } catch (e) {
        this.degraded.set(`subscribe:${dp.dpe}`, `${dp.dpe}: not subscribed (${errText(e)})`);
        this.log.error(`cannot subscribe ${dp.dpe}: ${errText(e)}`);
      }
    }
    this.investigations.subscribe();
    await this.tick();
  }

  async stop({ timeoutMs = 5000 } = {}) {
    if (this.stopped) return;
    this.stopped = true;
    this.investigations.stopped = true;
    for (const t of [this.timer, this.flushTimer, this.retryTimer]) if (t) this.timers.clearTimeout(t);
    this.timer = this.flushTimer = this.retryTimer = null;
    for (const s of this.subs) {
      try { unsubscribe(this.winccoa, s); } catch (e) { this.log.warn(`disconnect ${s.dp.dpe}: ${errText(e)}`); }
    }
    this.investigations.unsubscribe();
    const final = (async () => {
      await this.flush(true);                     // the backlog, and a heartbeat: contact until now
      for (const name of this.assets.keys()) this.pushGap(name, null);   // not watching from now on
      await this.flush(false);
    })();
    await withTimeout(final, timeoutMs);
    await withTimeout(this.investigations.drain(), timeoutMs);
    if (this.outbox.pending.length) {
      this.log.warn(`stopped with ${this.outbox.pending.length} unacknowledged observations (they are lost; the `
        + 'next start reports a gap)');
    }
    await this.writeStatus('stopped');
    this.log.info(`stopped: session ${this.session}, last acknowledged seq ${this.lastAckSeq}`);
  }

  applyOptions() {
    const T = this.api.WinccoaTimeFormat || {};
    const want = { temporal: T.Temporal, date: T.Date, number: T.Number }[this.cfg.time_format];
    try {
      if (want === undefined) throw new Error(`WinccoaTimeFormat has no ${this.cfg.time_format}`);
      this.winccoa.setOptions({ longAsBigInt: true, timeFormat: want });
      this.timeFormat = this.cfg.time_format;
    } catch (e) {
      this.winccoa.setOptions({ longAsBigInt: true, timeFormat: T.Date });
      this.timeFormat = 'date';
      this.degraded.set('time_format', `time format ${this.cfg.time_format} refused (${errText(e)}); source times in milliseconds`);
      this.log.warn(`time format ${this.cfg.time_format} refused (${errText(e)}); using Date (milliseconds)`);
    }
  }

  versionText() {
    let v = `adapter ${VERSION}`;
    try {
      const { api: a = {}, winccoa: w = {} } = this.winccoa.getVersionInfo() || {};
      v += `; API ${a.version}; WinCC OA ${w.display} patch ${w.patch} (${w.platform})`;
    } catch (e) {
      v += `; version info unavailable (${errText(e)})`;
    }
    return v;
  }

  readToken() {
    try {
      this.tokenFile.read();
      this.degraded.delete('token');
    } catch (e) {
      if (!(e instanceof TokenError)) throw e;
      this.degraded.set('token', e.message);
      this.log.error(e.message);
    }
  }

  resolveValueTypes() {
    for (const dp of this.cfg.datapoints) {
      if (dp.value_type) {
        this.valueTypes.set(dp.dpe, dp.value_type);
        continue;
      }
      try {
        const t = valueTypeFor(this.winccoa.dpElementType(dp.dpe), this.api.WinccoaElementType);
        if (t) this.valueTypes.set(dp.dpe, t);
        else this.degraded.set(`type:${dp.dpe}`, `${dp.dpe}: element type not supported; set value_type`);
      } catch (e) {
        this.degraded.set(`type:${dp.dpe}`, `${dp.dpe}: ${errText(e)}`);
        this.log.error(`datapoint ${dp.dpe}: ${errText(e)}`);
      }
    }
  }

  // --- values -----------------------------------------------------------------------------------------------------
  onUpdate(sub, u) {
    const dp = sub.dp;
    const C = this.api.WinccoaConnectUpdateType || {};
    if (u.error) {
      this.log.error(`update error for ${dp.dpe}: ${errText(u.error)}`);
      if (u.fellBack) this.degraded.set(`fallback:${dp.dpe}`, `${dp.dpe}: value only (no source time or quality)`);
      else this.degraded.set(`update:${dp.dpe}`, `${dp.dpe}: ${errText(u.error)}`);
      this.cache.delete(dp.dpe);
      this.pushGap(dp.asset, dp.dpe);
      this.scheduleFlush();
      return;
    }
    const fields = this.fields(sub, u);
    this.cache.set(dp.dpe, fields);
    const snapshot = u.type === C.Answer || u.type === C.Refresh;
    if (u.type === C.Refresh) this.pushGap(dp.asset, dp.dpe);
    const a = this.assets.get(dp.asset);
    if (!a.acquisition) return;                  // sent as a snapshot once an acquisition is known
    if (a.overflow) {                            // lost; a snapshot of the cached value follows the drained queue
      this.outbox.dropped += 1;
      return;
    }
    this.enqueueValue(dp, fields, snapshot ? 'snapshot' : 'change');
    this.scheduleFlush();
  }

  fields(sub, u) {
    const dp = sub.dp;
    const out = { dpe: dp.dpe };
    try {
      Object.assign(out, encodeValue(u.value, this.valueTypes.get(dp.dpe)));
    } catch (e) {
      if (!(e instanceof Unrepresentable)) throw e;
      this.log.warn(`${dp.dpe}: value not representable (${e.message}); reported as a gap`);
      return { unrepresentable: e.message };
    }
    if (u.valueOnly) return { ...out, source_status: 'unreadable', quality_status: 'unreadable' };
    const s = dp.subscription;
    if (!s.stime_attribute) {
      out.source_status = 'missing';
    } else {
      try {
        out.source_ns = timeToNs(u.stime).toString();
      } catch (e) {
        if (!(e instanceof Unrepresentable)) throw e;
        out.source_status = 'unreadable';
      }
    }
    const names = Object.keys(s.quality_attributes);
    if (names.length) {
      const q = {};
      let readable = true;
      for (const n of names) {
        const v = u.quality ? u.quality[n] : undefined;
        if (v === undefined || v === null) readable = false;
        else q[n] = typeof v === 'bigint' ? v.toString() : (['boolean', 'number', 'string'].includes(typeof v) ? v : String(v));
      }
      if (!readable) {
        out.quality_status = 'unreadable';
      } else {
        const rule = Object.entries(s.good_when);
        if (rule.length && rule.every(([k]) => typeof q[k] === 'boolean')) q.good = rule.every(([k, want]) => q[k] === want);
        out.quality = q;
      }
    }
    return out;
  }

  enqueueValue(dp, fields, kind) {
    const a = this.assets.get(dp.asset);
    if (fields.unrepresentable) return this.pushGap(dp.asset, dp.dpe);
    const obs = { kind, acquisition: a.acquisition, ...fields, observed_ns: this.now().toString() };
    if (Outbox.size(obs) > this.outbox.maxObservationBytes(this.session, this.assets.size)) {
      this.log.warn(`${dp.dpe}: value too large for one message; reported as a gap`);
      return this.pushGap(dp.asset, dp.dpe);
    }
    if (this.outbox.full()) {
      this.outbox.dropped += 1;
      if (!a.overflow) {
        a.overflow = true;
        this.log.warn(`outgoing queue full (${this.outbox.maxQueued}); asset ${dp.asset}: gap, snapshots after draining`);
        this.pushGap(dp.asset, null);
      }
      return null;
    }
    return this.outbox.push(obs);
  }

  /** A gap for an asset (all its keys) or one datapoint, in its current acquisition (none: nothing is open). */
  pushGap(assetName, dpe) {
    const a = this.assets.get(assetName);
    if (!a || !a.acquisition || this.halted) return null;
    this.gaps += 1;
    const g = { kind: 'gap', acquisition: a.acquisition, asset: assetName, observed_ns: this.now().toString() };
    if (dpe) g.dpe = dpe;
    return this.outbox.push(g);
  }

  snapshots(assetName) {
    for (const dp of this.cfg.datapoints) {
      if (dp.asset !== assetName || !this.cache.has(dp.dpe)) continue;
      this.enqueueValue(dp, this.cache.get(dp.dpe), 'snapshot');
    }
  }

  // --- the periodic tick: health, acquisitions, heartbeats, status ------------------------------------------------
  async tick() {
    if (this.stopped) return;
    try {
      if (!this.tokenFile.token || this.degraded.has('token')) this.readToken();
      const h = await this.client.request('GET', '/v1/health');
      this.onHealth(h);
      await this.measureRtt();
      await this.flush(true);
      await this.writeStatus();
    } catch (e) {
      this.log.error(`tick failed: ${e.stack || e.message}`);
    } finally {
      if (this.timer) this.timers.clearTimeout(this.timer);
      this.timer = this.stopped ? null : this.timers.setTimeout(() => this.tick(), this.cfg.heartbeat_interval_s * 1000);
    }
  }

  onHealth(h) {
    if (h.status === 0) {
      this.companion = h.tokenError ? 'token_unreadable' : 'unreachable';
      this.lastRttNs = null;
      return;
    }
    if (h.status === 401 || h.status === 429) {
      this.companion = 'unauthorized';
      this.lastRttNs = null;
      return;
    }
    if (!h.body || h.body.schema !== SCHEMA) {
      this.companion = 'version_mismatch';
      this.degraded.set('version', `companion speaks ${h.body && (h.body.schema || JSON.stringify(h.body.supported))}, `
        + `the adapter ${SCHEMA}`);
      return;
    }
    this.degraded.delete('version');
    this.health = h.body;
    this.lastRttNs = h.rttNs;
    const lim = h.body.limits || {};
    if (Number.isSafeInteger(lim.observations_per_message)) {
      this.outbox.maxPerMessage = Math.min(this.cfg.limits.observations_per_message, lim.observations_per_message);
    }
    if (Number.isSafeInteger(lim.body_bytes)) this.outbox.maxBytes = Math.min(this.cfg.limits.body_bytes, lim.body_bytes);
    if (h.status !== 200) {
      this.companion = 'database_unavailable';
      return;
    }
    if (this.companion !== 'ok' && !this.halted) this.companion = 'ok';
    const reported = new Map((h.body.assets || []).map((x) => [x.asset, x]));
    for (const [name, a] of this.assets) {
      const r = reported.get(name);
      if (!r) {
        if (!a.missing) this.log.warn(`asset ${name} is not configured in the companion`);
        a.missing = true;
        this.degraded.set(`asset:${name}`, `asset ${name} is not configured in the companion`);
        continue;
      }
      a.missing = false;
      this.degraded.delete(`asset:${name}`);
      const acq = r.current_acquisition || null;
      if (acq === a.acquisition) continue;
      this.log.info(`asset ${name}: current acquisition ${a.acquisition || '(none)'} -> ${acq || '(none)'}`);
      a.acquisition = acq;
      if (!acq) continue;
      if (a.startupGap) {
        a.startupGap = false;
        this.pushGap(name, null);
      }
      this.snapshots(name);
    }
  }

  /** The heartbeat RTT from GET /v1/clock; without it (absent or failed), the health request's RTT is kept. */
  async measureRtt() {
    if (this.lastRttNs === null || this.clockEndpoint === false) return;
    const c = await this.client.request('GET', '/v1/clock');
    if (c.status === 200 && c.body && c.body.schema === SCHEMA && c.rttNs !== null) {
      this.clockEndpoint = true;
      this.lastRttNs = c.rttNs;
    } else if (c.status === 404) {
      this.clockEndpoint = false;
      this.log.warn('the companion has no GET /v1/clock (an older version); heartbeat RTT is taken from GET '
        + '/v1/health, which includes its database time');
    }
  }

  heartbeats() {
    const out = [];
    for (const [name, a] of this.assets) {
      if (!a.acquisition) continue;
      const hb = { kind: 'heartbeat', acquisition: a.acquisition, asset: name, observed_ns: this.now().toString() };
      if (this.lastRttNs !== null) hb.rtt_ns = this.lastRttNs.toString();
      out.push(hb);
    }
    return out;
  }

  // --- sending ----------------------------------------------------------------------------------------------------
  scheduleFlush() {
    if (this.flushTimer || this.stopped) return;
    this.flushTimer = this.timers.setTimeout(() => {
      this.flushTimer = null;
      this.flush(false);
    }, this.cfg.flush_linger_ms);
  }

  /** Send everything pending (one flush at a time; a request during a flush runs right after it). */
  flush(heartbeat) {
    this.flushHeartbeat = this.flushHeartbeat || heartbeat;
    if (this.flushing) {
      this.flushAgain = true;
      return this.flushing;
    }
    this.flushing = (async () => {
      try {
        do {
          this.flushAgain = false;
          const hb = this.flushHeartbeat;
          this.flushHeartbeat = false;
          await this.send(hb);
        } while (this.flushAgain && !this.retryTimer);
      } finally {
        this.flushing = null;
      }
    })();
    return this.flushing;
  }

  async send(wantHeartbeat) {
    let hb = wantHeartbeat;
    while (!this.halted && this.session) {
      if (this.retryTimer) return;          // backing off: the retry will send
      // Heartbeats go only into the message holding the last pending gap (after it), or into any message when no
      // gap is pending; the outbox decides with the room the heartbeats take (see outbox.js, #496).
      const m = this.outbox.message(this.session, hb ? this.heartbeats() : []);
      if (m.body.observations.length === 0) return;
      const r = await this.client.request('POST', '/v1/observations', m.body);
      if (!this.onObservations(r, m)) return;
      if (m.heartbeats.length) {
        hb = false;
        this.lastHeartbeat = iso(BigInt(m.heartbeats[0].observed_ns));
      }
      if (this.outbox.pending.length === 0) {
        let again = false;
        for (const [name, a] of this.assets) {
          if (!a.overflow) continue;
          a.overflow = false;
          this.snapshots(name);
          again = true;
        }
        if (!again && !hb) return;
      }
    }
  }

  onObservations(r, m) {
    if (r.status === 200) {
      this.outbox.acknowledge(m.sent);
      if (r.body && Number.isSafeInteger(r.body.highest_seq)) this.lastAckSeq = r.body.highest_seq;
      this.companion = 'ok';
      this.backoffMs = this.cfg.retry.initial_ms;
      return true;
    }
    const b = r.body || {};
    if (r.status === 400 && b.error === 'unsupported_schema') {
      return this.halt('version_mismatch', `the companion supports ${JSON.stringify(b.supported)}, the adapter sends ${SCHEMA}`);
    }
    if (r.status === 409) return this.halt('conflict', `conflicting retry for seq ${JSON.stringify(b.seqs)}: ${b.detail || ''}`);
    if (r.status === 0 || r.status === 401 || r.status === 408 || r.status === 429 || r.status >= 500) {
      this.companion = r.status === 0 ? (r.tokenError ? 'token_unreadable' : 'unreachable')
        : r.status === 401 ? 'unauthorized' : r.status === 429 && b.error === 'authentication_backoff' ? 'unauthorized'
          : 'unavailable';
      if (r.tokenError) this.readToken();
      let delay = this.backoffMs;
      const ra = Number(r.retryAfter);
      if (Number.isFinite(ra) && ra > 0) delay = Math.max(delay, ra * 1000);
      this.backoffMs = Math.min(this.backoffMs * 2, this.cfg.retry.max_ms);
      this.log.warn(`observations not acknowledged (${r.status || r.error}); ${this.outbox.pending.length} pending, `
        + `retry in ${delay} ms`);
      if (!this.stopped) {
        this.retryTimer = this.timers.setTimeout(() => {
          this.retryTimer = null;
          this.flush(true);
        }, delay);
      }
      return false;
    }
    return this.halt('rejected', `HTTP ${r.status}: ${b.error || ''} ${b.detail || ''}`.trim());
  }

  halt(companion, detail) {
    this.halted = { companion, detail };
    this.companion = companion;
    this.log.error(`observation flow stopped (${companion}): ${detail}. Nothing more is sent in this session; `
      + 'fix the cause and restart the adapter');
    this.writeStatus();
    return false;
  }

  // --- status -----------------------------------------------------------------------------------------------------
  state() {
    if (this.stopped) return 'stopped';
    if (this.halted) return 'error';
    if (this.companion !== 'ok' || this.degraded.size) return 'degraded';
    return 'ok';
  }

  async writeStatus(force) {
    const detail = [this.halted && this.halted.detail, ...this.degraded.values()].filter(Boolean).join('; ');
    const health = this.health ? JSON.stringify(this.health) : '';
    try {
      await this.ns.write(this.winccoa, this.ns.adapterDp, {
        state: force === 'starting' || force === 'stopped' ? force : this.state(),
        detail: detail.slice(0, 4000), session: this.session || '', companion: this.companion,
        version: this.version || '', tokenFingerprint: this.tokenFile.fingerprint() || '',
        lastAckSeq: this.lastAckSeq, queued: this.outbox.pending.length, dropped: this.outbox.dropped,
        gaps: this.gaps, lateDropped: this.investigations.lateDropped,
        rttMs: this.lastRttNs === null ? -1 : Number(this.lastRttNs) / 1e6, lastHeartbeat: this.lastHeartbeat,
        health: health.slice(0, 64 * 1024),
      });
    } catch (e) {
      this.log.warn(`status not written: ${errText(e)}`);
    }
  }
}

function iso(ns) {
  const ms = ns / 1_000_000n;
  const frac = (ns % 1_000_000_000n).toString().padStart(9, '0');
  return new Date(Number(ms)).toISOString().replace(/\.\d{3}Z$/, `.${frac}Z`);
}

function withTimeout(p, ms) {
  let t;
  return Promise.race([p, new Promise((r) => { t = setTimeout(r, ms); })]).finally(() => clearTimeout(t));
}

module.exports = { Adapter, VERSION, iso, fingerprint };
