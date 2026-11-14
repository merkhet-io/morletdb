// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The adapter's own datapoint namespace (docs/winccoa.md: the adapter writes only there).
//
//   <prefix>_Adapter          type <prefix>Adapter        status and health of the adapter
//   <prefix>_Inv_<slot>       type <prefix>Investigation  one investigation slot (a UI panel): request in, result out
//
// Every write goes through `Namespace.write`, which refuses any element outside these datapoints.

const ADAPTER_ELEMENTS = [
  ['state', 'String'],             // starting | ok | degraded | error | stopped
  ['detail', 'String'],
  ['session', 'String'],
  ['companion', 'String'],         // ok | unreachable | unauthorized | version_mismatch | conflict | rejected
  ['version', 'String'],           // adapter version, API version and WinCC OA version
  ['tokenFingerprint', 'String'],
  ['lastAckSeq', 'Float'],         // doubles are exact up to 2^53, the protocol's seq range
  ['queued', 'Float'],
  ['dropped', 'Float'],
  ['gaps', 'Float'],
  ['lateDropped', 'Float'],
  ['rttMs', 'Float'],
  ['lastHeartbeat', 'String'],     // ISO 8601 UTC of the last acknowledged heartbeat
  ['health', 'String'],            // the companion's health report (JSON), as last received
];

const INVESTIGATION_ELEMENTS = [
  // Written by the panel:
  ['trigger', 'Int'],              // any change starts an investigation with the fields below
  ['collection', 'String'],
  ['recordingId', 'String'],
  ['startSample', 'Long'],
  ['endSample', 'Long'],
  ['topK', 'Int'],                 // 0: the companion's default
  // Written by the adapter, only while requestId is the slot's current request:
  ['requestId', 'String'],
  ['state', 'String'],             // idle | running | done | failed
  ['status', 'String'],            // ok | no_matches | evidence_unavailable | permission_denied | service_failure | ...
  ['detail', 'String'],
  ['matchCount', 'Int'],
  ['result', 'String'],            // the companion's result (JSON)
  ['updated', 'String'],           // ISO 8601 UTC
];

const INPUTS = ['trigger', 'collection', 'recordingId', 'startSample', 'endSample', 'topK'];

class NamespaceViolation extends Error {}

class Namespace {
  constructor(prefix, slots) {
    this.prefix = prefix;
    this.slots = slots;
    this.adapterType = `${prefix}Adapter`;
    this.investigationType = `${prefix}Investigation`;
    this.adapterDp = `${prefix}_Adapter`;
    this.slotDps = new Map(slots.map((s) => [s, `${prefix}_Inv_${s}`]));
    this.dps = new Set([this.adapterDp, ...this.slotDps.values()]);
  }

  slotDp(slot) {
    return this.slotDps.get(slot);
  }

  /** Throws NamespaceViolation unless `dpe` is an element of one of the namespace datapoints. */
  check(dpe) {
    const i = dpe.indexOf('.');
    const dp = i < 0 ? dpe : dpe.slice(0, i);
    const el = i < 0 ? '' : dpe.slice(i + 1);
    const bare = dp.includes(':') ? dp.slice(dp.indexOf(':') + 1) : dp;
    if (!this.dps.has(bare) || !el || el.includes(':')) {
      throw new NamespaceViolation(`refusing to write ${dpe}: outside the adapter's namespace`);
    }
    const elements = bare === this.adapterDp ? ADAPTER_ELEMENTS : INVESTIGATION_ELEMENTS;
    if (!elements.some(([n]) => n === el)) throw new NamespaceViolation(`refusing to write ${dpe}: unknown element`);
  }

  /** dpSetWait on namespace elements only. `values` maps element names of `dp` to values. */
  async write(winccoa, dp, values) {
    const names = Object.keys(values).map((k) => `${dp}.${k}`);
    names.forEach((n) => this.check(n));
    return winccoa.dpSetWait(names, Object.values(values));
  }

  /** The datapoint type trees (WinccoaDpTypeNode) of the namespace, built with the loaded API's classes. */
  typeTrees(api) {
    const E = api.WinccoaElementType;
    const node = (name, els) => new api.WinccoaDpTypeNode(name, E.Struct, '', els.map(([n, t]) => new api.WinccoaDpTypeNode(n, E[t])));
    return [node(this.adapterType, ADAPTER_ELEMENTS), node(this.investigationType, INVESTIGATION_ELEMENTS)];
  }

  /** [dp name, dp type] of every namespace datapoint. */
  datapoints() {
    return [[this.adapterDp, this.adapterType], ...[...this.slotDps.values()].map((d) => [d, this.investigationType])];
  }
}

module.exports = { Namespace, NamespaceViolation, ADAPTER_ELEMENTS, INVESTIGATION_ELEMENTS, INPUTS };
