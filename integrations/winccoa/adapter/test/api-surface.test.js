// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The fake implements exactly the API surface listed in API.md, and the adapter uses nothing else.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createFake } = require('./fake-winccoa');

const ROOT = path.join(__dirname, '..');
const doc = fs.readFileSync(path.join(ROOT, 'API.md'), 'utf8');

function tableNames(heading) {
  const part = doc.split(`## ${heading}\n`)[1].split('\n## ')[0];
  return part.split('\n').filter((l) => l.startsWith('| `')).map((l) => /^\| `([A-Za-z_]+)`/.exec(l)[1]);
}

const sources = () => ['index.js', 'bootstrap.js', ...fs.readdirSync(path.join(ROOT, 'src')).map((f) => path.join('src', f))]
  .map((f) => fs.readFileSync(path.join(ROOT, f), 'utf8')).join('\n');

test('the fake implements exactly the documented methods and exports', () => {
  const { api } = createFake();
  const methods = Object.getOwnPropertyNames(api.WinccoaManager.prototype).sort();
  assert.deepEqual(methods, tableNames('WinccoaManager methods').sort());
  assert.deepEqual(Object.keys(api).sort(), tableNames('Module exports').sort());
});

test('the adapter calls only documented WinccoaManager methods and module exports', () => {
  const src = sources();
  const methods = new Set(tableNames('WinccoaManager methods'));
  const called = new Set([...src.matchAll(/(?<![/\w])winccoa\.([A-Za-z_]+)\b(?!\.md)/g)].map((m) => m[1]));
  assert.ok(called.has('logInfo') && called.has('dpConnect'));
  for (const m of called) assert.ok(methods.has(m), `winccoa.${m} is not in API.md`);
  const exportsUsed = new Set([...src.matchAll(/\bapi\.(Winccoa[A-Za-z_]*)\b/g)].map((m) => m[1]));
  const exportsDoc = new Set(tableNames('Module exports'));
  for (const e of exportsUsed) assert.ok(exportsDoc.has(e), `api.${e} is not in API.md`);
  // `winccoa-manager` is loaded only by the two manager scripts.
  for (const f of fs.readdirSync(path.join(ROOT, 'src'))) {
    assert.ok(!fs.readFileSync(path.join(ROOT, 'src', f), 'utf8').includes("require('winccoa-manager')"), f);
  }
  // Every documented member of WinccoaElementType the namespace uses exists in the fake.
  const { api } = createFake();
  const nsSrc = fs.readFileSync(path.join(ROOT, 'src', 'namespace.js'), 'utf8');
  for (const t of nsSrc.matchAll(/\['[a-zA-Z]+', '([A-Za-z]+)'\]/g)) assert.ok(t[1] in api.WinccoaElementType, t[1]);
});

test('no proprietary file or package is part of the adapter', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  assert.equal(pkg.dependencies, undefined);
  assert.equal(pkg.devDependencies, undefined);
  assert.ok(!fs.existsSync(path.join(ROOT, 'node_modules')));
  const all = [];
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p); else all.push(path.relative(ROOT, p));
  });
  walk(ROOT);
  assert.ok(!all.some((f) => /\.d\.ts$|\.node$|winccoa-manager/.test(f)), all.join());
});
