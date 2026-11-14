// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Investigations started from the namespace datapoints <prefix>_Inv_<slot> (docs/winccoa.md section 4).
//
// - A panel writes collection, recordingId, startSample, endSample and topK, then changes `trigger`. The adapter
//   connects to all six elements in one dpConnect, so one callback carries a consistent set; it acts only when
//   `trigger` changed. The initial answer only records the trigger's value.
// - Each start gets a new request_id (UUID) and becomes the slot's current request. Retries of a request (after a
//   connection failure) resend the identical body, which the companion answers with the same result.
// - A result (or failure) is written into the slot only while its request_id is still the slot's current one;
//   otherwise it is dropped and counted (`lateDropped`), as replay/adapter_sim.py `Adapter.accept` does.
// - A 202 ("running") is polled with GET /v1/investigations/{request_id} until done, superseded locally, or
//   `investigation_wait_ms` has passed.

const crypto = require('node:crypto');
const { INPUTS } = require('./namespace');
const { SCHEMA } = require('./client');

class Investigations {
  constructor({ winccoa, namespace, client, config, log, sleep, onLate, isoNow }) {
    this.winccoa = winccoa;
    this.ns = namespace;
    this.client = client;
    this.retry = config.retry;
    this.log = log;
    this.sleep = sleep;
    this.onLate = onLate || (() => {});
    this.isoNow = isoNow;
    this.current = new Map();      // slot -> request_id
    this.lastTrigger = new Map();  // slot -> trigger value seen
    this.connections = [];
    this.running = new Set();
    this.lateDropped = 0;
    this.stopped = false;
  }

  subscribe() {
    for (const slot of this.ns.slots) {
      const dp = this.ns.slotDp(slot);
      const names = INPUTS.map((e) => `${dp}.${e}`);
      const id = this.winccoa.dpConnect((dpeNames, values, type, error) => {
        if (error) {
          this.log.error(`investigation slot ${slot}: connection error ${error.message || error}`);
          return;
        }
        const fields = Object.fromEntries(INPUTS.map((k, i) => [k, values[i]]));
        const trig = String(fields.trigger);
        const seen = this.lastTrigger.get(slot);
        this.lastTrigger.set(slot, trig);
        if (seen === undefined || seen === trig || this.stopped) return;   // initial answer or another field changed
        this.start(slot, fields);
      }, names, true);
      this.connections.push(id);
    }
  }

  unsubscribe() {
    for (const id of this.connections.splice(0)) {
      try { this.winccoa.dpDisconnect(id); } catch (e) { this.log.warn(`disconnect ${id}: ${e.message}`); }
    }
  }

  /** Start an investigation in `slot`; returns the promise of its completion (for tests and shutdown). */
  start(slot, fields) {
    const rid = crypto.randomUUID();
    this.current.set(slot, rid);
    const p = this.run(slot, rid, fields).catch((e) => this.log.error(`investigation ${rid}: ${e.message}`));
    this.running.add(p);
    p.finally(() => this.running.delete(p));
    return p;
  }

  async run(slot, rid, f) {
    const body = request(slot, rid, f);
    if (typeof body === 'string') {
      return this.write(slot, rid, { state: 'failed', status: 'invalid_request', detail: body, matchCount: 0, result: '' });
    }
    await this.write(slot, rid, { state: 'running', status: '', detail: '', matchCount: 0, result: '' });
    let r;
    let attempt = 0;
    let delay = this.retry.initial_ms;
    for (;;) {
      r = await this.client.request('POST', '/v1/investigations', body);
      if (r.status !== 0 && r.status < 500 && r.status !== 429) break;
      attempt += 1;
      if (attempt >= this.retry.investigation_attempts || this.current.get(slot) !== rid || this.stopped) break;
      await this.sleep(delay);
      delay = Math.min(delay * 2, this.retry.max_ms);
    }
    if (r.status === 202) r = await this.poll(slot, rid);
    if (r.status === 200 || (r.status === 503 && r.body && r.body.status)) {
      const res = r.body;
      return this.write(slot, rid, {
        state: 'done', status: String(res.status || ''), detail: String(res.detail || ''),
        matchCount: Array.isArray(res.matches) ? res.matches.length : 0, result: JSON.stringify(res),
      });
    }
    const detail = r.status === 0 ? `companion unreachable: ${r.error}`
      : `HTTP ${r.status}: ${(r.body && (r.body.detail || r.body.error)) || ''}`;
    const status = { 0: 'unreachable', 400: 'invalid_request', 401: 'unauthorized', 403: 'not_configured',
      409: 'conflict', 429: 'busy' }[r.status] || 'failed';
    return this.write(slot, rid, { state: 'failed', status, detail, matchCount: 0, result: '' });
  }

  async poll(slot, rid) {
    const deadline = Date.now() + this.retry.investigation_wait_ms;
    for (;;) {
      if (this.current.get(slot) !== rid || this.stopped) return { status: 0, error: 'superseded or stopped while running' };
      await this.sleep(this.retry.investigation_poll_ms);
      const r = await this.client.request('GET', `/v1/investigations/${rid}`);
      if (r.status === 200 && r.body && r.body.state === 'done') return r;
      if (r.status !== 0 && r.status !== 200 && r.status < 500) return r;
      if (Date.now() > deadline) return { status: 0, error: `no result after ${this.retry.investigation_wait_ms} ms` };
    }
  }

  /** Write into the slot only if `rid` is still its current request; otherwise drop and count. */
  async write(slot, rid, values) {
    if (this.current.get(slot) !== rid) {
      this.lateDropped += 1;
      this.onLate(slot, rid);
      this.log.info(`investigation ${rid} in slot ${slot}: result dropped, superseded by ${this.current.get(slot)}`);
      return false;
    }
    try {
      await this.ns.write(this.winccoa, this.ns.slotDp(slot), { requestId: rid, ...values, updated: this.isoNow() });
    } catch (e) {
      this.log.error(`investigation ${rid}: writing slot ${slot} failed: ${e.message}`);
      return false;
    }
    return true;
  }

  async drain() {
    await Promise.allSettled([...this.running]);
  }
}

function toInt(v, name) {
  if (typeof v === 'bigint') {
    if (v < 0n || v > BigInt(Number.MAX_SAFE_INTEGER)) return `${name} is out of range`;
    return Number(v);
  }
  if (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0) return v;
  return `${name} must be a nonnegative integer`;
}

/** The request body, or a string saying why the panel's fields are not a request. */
function request(slot, rid, f) {
  if (typeof f.collection !== 'string' || !f.collection) return 'collection is empty';
  if (typeof f.recordingId !== 'string' || !f.recordingId) return 'recordingId is empty';
  const a = toInt(f.startSample, 'startSample');
  const b = toInt(f.endSample, 'endSample');
  const k = toInt(f.topK ?? 0, 'topK');
  for (const x of [a, b, k]) if (typeof x === 'string') return x;
  if (b <= a) return 'endSample must be greater than startSample';
  const body = { schema: SCHEMA, request_id: rid, slot, collection: f.collection, recording_id: f.recordingId,
    start_sample: a, end_sample: b };
  if (k > 0) body.top_k = k;
  return body;
}

module.exports = { Investigations, request };
