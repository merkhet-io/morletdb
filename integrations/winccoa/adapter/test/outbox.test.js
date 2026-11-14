// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { Outbox } = require('../src/outbox');

const change = (i) => ({ kind: 'change', acquisition: 'a', dpe: 'D.x', value_text: String(i), value_type: 'int',
  source_ns: '1', observed_ns: '1' });

test('seqs per session, messages bounded by count and by bytes, resent identically until acknowledged', () => {
  const o = new Outbox({ maxPerMessage: 3, maxBytes: 1 << 20, maxQueued: 100 });
  for (let i = 0; i < 7; i += 1) o.push(change(i));
  let m = o.message('s');
  assert.deepEqual(m.body.observations.map((x) => x.seq), [0, 1, 2]);
  const again = o.message('s');                              // not acknowledged: the same observations again
  assert.deepEqual(again.body.observations, m.body.observations);
  o.acknowledge(m.sent);
  m = o.message('s');
  assert.deepEqual(m.body.observations.map((x) => x.seq), [3, 4, 5]);

  const small = new Outbox({ maxPerMessage: 1000, maxBytes: 400, maxQueued: 100 });
  for (let i = 0; i < 10; i += 1) small.push(change(i));
  const s = small.message('s');
  assert.ok(Buffer.byteLength(JSON.stringify(s.body)) <= 400);
  assert.ok(s.sent > 0 && s.sent < 10);
});

test('heartbeats are fresh, never retained, and placed after the last gap of the message', () => {
  const o = new Outbox({ maxPerMessage: 10, maxBytes: 1 << 20, maxQueued: 100 });
  o.push(change(0));
  o.push({ kind: 'gap', acquisition: 'a', asset: 'r', observed_ns: '2' });
  o.push(change(1));
  const m = o.message('s', [{ kind: 'heartbeat', acquisition: 'a', asset: 'r', observed_ns: '3' }]);
  assert.deepEqual(m.body.observations.map((x) => x.kind), ['change', 'gap', 'heartbeat', 'change']);
  const hbSeq = m.heartbeats[0].seq;
  const m2 = o.message('s');                                 // failed: resend without that heartbeat
  assert.ok(!m2.body.observations.some((x) => x.seq === hbSeq));
  const m3 = o.message('s', [{ kind: 'heartbeat', acquisition: 'a', asset: 'r', observed_ns: '4' }]);
  assert.ok(m3.heartbeats[0].seq > hbSeq);                   // a heartbeat seq is never reused
  assert.equal(m3.body.observations.length, 4);
});

test('the queue reports full at its limit', () => {
  const o = new Outbox({ maxPerMessage: 10, maxBytes: 1 << 20, maxQueued: 2 });
  o.push(change(0));
  assert.equal(o.full(), false);
  o.push(change(1));
  assert.equal(o.full(), true);
});
