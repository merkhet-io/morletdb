// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// One-time bootstrap of the adapter's datapoint types and datapoints (idempotent), run as a one-time WinCC OA
// JavaScript Manager, for example:   -currentproj morlet-adapter/bootstrap.js --config config/adapter.json
// It touches only the adapter's namespace and exits when done (exit code 1 on a conflict, 2 on an error).

const { bootstrapMain } = require('./src/main');

async function main() {
  await bootstrapMain(require('winccoa-manager'), process.argv.slice(2));
}

main();
