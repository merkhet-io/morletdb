// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Investigations from the namespace slot datapoints: request correlation as in replay/adapter_sim.py (only the
// slot's current request_id is written; late results are dropped and counted), retries, polling and refusals.
const test = require('node:test');
const assert = require('node:assert/strict');
const { setup, waitFor } = require('./helpers');

const SLOT = 'Morlet_Inv_panel1';
const posts = (c) => c.requests.filter((r) => r.url === '/v1/investigations' && r.method === 'POST');

async function started(t, opts) {
  const s = await setup(opts);
  t.after(s.cleanup);
  await s.adapter.start();
  return s;
}

async function ask(s, { start = 4096n, end = 8192n, topK = 5, trigger } = {}) {
  await s.winccoa.dpSetWait([`${SLOT}.collection`, `${SLOT}.recordingId`, `${SLOT}.startSample`, `${SLOT}.endSample`,
    `${SLOT}.topK`], ['plant.rig', 'rec-vib', start, end, topK]);
  const n = trigger ?? (Number(s.ctl.value(`${SLOT}.trigger`)) + 1);
  await s.winccoa.dpSetWait(`${SLOT}.trigger`, n);
}

const done = (s) => ['done', 'failed'].includes(s.ctl.value(`${SLOT}.state`));

test('a trigger starts an investigation; the result is written into the slot with its request_id', async (t) => {
  const s = await started(t);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(posts(s.companion).length, 0);                    // the initial answer starts nothing
  await ask(s);
  await waitFor(() => done(s));
  const [p] = posts(s.companion);
  assert.match(p.body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual({ ...p.body, request_id: '' }, { schema: 'morlet-winccoa/1', request_id: '', slot: 'panel1',
    collection: 'plant.rig', recording_id: 'rec-vib', start_sample: 4096, end_sample: 8192, top_k: 5 });
  assert.equal(s.ctl.value(`${SLOT}.requestId`), p.body.request_id);
  assert.equal(s.ctl.value(`${SLOT}.state`), 'done');
  assert.equal(s.ctl.value(`${SLOT}.status`), 'ok');
  assert.equal(s.ctl.value(`${SLOT}.matchCount`), 1);
  assert.equal(JSON.parse(s.ctl.value(`${SLOT}.result`)).request_id, p.body.request_id);
  assert.match(s.ctl.value(`${SLOT}.updated`), /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z$/);
});

test('a superseded request\'s late result is dropped and counted; the slot keeps the current one', async (t) => {
  let release;
  const gate = new Promise((r) => { release = r; });
  let first = null;
  const s = await started(t, { companion: { handler: async (r) => {
    if (r.url !== '/v1/investigations') return undefined;
    if (first === null) {
      first = r.body.request_id;
      await gate;                                               // the first answer arrives after the second
    }
    return { status: 200, body: { schema: 'morlet-winccoa/1', request_id: r.body.request_id, slot: r.body.slot,
      status: r.body.request_id === first ? 'no_matches' : 'ok', matches: [], state: 'done' } };
  } } });
  await ask(s);
  await waitFor(() => first !== null);
  await ask(s, { start: 0n, end: 4096n });
  await waitFor(() => done(s));
  const second = s.ctl.value(`${SLOT}.requestId`);
  assert.notEqual(second, first);
  release();
  await waitFor(() => s.adapter.investigations.lateDropped === 1);
  await s.adapter.investigations.drain();
  assert.equal(s.ctl.value(`${SLOT}.requestId`), second);
  assert.equal(s.ctl.value(`${SLOT}.status`), 'ok');
  await s.adapter.writeStatus();
  assert.equal(s.ctl.value('Morlet_Adapter.lateDropped'), 1);
});

test('202 running: polled until done', async (t) => {
  let polls = 0;
  const s = await started(t, { companion: { handler: (r) => {
    if (r.url === '/v1/investigations') return { status: 202, body: { request_id: r.body.request_id, state: 'running' } };
    if (r.url.startsWith('/v1/investigations/')) {
      polls += 1;
      const rid = r.url.split('/').pop();
      return polls < 3 ? { status: 200, body: { request_id: rid, state: 'running' } }
        : { status: 200, body: { request_id: rid, slot: 'panel1', state: 'done', status: 'evidence_unavailable',
          detail: 'not searchable yet' } };
    }
    return undefined;
  } } });
  await ask(s);
  await waitFor(() => done(s));
  assert.equal(polls, 3);
  assert.equal(s.ctl.value(`${SLOT}.status`), 'evidence_unavailable');
  assert.equal(s.ctl.value(`${SLOT}.detail`), 'not searchable yet');
});

test('a connection failure is retried with the identical body (same request_id); a 409 fails the slot', async (t) => {
  let n = 0;
  const s = await started(t, { companion: { handler: (r) => {
    if (r.url !== '/v1/investigations') return undefined;
    n += 1;
    if (n === 1) return null;
    if (n === 2) return undefined;
    return { status: 409, body: { error: 'conflict', detail: 'request_id was used with a different body' } };
  } } });
  await ask(s);
  await waitFor(() => done(s));
  const [a, b] = posts(s.companion);
  assert.deepEqual(a.body, b.body);
  assert.equal(s.ctl.value(`${SLOT}.status`), 'ok');
  await ask(s);
  await waitFor(() => s.ctl.value(`${SLOT}.status`) === 'conflict');
  assert.equal(s.ctl.value(`${SLOT}.state`), 'failed');
  assert.match(s.ctl.value(`${SLOT}.detail`), /HTTP 409/);
});

test('fields that are no request fail the slot without a request', async (t) => {
  const s = await started(t);
  await ask(s, { start: 8192n, end: 4096n });
  await waitFor(() => done(s));
  assert.equal(s.ctl.value(`${SLOT}.status`), 'invalid_request');
  assert.equal(posts(s.companion).length, 0);
});

test('an unreachable companion fails the slot after the configured attempts', async (t) => {
  const s = await started(t, { companion: { handler: (r) => (r.url === '/v1/investigations' ? null : undefined) } });
  await ask(s);
  await waitFor(() => done(s));
  assert.equal(posts(s.companion).length, 3);
  assert.equal(s.ctl.value(`${SLOT}.status`), 'unreachable');
});
