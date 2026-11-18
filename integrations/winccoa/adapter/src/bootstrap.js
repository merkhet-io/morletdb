// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Idempotent creation of the adapter's datapoint types and datapoints, in its namespace only.
//
// - A type that does not exist is created with WinccoaManager.dpTypeCreate. A type that exists with the same
//   structure (dpTypeGet) is left alone; one with another structure is a conflict and is NOT changed.
// - A datapoint that does not exist is created with dpCreate. One that exists with the expected type (dpTypeName)
//   is left alone; one with another type is a conflict and is NOT changed.
// Running it again changes nothing. Nothing outside the namespace is created, changed or deleted.

function shape(node) {
  return {
    name: node.name,
    type: node.type,
    refName: node.refName || '',
    children: (node.children || []).map(shape).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)),
  };
}

function same(a, b) {
  return JSON.stringify(shape(a)) === JSON.stringify(shape(b));
}

/** {created: [], existing: [], conflicts: []}; entries are "type <name>" or "datapoint <name>". */
async function bootstrap(winccoa, api, namespace, log) {
  const report = { created: [], existing: [], conflicts: [] };
  const types = new Set(winccoa.dpTypes(`${namespace.prefix}*`));
  for (const tree of namespace.typeTrees(api)) {
    const what = `type ${tree.name}`;
    if (types.has(tree.name)) {
      const have = winccoa.dpTypeGet(tree.name);
      if (same(have, tree)) report.existing.push(what);
      else {
        report.conflicts.push(what);
        log.error(`${what} exists with another structure; it is not changed. Rename the namespace prefix or `
          + 'remove the type by hand after checking what uses it');
      }
      continue;
    }
    await winccoa.dpTypeCreate(tree);
    report.created.push(what);
    log.info(`created ${what}`);
  }
  const conflictTypes = new Set(report.conflicts.map((c) => c.slice(5)));
  for (const [dp, type] of namespace.datapoints()) {
    const what = `datapoint ${dp}`;
    if (winccoa.dpExists(dp)) {
      let have;
      try {
        have = winccoa.dpTypeName(dp);
      } catch (e) {
        have = `unknown (${e.message})`;
      }
      if (have === type) report.existing.push(what);
      else {
        report.conflicts.push(what);
        log.error(`${what} exists with type ${have}, not ${type}; it is not changed`);
      }
      continue;
    }
    if (conflictTypes.has(type)) {
      report.conflicts.push(what);
      log.error(`${what} not created: its type ${type} is in conflict`);
      continue;
    }
    await winccoa.dpCreate(dp, type);
    report.created.push(what);
    log.info(`created ${what}`);
  }
  return report;
}

module.exports = { bootstrap, same };
