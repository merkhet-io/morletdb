// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { validate, load, ConfigError, DEFAULTS } = require('../src/config');
const { config } = require('./helpers');

const base = () => config({ companion: { url: 'http://127.0.0.1:8770', token_file: '/x/token' } });

test('the defaults are those of docs/winccoa.md section Defaults', () => {
  const c = validate({ companion: { token_file: '/x/token' }, assets: [{ name: 'rig-1' }] });
  assert.equal(c.heartbeat_interval_s, 10);
  assert.equal(c.limits.observations_per_message, 1000);
  assert.equal(c.limits.body_bytes, 1 << 20);
  assert.equal(c.limits.datapoints, 500);
  assert.equal(c.companion.url, 'http://127.0.0.1:8770');
  assert.equal(DEFAULTS.subscription.stime_attribute, '_original.._stime');
});

test('comments are keys starting with an underscore', () => {
  const c = validate({ _comment: 'x', companion: { _why: 'y', token_file: '/x/token' }, assets: [{ name: 'a' }] });
  assert.equal(c.companion.token_file, '/x/token');
});

test('refused: remote companion, limits above the protocol, datapoints in the namespace, unknown keys', () => {
  const bad = [
    (c) => { c.companion.url = 'http://10.0.0.5:8770'; },
    (c) => { c.companion.url = 'https://127.0.0.1:8770'; },
    (c) => { c.companion.url = 'http://user:pw@127.0.0.1:8770'; },
    (c) => { c.companion.token_file = ''; },
    (c) => { c.limits = { observations_per_message: 1001 }; },
    (c) => { c.limits = { body_bytes: (1 << 20) + 1 }; },
    (c) => { c.datapoints.push({ dpe: 'Morlet_Adapter.state', asset: 'rig-1' }); },
    (c) => { c.datapoints.push({ dpe: 'System1:Morlet_Inv_panel1.trigger', asset: 'rig-1' }); },
    (c) => { c.datapoints.push({ dpe: 'Rig1.*', asset: 'rig-1' }); },
    (c) => { c.datapoints.push({ dpe: 'Rig1.x:_original.._value', asset: 'rig-1' }); },
    (c) => { c.datapoints.push({ dpe: c.datapoints[0].dpe, asset: 'rig-1' }); },
    (c) => { c.datapoints.push({ dpe: 'Rig1.y', asset: 'nope' }); },
    (c) => { c.datapoints[0].value_type = 'double'; },
    (c) => { c.surprise = 1; },
    (c) => { c.namespace.prefix = 'bad prefix'; },
    (c) => { c.namespace.slots = ['a', 'a']; },
    (c) => { c.subscription = { mode: 'poll' }; },
    (c) => { c.subscription = { good_when: { invalid: false } }; },
    (c) => { c.subscription = { quality_attributes: { bad: 'not an attribute' } }; },
    (c) => { c.time_format = 'iso'; },
    (c) => { c.assets = []; },
  ];
  for (const change of bad) {
    const c = base();
    change(c);
    assert.throws(() => validate(c), ConfigError, String(change));
  }
});

test('an internal datapoint name with a system prefix is a plain element, an attribute address is not', () => {
  const c = base();
  c.datapoints.push({ dpe: 'System1:_mp_Pump1.state', asset: 'rig-1' });
  assert.equal(validate(c).datapoints.at(-1).dpe, 'System1:_mp_Pump1.state');
  c.datapoints.push({ dpe: 'System1:Rig1.x:_online.._value', asset: 'rig-1' });
  assert.throws(() => validate(c), /no wildcards or attributes/);
});

test('at most 500 datapoints', () => {
  const c = base();
  c.datapoints = Array.from({ length: 501 }, (_, i) => ({ dpe: `Rig1.x${i}`, asset: 'rig-1' }));
  assert.throws(() => validate(c), /limit is 500/);
  c.datapoints.pop();
  assert.equal(validate(c).datapoints.length, 500);
});

test('the shipped template is valid, and the token file is resolved next to the configuration', () => {
  const file = path.join(__dirname, '..', 'config', 'adapter.example.json');
  const c = load(file);
  assert.equal(path.dirname(c.companion.token_file), path.resolve(path.dirname(file)));
  assert.ok(c.datapoints.length > 0);
  assert.throws(() => load(path.join(__dirname, 'nope.json')), ConfigError);
  const tmp = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'cfg-'));
  fs.writeFileSync(path.join(tmp, 'c.json'), '{not json');
  assert.throws(() => load(path.join(tmp, 'c.json')), /not JSON/);
  fs.rmSync(tmp, { recursive: true });
});
