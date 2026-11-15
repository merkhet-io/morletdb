// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// #496: a heartbeat must not land before a pending gap. The REAL Adapter.send and Outbox on the fake WinCC OA API,
// with a recording client in place of the companion. Used by adapter.test.js, and by test/e2e/test_e2e.py, which
// feeds the emitted observations through the companion's derive.compute.
//
//   node heartbeat-gap.js count|bytes      prints {"posts": [[observation, ...], ...], ...} in sending order
//
// A heartbeat at 0 s is acknowledged first (so the clock estimate is good), a change of SPEED at 1 s and an asset
// gap at 10 s are queued, and send(true) runs at 10 s with a heartbeat. The message capacity is just enough for the
// change and the gap (`count`: two observations; `bytes`: their bytes), so a heartbeat beside them does not fit.

const { createFake } = require('./fake-winccoa');
const { validate } = require('../src/config');
const { Adapter } = require('../src/adapter');
const { Outbox } = require('../src/outbox');
const { config, SPEED } = require('./helpers');

const S = 1_000_000_000n;
const SESSION = 'winccoa-496';

function quietLog() {
  const noop = () => {};
  return { info: noop, warn: noop, error: noop, debug: noop, setSecrets: noop };
}

async function emit(bound) {
  const { api } = createFake();
  const winccoa = new api.WinccoaManager();
  const conf = validate(config(), __dirname);
  let t = 0n;
  const posts = [];
  const client = {
    async request(method, url, body) {
      if (url !== '/v1/observations') return { status: 404, body: { error: 'not_found' } };
      posts.push(JSON.parse(JSON.stringify(body.observations)));
      const highest = Math.max(...body.observations.map((o) => o.seq));
      return { status: 200, body: { schema: 'morlet-winccoa/1', session: SESSION, highest_seq: highest } };
    },
  };
  const adapter = new Adapter({ api, winccoa, config: conf, log: quietLog(), client, now: () => t,
    tokenFile: { fingerprint: () => '', read: () => null } });
  adapter.session = SESSION;
  adapter.assets.get('rig-1').acquisition = 'acq-1';
  adapter.lastRttNs = 1_000_000n;                    // 1 ms
  await adapter.send(true);                          // the heartbeat at 0 s, alone
  t = 1n * S;
  const change = adapter.outbox.push({ kind: 'change', acquisition: 'acq-1', dpe: SPEED, value_text: '1200',
    value_type: 'int', source_ns: t.toString(), quality: { bad: false, uncertain: false, good: true },
    observed_ns: t.toString() });
  t = 10n * S;
  const gap = adapter.pushGap('rig-1', null);
  const ob = adapter.outbox;
  if (bound === 'count') {
    ob.maxPerMessage = 2;
  } else if (bound === 'bytes') {
    // The envelope, the change and the gap fit (each with its separator); a heartbeat (about 150 bytes) does not.
    ob.maxBytes = Buffer.byteLength(JSON.stringify({ schema: 'morlet-winccoa/1', session: SESSION, observations: [] }))
      + Outbox.size(change) + 1 + Outbox.size(gap) + 1 + 10;
  } else {
    throw new Error(`unknown bound ${bound}`);
  }
  await adapter.send(true);
  return { posts, pending: ob.pending.length, maxBytes: ob.maxBytes, maxPerMessage: ob.maxPerMessage };
}

module.exports = { emit, SESSION };

if (require.main === module) {
  emit(process.argv[2]).then((r) => {
    process.stdout.write(`${JSON.stringify(r)}\n`);
  }, (e) => {
    console.error(e.stack || e.message);
    process.exit(1);
  });
}
