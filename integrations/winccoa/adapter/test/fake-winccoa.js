// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// A fake of the `winccoa-manager` module (WinCC OA JavaScript Manager API 2.6.6), written from the public
// documentation, for tests only. It implements exactly the surface listed in ../API.md and nothing more
// (test/api-surface.test.js checks that). Behaviour it does NOT know (for example which attributes dpConnect
// really accepts) is a switch of the controller, so tests cover both outcomes.

// Enumerations, values as documented.
const WinccoaConnectUpdateType = Object.freeze({ Normal: 0, Answer: 1, Refresh: 2 });
const WinccoaTimeFormat = Object.freeze({ Date: 0, Number: 1, Temporal: 2 });
const WinccoaElementType = Object.freeze({
  Struct: 1, Int: 21, UInt: 20, Long: 54, ULong: 58, Float: 22, Bool: 23, String: 25, Time: 26, Typeref: 41,
  DynInt: 5, DynFloat: 6, DynBool: 7, DynString: 9,
});

class WinccoaError extends Error {
  constructor(code, message, catalog = '', details = undefined) {
    super(message);
    this.name = 'WinccoaError';
    this.code = code;
    this.catalog = catalog;
    this.details = details;
  }
}

class WinccoaDpTypeNode {
  constructor(name, type, refName = '', children = [], newName = '') {
    this.name = name;
    this.type = type;
    this.refName = refName;
    this.children = children;
    this.newName = newName;
  }
}

const DEFAULTS = { [WinccoaElementType.Int]: 0, [WinccoaElementType.UInt]: 0, [WinccoaElementType.Float]: 0,
  [WinccoaElementType.Bool]: false, [WinccoaElementType.String]: '', [WinccoaElementType.Long]: 0n,
  [WinccoaElementType.ULong]: 0n };

