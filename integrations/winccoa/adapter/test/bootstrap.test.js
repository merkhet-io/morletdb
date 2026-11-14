// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createFake } = require('./fake-winccoa');
const { bootstrap } = require('../src/bootstrap');
const { bootstrapMain } = require('../src/main');
const { Namespace } = require('../src/namespace');
const { makeLog } = require('../src/log');

function fake() {
  const { api, ctl } = createFake();
  const winccoa = new api.WinccoaManager();
  return { api, ctl, winccoa, log: makeLog(winccoa) };
}

test('idempotent: the second run creates and changes nothing', async () => {
  const f = fake();
  f.ctl.define('Rig1.speed', f.api.WinccoaElementType.Int, { value: 7 });
  const ns = new Namespace('Morlet', ['panel1', 'panel2']);
  const r1 = await bootstrap(f.winccoa, f.api, ns, f.log);
  assert.deepEqual(r1.created.sort(), ['datapoint Morlet_Adapter', 'datapoint Morlet_Inv_panel1',
    'datapoint Morlet_Inv_panel2', 'type MorletAdapter', 'type MorletInvestigation']);
  assert.deepEqual(r1.conflicts, []);
  const callsBefore = f.ctl.calls.filter((c) => ['dpCreate', 'dpTypeCreate'].includes(c)).length;
  const r2 = await bootstrap(f.winccoa, f.api, ns, f.log);
  assert.deepEqual(r2.created, []);
  assert.deepEqual(r2.conflicts, []);
  assert.equal(r2.existing.length, 5);
  assert.equal(f.ctl.calls.filter((c) => ['dpCreate', 'dpTypeCreate'].includes(c)).length, callsBefore);
  // Only the namespace was touched: no writes, and the asset datapoint is unchanged.
  assert.deepEqual(f.ctl.writes, []);
  assert.equal(f.ctl.value('Rig1.speed'), 7);
  assert.ok(f.ctl.created.every((n) => /^(type Morlet(Adapter|Investigation)|datapoint Morlet_(Adapter|Inv_panel[12]))$/.test(n)));
  // A slot added later: only its datapoint is created.
  const r3 = await bootstrap(f.winccoa, f.api, new Namespace('Morlet', ['panel1', 'panel2', 'panel3']), f.log);
  assert.deepEqual(r3.created, ['datapoint Morlet_Inv_panel3']);
});

test('a type or datapoint that exists with another shape is a conflict and left unchanged', async () => {
  const f = fake();
  const E = f.api.WinccoaElementType;
  const other = new f.api.WinccoaDpTypeNode('MorletAdapter', E.Struct, '', [new f.api.WinccoaDpTypeNode('state', E.Int)]);
  await f.winccoa.dpTypeCreate(other);
  await f.winccoa.dpTypeCreate(new f.api.WinccoaDpTypeNode('Other', E.Struct, '', [new f.api.WinccoaDpTypeNode('x', E.Int)]));
  await f.winccoa.dpCreate('Morlet_Inv_panel1', 'Other');
  const r = await bootstrap(f.winccoa, f.api, new Namespace('Morlet', ['panel1']), f.log);
  assert.deepEqual(r.conflicts.sort(), ['datapoint Morlet_Adapter', 'datapoint Morlet_Inv_panel1', 'type MorletAdapter']);
  assert.deepEqual(r.created, ['type MorletInvestigation']);
  assert.equal(f.winccoa.dpTypeGet('MorletAdapter'), other);
  assert.equal(f.winccoa.dpTypeName('Morlet_Inv_panel1'), 'Other');
});

test('the bootstrap entry point exits 0, then 0 again, and 1 on a conflict', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'boot-'));
  const cfg = path.join(dir, 'adapter.json');
  fs.writeFileSync(cfg, JSON.stringify({ companion: { token_file: 'token' }, assets: [{ name: 'rig-1' }] }));
  const f = fake();
  assert.equal((await bootstrapMain(f.api, ['--config', cfg], { winccoa: f.winccoa })).code, 0);
  assert.equal(f.ctl.exitCode, 0);
  assert.equal((await bootstrapMain(f.api, ['--config', cfg], { winccoa: f.winccoa })).code, 0);
  const g = fake();
  await g.winccoa.dpTypeCreate(new g.api.WinccoaDpTypeNode('MorletAdapter', g.api.WinccoaElementType.Struct, '', []));
  assert.equal((await bootstrapMain(g.api, ['--config', cfg], { winccoa: g.winccoa })).code, 1);
  assert.equal(g.ctl.exitCode, 1);
  fs.writeFileSync(cfg, '{}');
  assert.equal((await bootstrapMain(g.api, ['--config', cfg], { winccoa: g.winccoa })).code, 2);
  fs.rmSync(dir, { recursive: true });
});
