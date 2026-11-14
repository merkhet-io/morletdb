// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Test helpers: a scripted stand-in for the companion's HTTP interface (unit tests only; the end-to-end test uses
// the real companion), a token file with mode 0600, and a configured adapter on the fake WinCC OA API.

const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { createFake } = require('./fake-winccoa');
const { validate } = require('../src/config');
const { makeLog } = require('../src/log');
const { Adapter } = require('../src/adapter');

const SPEED = 'System1:Rig1.speed';
const LOAD = 'System1:Rig1.load';

function tokenFile(dir, token = crypto.randomBytes(32).toString('hex')) {
  const f = path.join(dir, 'token');
  fs.writeFileSync(f, `${token}\n`, { mode: 0o600 });
  fs.chmodSync(f, 0o600);
  return { file: f, token };
}

/** A companion stand-in: records requests; `handler(req)` returns {status, body} or undefined for the default. */
async function fakeCompanion({ token, acquisition = 'acq-1', handler } = {}) {
  const c = { requests: [], acquisition, handler, highest: -1, stored: new Map(), limits: null };
  c.server = http.createServer((req, res) => {
    let data = '';
    req.on('data', (d) => { data += d; });
    req.on('end', async () => {
      const r = { method: req.method, url: req.url, headers: req.headers, body: data ? JSON.parse(data) : null };
      c.requests.push(r);
      let out = c.handler ? await c.handler(r, c) : undefined;
      if (out === undefined) out = defaultResponse(r, c, token);
      if (out === null) return req.socket.destroy();       // a dropped connection
      const text = JSON.stringify(out.body || {});
      res.writeHead(out.status, { 'Content-Type': 'application/json', ...(out.headers || {}) });
      res.end(text);
    });
  });
  await new Promise((r) => c.server.listen(0, '127.0.0.1', r));
  c.port = c.server.address().port;
  c.url = `http://127.0.0.1:${c.port}`;
  c.close = () => new Promise((r) => { c.server.closeAllConnections(); c.server.close(r); });
  c.observations = () => c.requests.filter((x) => x.url === '/v1/observations' && x.status !== 'refused')
    .flatMap((x) => x.body.observations);
  return c;
}

function defaultResponse(r, c, token) {
  if (r.headers.authorization !== `Bearer ${token}`) return { status: 401, body: { error: 'unauthorized' } };
  if (r.url === '/v1/health') {
    return { status: 200, body: { schema: 'morlet-winccoa/1', status: 'ok',
      assets: [{ asset: 'rig-1', collection: 'plant.rig', current_acquisition: c.acquisition }],
      limits: c.limits || { observations_per_message: 1000, body_bytes: 1048576 } } };
  }
  if (r.url === '/v1/observations') {
    for (const o of r.body.observations) {
      const k = `${r.body.session}/${o.seq}`;
      const prev = c.stored.get(k);
      if (prev !== undefined && prev !== JSON.stringify(o)) return { status: 409, body: { error: 'conflict', seqs: [o.seq] } };
    }
    for (const o of r.body.observations) {
      c.stored.set(`${r.body.session}/${o.seq}`, JSON.stringify(o));
      c.highest = Math.max(c.highest, o.seq);
    }
    return { status: 200, body: { schema: 'morlet-winccoa/1', session: r.body.session, highest_seq: c.highest } };
  }
  if (r.url === '/v1/investigations' && r.method === 'POST') {
    return { status: 200, body: { schema: 'morlet-winccoa/1', request_id: r.body.request_id, slot: r.body.slot,
      status: 'ok', matches: [{ rank: 1 }], state: 'done', current: true, superseded_by: null } };
  }
  return { status: 404, body: { error: 'not_found' } };
}

function config(over = {}) {
  return {
    companion: { url: 'http://127.0.0.1:1', token_file: 'token', request_timeout_ms: 2000 },
    namespace: { prefix: 'Morlet', slots: ['panel1'] },
    heartbeat_interval_s: 3600,         // ticks are driven by the tests
    flush_linger_ms: 5,
    retry: { initial_ms: 20, max_ms: 80, investigation_attempts: 3, investigation_poll_ms: 10, investigation_wait_ms: 2000 },
    assets: [{ name: 'rig-1' }],
    datapoints: [{ dpe: SPEED, asset: 'rig-1' }, { dpe: LOAD, asset: 'rig-1' }],
    ...over,
  };
}

/** A fake WinCC OA with the asset datapoints and a bootstrapped namespace, and an adapter on it. */
async function setup({ companion: cOpts = {}, cfg = {}, fake: fOpts = {}, now } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'morlet-adapter-'));
  const tf = tokenFile(dir);
  const companion = await fakeCompanion({ token: tf.token, ...cOpts });
  const { api, ctl } = createFake(fOpts);
  const E = api.WinccoaElementType;
  ctl.define(SPEED, E.Int, { value: 1800, stimeNs: 1_700_000_000_000_000_000n, attrs: { bad: false, uncertain: false } });
  ctl.define(LOAD, E.String, { value: 'high', stimeNs: 1_700_000_000_000_000_000n, attrs: { bad: false, uncertain: false } });
  const winccoa = new api.WinccoaManager();
  const raw = config(cfg);
  raw.companion = { ...raw.companion, url: companion.url, token_file: tf.file, ...(cfg.companion || {}) };
  const conf = validate(raw, dir);
  const { Namespace } = require('../src/namespace');
  const { bootstrap } = require('../src/bootstrap');
  const ns = new Namespace(conf.namespace.prefix, conf.namespace.slots);
  await bootstrap(winccoa, api, ns, makeLog(winccoa));
  ctl.writes.length = 0;
  const log = makeLog(winccoa);
  const adapter = new Adapter({ api, winccoa, config: conf, log, ...(now ? { now } : {}) });
  const cleanup = async () => {
    await adapter.stop({ timeoutMs: 500 }).catch(() => {});
    await companion.close();
    fs.rmSync(dir, { recursive: true, force: true });
  };
  return { dir, tf, companion, api, ctl, winccoa, conf, adapter, cleanup };
}

async function waitFor(cond, { timeoutMs = 3000, stepMs = 5, what = 'condition' } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const v = await cond();
    if (v) return v;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, stepMs));
  }
}

const tick = () => new Promise((r) => setImmediate(r));

module.exports = { SPEED, LOAD, tokenFile, fakeCompanion, config, setup, waitFor, tick };