/** The controller of one fake WinCC OA system, and the module object handed to the adapter. */
function createFake({ system = 'System1', acceptAttributes = true, temporal = true, async = true } = {}) {
  const ctl = {
    system,
    acceptAttributes,
    temporal,
    elements: new Map(),     // bare dpe -> {type, value, stimeNs, attrs}
    types: new Map(),        // type name -> WinccoaDpTypeNode
    dps: new Map(),          // dp name -> type name
    connections: new Map(),  // id -> {kind, names, callback, query}
    writes: [],              // [{names, values}] of every dpSetWait
    created: [],             // names created by dpCreate / dpTypeCreate
    logs: [],
    options: { longAsBigInt: false, timeFormat: WinccoaTimeFormat.Date },
    exitCode: null,
    exitCallback: null,
    nextId: 0,
    calls: [],
  };

  const bare = (name) => (name.startsWith(`${system}:`) ? name.slice(system.length + 1) : name);
  const split = (address) => {
    const b = bare(address);
    const i = b.indexOf(':_');
    return i < 0 ? { dpe: b, attr: null } : { dpe: b.slice(0, i), attr: b.slice(i + 1) };
  };
  const timeOut = (ns) => {
    if (ns === undefined || ns === null) return undefined;
    const f = ctl.options.timeFormat;
    if (f === WinccoaTimeFormat.Temporal) return { epochNanoseconds: ns };     // a Temporal.Instant stand-in
    const ms = Number(ns / 1_000_000n);
    return f === WinccoaTimeFormat.Number ? ms : new Date(ms);
  };
  const out = (v) => (typeof v === 'bigint' && !ctl.options.longAsBigInt ? Number(v) : v);
  const read = (address) => {
    const { dpe, attr } = split(address);
    const el = ctl.elements.get(dpe);
    if (!el) throw new WinccoaError(-1, `DP does not exist: ${address}`);
    if (attr === null || attr === '_original.._value' || attr === '_online.._value') return out(el.value);
    if (attr === '_original.._stime' || attr === '_online.._stime') return timeOut(el.stimeNs);
    const name = attr.split('..')[1].replace(/^_/, '');
    return el.attrs && name in el.attrs ? el.attrs[name] : false;
  };
  const check = (address) => {
    const { dpe, attr } = split(address);
    if (!ctl.elements.has(dpe)) throw new WinccoaError(-1, `unknown data point element ${address}`);
    if (attr !== null && !ctl.acceptAttributes) throw new WinccoaError(-2, `attribute not accepted: ${address}`);
  };
  const deliver = (fn) => (async ? queueMicrotask(fn) : fn());

  ctl.notify = (dpe, type = WinccoaConnectUpdateType.Normal) => {
    for (const [, c] of ctl.connections) {
      if (!c.dpes.includes(dpe)) continue;
      if (c.kind === 'connect') {
        const values = c.names.map(read);
        deliver(() => c.callback(c.names.slice(), values, type));
      } else {
        const values = [['', ...c.attrs.map((a) => `:${a}`)], [`${system}:${dpe}`, ...c.attrs.map((a) => read(`${dpe}:${a}`))]];
        deliver(() => c.callback(values, type));
      }
    }
  };
  ctl.define = (dpe, type, { value, stimeNs, attrs } = {}) => {
    ctl.elements.set(bare(dpe), { type, value: value === undefined ? DEFAULTS[type] : value, stimeNs, attrs: attrs || {} });
  };
  ctl.change = (dpe, value, { stimeNs, attrs } = {}) => {
    const el = ctl.elements.get(bare(dpe));
    el.value = value;
    el.stimeNs = stimeNs;
    if (attrs) el.attrs = attrs;
    ctl.notify(bare(dpe));
  };
  ctl.refresh = (dpe) => ctl.notify(bare(dpe), WinccoaConnectUpdateType.Refresh);
  ctl.value = (dpe) => {
    const el = ctl.elements.get(bare(dpe));
    return el && el.value;
  };
  ctl.connectionError = (dpe, error) => {
    for (const [, c] of ctl.connections) {
      if (!c.dpes.includes(bare(dpe))) continue;
      if (c.kind === 'connect') deliver(() => c.callback(undefined, undefined, undefined, error));
      else deliver(() => c.callback(undefined, undefined, error));
    }
  };
  ctl.triggerExit = async () => {
    if (ctl.exitCallback) await ctl.exitCallback();
  };

  class WinccoaManager {
    constructor(options) {
      if (options) this.setOptions(options);
    }

    dpConnect(callback, dpeNames, answer = true) {
      ctl.calls.push('dpConnect');
      const names = Array.isArray(dpeNames) ? dpeNames : [dpeNames];
      names.forEach(check);
      const id = ctl.nextId++;
      ctl.connections.set(id, { kind: 'connect', names, callback, dpes: names.map((n) => split(n).dpe) });
      if (answer) {
        const values = names.map(read);
        deliver(() => callback(names.slice(), values, WinccoaConnectUpdateType.Answer));
      }
      return id;
    }

    dpDisconnect(id) {
      ctl.calls.push('dpDisconnect');
      if (!ctl.connections.has(id) || ctl.connections.get(id).kind !== 'connect') throw new WinccoaError(-3, `no connection ${id}`);
      ctl.connections.delete(id);
      return id;
    }

    dpQueryConnectSingle(callback, answer, query, blockingTime) {
      ctl.calls.push('dpQueryConnectSingle');
      const id = ctl.nextId++;
      const m = /^SELECT (.+) FROM '([^']+)'$/.exec(query);
      const attrs = m ? m[1].split(',').map((s) => s.trim().replace(/^'|'$/g, '')) : [];
      const dpe = m ? bare(m[2]) : null;
      const valid = m && ctl.elements.has(dpe) && (ctl.acceptAttributes || attrs.length === 0);
      if (!valid) {
        // Documented: an invalid query throws nothing; the first (and only) callback carries the error.
        deliver(() => callback(undefined, undefined, new WinccoaError(-4, `invalid query: ${query}`)));
        ctl.connections.set(id, { kind: 'query', dpes: [], attrs: [], callback });
        return id;
      }
      ctl.connections.set(id, { kind: 'query', dpes: [dpe], attrs, callback, blockingTime });
      if (answer) {
        const values = [['', ...attrs.map((a) => `:${a}`)], [`${system}:${dpe}`, ...attrs.map((a) => read(`${dpe}:${a}`))]];
        deliver(() => callback(values, WinccoaConnectUpdateType.Answer));
      }
      return id;
    }

    dpQueryDisconnect(id) {
      ctl.calls.push('dpQueryDisconnect');
      if (!ctl.connections.has(id) || ctl.connections.get(id).kind !== 'query') throw new WinccoaError(-3, `no query ${id}`);
      ctl.connections.delete(id);
      return id;
    }

    async dpSetWait(dpeNames, values) {
      ctl.calls.push('dpSetWait');
      const names = Array.isArray(dpeNames) ? dpeNames : [dpeNames];
      const vals = Array.isArray(dpeNames) ? values : [values];
      if (names.length !== vals.length) throw new WinccoaError(-5, 'array sizes mismatch');
      names.forEach((n) => {
        if (!ctl.elements.has(split(n).dpe)) throw new WinccoaError(-1, `DP does not exist: ${n}`);
      });
      ctl.writes.push({ names: names.slice(), values: vals.slice() });
      names.forEach((n, i) => { ctl.elements.get(split(n).dpe).value = vals[i]; });
      for (const n of new Set(names.map((x) => split(x).dpe))) ctl.notify(n);
      return true;
    }

    dpElementType(dpeName) {
      ctl.calls.push('dpElementType');
      const el = ctl.elements.get(bare(dpeName));
      if (!el) throw new WinccoaError(-1, `DP does not exist: ${dpeName}`);
      return el.type;
    }

    dpExists(dpeName) {
      ctl.calls.push('dpExists');
      const b = bare(dpeName);
      return ctl.dps.has(b) || ctl.elements.has(b) || ctl.types.has(b);
    }

    async dpCreate(dpeName, dpType) {
      ctl.calls.push('dpCreate');
      const b = bare(dpeName);
      if (ctl.dps.has(b)) throw new WinccoaError(-6, `data point ${dpeName} already exists`);
      const t = ctl.types.get(dpType);
      if (!t) throw new WinccoaError(-7, `invalid dpType ${dpType}`);
      ctl.dps.set(b, dpType);
      for (const c of t.children) ctl.define(`${b}.${c.name}`, c.type);
      ctl.created.push(`datapoint ${b}`);
      return true;
    }

    async dpTypeCreate(startNode) {
      ctl.calls.push('dpTypeCreate');
      if (!(startNode instanceof WinccoaDpTypeNode) || !startNode.name) throw new WinccoaError(-8, 'invalid argument');
      if (ctl.types.has(startNode.name)) throw new WinccoaError(-9, `type ${startNode.name} exists`);
      ctl.types.set(startNode.name, startNode);
      ctl.created.push(`type ${startNode.name}`);
      return true;
    }

    dpTypeGet(dpt) {
      ctl.calls.push('dpTypeGet');
      const t = ctl.types.get(dpt);
      if (!t) throw new WinccoaError(-10, `dpt ${dpt} does not exist`);
      return t;
    }

    dpTypes(pattern = '') {
      ctl.calls.push('dpTypes');
      const re = new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);
      return [...ctl.types.keys()].filter((n) => pattern === '' || re.test(n)).sort();
    }

    dpTypeName(dp) {
      ctl.calls.push('dpTypeName');
      const t = ctl.dps.get(bare(dp));
      if (!t) throw new WinccoaError(-1, `dp ${dp} does not exist`);
      return t;
    }

    setOptions(options) {
      ctl.calls.push('setOptions');
      if ('timeFormat' in options) {
        if (!Object.values(WinccoaTimeFormat).includes(options.timeFormat)) throw new WinccoaError(-11, 'option out of range');
        if (options.timeFormat === WinccoaTimeFormat.Temporal && !ctl.temporal) throw new WinccoaError(-11, 'Temporal unavailable');
      }
      Object.assign(ctl.options, options);
      return true;
    }

    getVersionInfo() {
      ctl.calls.push('getVersionInfo');
      return { api: { version: 0 }, winccoa: { display: 'fake', major: 3, minor: 21, numeric: 321000,
        numeric_full: 321000, patch: 0, platform: 'fake', revision: 0, version: 'fake' } };
    }

    registerExitCallback(callback) {
      ctl.calls.push('registerExitCallback');
      ctl.exitCallback = callback;
    }

    exit(exitCode = 0) {
      ctl.calls.push('exit');
      ctl.exitCode = exitCode;
    }

    logInfo(...args) { ctl.logs.push(['info', args.join(' ')]); }

    logWarning(...args) { ctl.logs.push(['warning', args.join(' ')]); }

    logSevere(...args) { ctl.logs.push(['severe', args.join(' ')]); }
  }

  const api = { WinccoaManager, WinccoaConnectUpdateType, WinccoaTimeFormat, WinccoaElementType, WinccoaDpTypeNode,
    WinccoaError };
  return { api, ctl };
}

module.exports = { createFake };
