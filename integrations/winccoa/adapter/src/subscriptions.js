// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Subscriptions to the configured asset datapoints, with value, source time and quality where the API offers them.
//
// Two modes (API.md); which of them delivers `_original.._stime` and the quality attributes is UNCONFIRMED and is
// settled in phase C:
//
// - "connect": WinccoaManager.dpConnect(callback, [<dpe>:<value attr>, <dpe>:<stime attr>, <dpe>:<quality attrs>...],
//   true). Documented: "Each update will contain updates for all elements in dpeNames", so value, time and quality
//   arrive together.
// - "query": WinccoaManager.dpQueryConnectSingle(callback, true, "SELECT '<value attr>', '<stime attr>', ... FROM
//   '<dpe>'"). Documented: the callback gets a table whose row 0 is a header.
//
// If the subscription with attributes is refused (dpConnect throws a WinccoaError, or the query callback carries
// one), and `fallback_to_value_only` is set, the adapter subscribes to the plain element instead: values then
// arrive without source time and quality, are sent as `source_status: "unreadable"` and `quality_status:
// "unreadable"`, and become no context (the companion records them as untimed). Missing evidence stays missing.

class Subscription {
  constructor(dp) {
    this.dp = dp;              // {dpe, asset, value_type, subscription}
    this.id = null;
    this.kind = null;          // 'connect' | 'query'
    this.valueOnly = false;
    this.columns = [];         // [{role: 'value'|'stime'|'quality', name}]
  }

  addresses() {
    const s = this.dp.subscription;
    const cols = [];
    cols.push({ role: 'value', attr: s.value_attribute });
    if (s.stime_attribute) cols.push({ role: 'stime', attr: s.stime_attribute });
    for (const [name, attr] of Object.entries(s.quality_attributes)) cols.push({ role: 'quality', name, attr });
    return cols;
  }
}

/**
 * Subscribe one datapoint. `onUpdate(sub, update)` gets {value, stime (raw WinccoaTime or undefined), quality
 * (raw attribute map or undefined), valueOnly, type} or {error, type}.
 */
function subscribe(winccoa, sub, onUpdate, log) {
  const s = sub.dp.subscription;
  const cols = sub.addresses();
  try {
    if (s.mode === 'query') {
      subscribeQuery(winccoa, sub, cols, onUpdate);
    } else {
      subscribeConnect(winccoa, sub, cols, onUpdate);
    }
    return true;
  } catch (e) {
    if (!s.fallback_to_value_only) throw e;
    log.warn(`subscription with attributes refused for ${sub.dp.dpe} (${errText(e)}); subscribing to the value only: `
      + 'no source time or quality, so no context from this datapoint');
    subscribeValueOnly(winccoa, sub, onUpdate);
    return false;
  }
}

function subscribeConnect(winccoa, sub, cols, onUpdate) {
  const names = cols.map((c) => (c.attr ? `${sub.dp.dpe}:${c.attr}` : sub.dp.dpe));
  sub.kind = 'connect';
  sub.valueOnly = false;
  sub.columns = cols;
  sub.id = winccoa.dpConnect((dpeNames, values, type, error) => {
    if (error) return onUpdate(sub, { error, type });
    onUpdate(sub, fromColumns(cols, values, type));
  }, names, true);
}

function subscribeQuery(winccoa, sub, cols, onUpdate) {
  const select = cols.map((c) => `'${c.attr}'`).join(', ');
  const query = `SELECT ${select} FROM '${sub.dp.dpe}'`;
  sub.kind = 'query';
  sub.valueOnly = false;
  sub.columns = cols;
  let first = true;
  sub.id = winccoa.dpQueryConnectSingle((rows, type, error) => {
    const wasFirst = first;
    first = false;
    if (error) {
      // Documented: an invalid query throws nothing, and the first (and only) callback carries the error.
      if (wasFirst && sub.dp.subscription.fallback_to_value_only) {
        try { winccoa.dpQueryDisconnect(sub.id); } catch (_) { /* already closed */ }
        subscribeValueOnly(winccoa, sub, onUpdate);
        return onUpdate(sub, { error, type, fellBack: true });
      }
      return onUpdate(sub, { error, type });
    }
    // Row 0 is the header; with FROM naming one element there is one data row.
    for (const row of (rows || []).slice(1)) onUpdate(sub, fromColumns(cols, row.slice(1), type));
  }, true, query);
}

function subscribeValueOnly(winccoa, sub, onUpdate) {
  sub.kind = 'connect';
  sub.valueOnly = true;
  sub.columns = [{ role: 'value' }];
  sub.id = winccoa.dpConnect((dpeNames, values, type, error) => {
    if (error) return onUpdate(sub, { error, type });
    onUpdate(sub, { value: values[0], valueOnly: true, type });
  }, [sub.dp.dpe], true);
}

function fromColumns(cols, values, type) {
  const u = { type, valueOnly: false, quality: undefined, stime: undefined };
  cols.forEach((c, i) => {
    if (c.role === 'value') u.value = values[i];
    else if (c.role === 'stime') u.stime = values[i];
    else {
      u.quality = u.quality || {};
      u.quality[c.name] = values[i];
    }
  });
  return u;
}

function unsubscribe(winccoa, sub) {
  if (sub.id === null) return;
  try {
    if (sub.kind === 'query') winccoa.dpQueryDisconnect(sub.id);
    else winccoa.dpDisconnect(sub.id);
  } finally {
    sub.id = null;
  }
}

function errText(e) {
  if (!e) return 'unknown error';
  return e.code !== undefined ? `${e.code}: ${e.message}` : (e.message || String(e));
}

module.exports = { Subscription, subscribe, unsubscribe, errText };
