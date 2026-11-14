// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Entry points for the WinCC OA JavaScript Manager. `api` is the `winccoa-manager` module, loaded only by the
// manager's scripts (index.js, bootstrap.js) at run time; tests pass a fake.

const path = require('node:path');
const { load, ConfigError } = require('./config');
const { makeLog } = require('./log');
const { Adapter } = require('./adapter');
const { Namespace } = require('./namespace');
const { bootstrap } = require('./bootstrap');

const DEFAULT_CONFIG = path.join(__dirname, '..', 'config', 'adapter.json');

function configPath(argv) {
  const i = argv.indexOf('--config');
  if (i >= 0 && argv[i + 1]) return path.resolve(path.join(__dirname, '..'), argv[i + 1]);
  return DEFAULT_CONFIG;
}

/** The long-running adapter. Returns the Adapter (or null when the configuration is unusable). */
async function run(api, argv, options = {}) {
  const winccoa = options.winccoa || new api.WinccoaManager();
  const log = makeLog(winccoa);
  let config;
  try {
    config = load(configPath(argv));
  } catch (e) {
    if (!(e instanceof ConfigError)) throw e;
    log.error(`configuration: ${e.message}`);
    winccoa.exit(2);
    return null;
  }
  const adapter = new Adapter({ api, winccoa, config, log, ...(options.adapter || {}) });
  winccoa.registerExitCallback(async () => {
    await adapter.stop();
  });
  try {
    await adapter.start();
  } catch (e) {
    log.error(`start failed: ${e.stack || e.message}`);
  }
  return adapter;
}

/** The one-time bootstrap: creates the namespace types and datapoints, then exits (1 on a conflict). */
async function bootstrapMain(api, argv, options = {}) {
  const winccoa = options.winccoa || new api.WinccoaManager();
  const log = makeLog(winccoa, 'morlet-adapter-bootstrap');
  let code = 0;
  let report = null;
  try {
    const config = load(configPath(argv));
    const ns = new Namespace(config.namespace.prefix, config.namespace.slots);
    report = await bootstrap(winccoa, api, ns, log);
    log.info(`created: ${report.created.join(', ') || 'nothing'}; already present: ${report.existing.join(', ') || 'nothing'}`
      + `; conflicts: ${report.conflicts.join(', ') || 'none'}`);
    if (report.conflicts.length) code = 1;
  } catch (e) {
    log.error(`bootstrap failed: ${e.message}`);
    code = 2;
  }
  winccoa.exit(code);
  return { code, report };
}

module.exports = { run, bootstrapMain, configPath };
