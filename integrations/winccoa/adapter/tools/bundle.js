// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Build the reviewable adapter bundle from a commit (default HEAD) of a clean checkout:
//
//   node integrations/winccoa/adapter/tools/bundle.js [--out DIR] [--ref REF] [--allow-dirty]
//
// The tarball morlet-winccoa-adapter-<version>.tar.gz holds, under one top directory and at their repository paths,
// the adapter (without its tests), the configuration templates, the bootstrap, the docs, LICENSE and MANIFEST.json
// (version, commit, and the SHA-256 and size of every file). Content comes from the commit through `git archive`,
// never from the working tree, so the bundle is what was reviewed. A sibling .sha256 file holds the tarball's hash.
// It contains no WinCC OA file: `winccoa-manager` comes with the WinCC OA installation.

const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ADAPTER = 'integrations/winccoa/adapter';
const EXTRA = ['LICENSE', 'docs/winccoa.md', 'integrations/winccoa/README.md',
  'integrations/winccoa/companion/companion.example.toml'];
const STATUS = 'experimental WinCC OA adapter, awaiting validation in WinCC OA';

function git(args, opts = {}) {
  return execFileSync('git', args, { encoding: opts.encoding === undefined ? 'utf8' : opts.encoding, cwd: opts.cwd,
    maxBuffer: 64 << 20 });
}

function parse(argv) {
  const o = { out: 'dist', ref: 'HEAD', allowDirty: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--out') o.out = argv[++i];
    else if (argv[i] === '--ref') o.ref = argv[++i];
    else if (argv[i] === '--allow-dirty') o.allowDirty = true;
    else throw new Error(`unknown argument ${argv[i]}`);
  }
  return o;
}

function build(opts) {
  const root = git(['rev-parse', '--show-toplevel'], { cwd: __dirname }).trim();
  const commit = git(['rev-parse', '--verify', `${opts.ref}^{commit}`], { cwd: root }).trim();
  const paths = [ADAPTER, ...EXTRA];
  const dirty = git(['status', '--porcelain', '--', ...paths], { cwd: root }).trim();
  if (dirty && !opts.allowDirty) {
    throw new Error(`the checkout has changes under the bundled paths; commit them or pass --allow-dirty `
      + `(the bundle is built from ${opts.ref} either way):\n${dirty}`);
  }
  const files = git(['ls-tree', '-r', '--name-only', commit, '--', ...paths], { cwd: root }).split('\n')
    .filter((f) => f && !f.startsWith(`${ADAPTER}/test/`)).sort();
  for (const want of EXTRA) if (!files.includes(want)) throw new Error(`${want} is missing at ${commit}`);
  const pkg = JSON.parse(git(['show', `${commit}:${ADAPTER}/package.json`], { cwd: root }));
  const committed = git(['show', '-s', '--format=%cI', commit], { cwd: root }).trim();
  const manifest = {
    name: pkg.name, version: pkg.version, status: STATUS, license: 'Apache-2.0', commit, committed,
    note: 'WinCC OA is installed and licensed separately; community-maintained; no Siemens certification or partnership.',
    files: files.map((f) => {
      const data = git(['show', `${commit}:${f}`], { cwd: root, encoding: 'buffer' });
      return { path: f, sha256: crypto.createHash('sha256').update(data).digest('hex'), bytes: data.length };
    }),
  };
  const top = `${pkg.name}-${pkg.version}`;
  const out = path.resolve(root, opts.out);
  fs.mkdirSync(out, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'morlet-bundle-'));
  try {
    const mf = path.join(tmp, 'MANIFEST.json');
    fs.writeFileSync(mf, `${JSON.stringify(manifest, null, 2)}\n`);
    const tarball = path.join(out, `${top}.tar.gz`);
    git(['archive', '--format=tar.gz', `--prefix=${top}/`, `--add-file=${mf}`, '-o', tarball, commit, '--', ...files],
      { cwd: root });
    const digest = crypto.createHash('sha256').update(fs.readFileSync(tarball)).digest('hex');
    fs.writeFileSync(`${tarball}.sha256`, `${digest}  ${path.basename(tarball)}\n`);
    return { tarball, digest, manifest, top };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

if (require.main === module) {
  try {
    const r = build(parse(process.argv.slice(2)));
    process.stdout.write(`${r.tarball}\n${r.manifest.files.length} files, commit ${r.manifest.commit}, sha256 ${r.digest}\n`);
  } catch (e) {
    process.stderr.write(`bundle: ${e.message}\n`);
    process.exit(1);
  }
}

module.exports = { build, parse, STATUS };
