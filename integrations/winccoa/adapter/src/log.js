// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Logging through the WinCC OA log (WinccoaManager.logInfo, logWarning, logSevere). The token never reaches a log
// line: callers log its fingerprint only, and `redact` removes it from any text as a second line of defence.

function makeLog(winccoa, prefix = 'morlet-adapter') {
  let secrets = [];
  const redact = (args) => args.map((a) => {
    let s = typeof a === 'string' ? a : (a instanceof Error ? `${a.name}: ${a.message}` : safeString(a));
    for (const t of secrets) if (t) s = s.split(t).join('<token>');
    return s;
  });
  const out = (fn, args) => {
    try {
      fn.call(winccoa, `[${prefix}]`, ...redact(args));
    } catch (_) { /* logging must never stop the adapter */ }
  };
  return {
    info: (...a) => out(winccoa.logInfo, a),
    warn: (...a) => out(winccoa.logWarning, a),
    error: (...a) => out(winccoa.logSevere, a),
    setSecrets: (list) => { secrets = list.filter(Boolean); },
  };
}

function safeString(v) {
  try {
    return JSON.stringify(v, (_, x) => (typeof x === 'bigint' ? x.toString() : x));
  } catch (_) {
    return String(v);
  }
}

module.exports = { makeLog, safeString };
