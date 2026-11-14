// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The adapter, started as a WinCC OA JavaScript Manager (WCCOAjsManager) with this script relative to the project's
// javascript directory, for example:   morlet-adapter/index.js --config config/adapter.json
// `winccoa-manager` is provided by the WinCC OA installation at run time; it is not part of this package.

const { run } = require('./src/main');

async function main() {
  await run(require('winccoa-manager'), process.argv.slice(2));
}

main();
