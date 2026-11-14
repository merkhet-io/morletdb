// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The bundle: built from the commit, with a manifest whose hashes match the extracted files.
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { build } = require('../tools/bundle');

test('the bundle holds the adapter, templates, docs, LICENSE and a manifest that matches', () => {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'bundle-'));
  try {
    const r = build({ out, ref: 'HEAD', allowDirty: true });
    assert.equal(fs.readFileSync(`${r.tarball}.sha256`, 'utf8').split(' ')[0], r.digest);
    execFileSync('tar', ['-xzf', r.tarball, '-C', out]);
    const top = path.join(out, r.top);
    const manifest = JSON.parse(fs.readFileSync(path.join(top, 'MANIFEST.json'), 'utf8'));
    assert.deepEqual(manifest, r.manifest);
    assert.match(manifest.status, /awaiting validation in WinCC OA/);
    const paths = manifest.files.map((f) => f.path);
    for (const want of ['LICENSE', 'docs/winccoa.md', 'integrations/winccoa/README.md',
      'integrations/winccoa/adapter/index.js', 'integrations/winccoa/adapter/bootstrap.js',
      'integrations/winccoa/adapter/API.md', 'integrations/winccoa/adapter/config/adapter.example.json',
      'integrations/winccoa/companion/companion.example.toml']) {
      assert.ok(paths.includes(want), want);
    }
    assert.ok(!paths.some((p) => p.includes('/test/')));
    for (const f of manifest.files) {
      const data = fs.readFileSync(path.join(top, f.path));
      assert.equal(crypto.createHash('sha256').update(data).digest('hex'), f.sha256, f.path);
      assert.equal(data.length, f.bytes);
    }
    const listed = execFileSync('tar', ['-tzf', r.tarball], { encoding: 'utf8' }).split('\n').filter((l) => l && !l.endsWith('/'));
    assert.equal(listed.length, manifest.files.length + 1);
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
});
