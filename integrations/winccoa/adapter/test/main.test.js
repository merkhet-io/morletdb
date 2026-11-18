// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The manager entry point: configuration loading, the exit callback (as PMON's exit signal would run it).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createFake } = require('./fake-winccoa');
const { run, bootstrapMain } = require('../src/main');
const { fakeCompanion, tokenFile, waitFor, SPEED } = require('./helpers');

test('run: starts from the configuration, and the exit callback stops cleanly', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'main-'));
  const tf = tokenFile(dir);
  const c = await fakeCompanion({ token: tf.token });
  t.after(async () => { await c.close(); fs.rmSync(dir, { recursive: true }); });
  const cfg = path.join(dir, 'adapter.json');
  fs.writeFileSync(cfg, JSON.stringify({ companion: { url: c.url, token_file: 'token' }, assets: [{ name: 'rig-1' }],
    datapoints: [{ dpe: SPEED, asset: 'rig-1' }], flush_linger_ms: 5 }));
  const { api, ctl } = createFake();
  ctl.define(SPEED, api.WinccoaElementType.Int, { value: 1, stimeNs: 1n, attrs: { bad: false, uncertain: false } });
  const winccoa = new api.WinccoaManager();
  await bootstrapMain(api, ['--config', cfg], { winccoa });
  const adapter = await run(api, ['--config', cfg], { winccoa });
  await waitFor(() => adapter.lastAckSeq >= 2);
  assert.equal(ctl.value('Morlet_Adapter.state'), 'ok');
  await ctl.triggerExit();
  assert.equal(ctl.value('Morlet_Adapter.state'), 'stopped');
  assert.equal(ctl.connections.size, 0);
  assert.equal(c.requests.at(-1).body.observations.at(-1).kind, 'gap');
});

test('run: an unusable configuration is logged and the manager exits with code 2', async () => {
  const { api, ctl } = createFake();
  const winccoa = new api.WinccoaManager();
  const r = await run(api, ['--config', path.join(os.tmpdir(), 'does-not-exist.json')], { winccoa });
  assert.equal(r, null);
  assert.equal(ctl.exitCode, 2);
  assert.match(ctl.logs.map((l) => l[1]).join(), /cannot read the configuration/);
});
