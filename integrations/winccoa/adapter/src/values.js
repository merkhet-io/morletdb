// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// Conversions from WinCC OA values to protocol `morlet-winccoa/1` fields. Nothing is rounded: a value or time that
// cannot be represented exactly is reported as such, never approximated.
//
// - Times are integer nanoseconds since the Unix epoch, as BigInt, sent as decimal strings. A WinccoaTime is a Date
//   (milliseconds), a number (milliseconds since the epoch) or a Temporal.Instant (nanoseconds, read through its
//   standard `epochNanoseconds` BigInt), depending on WinccoaOptions.timeFormat (API.md).
// - value_text is the exact JSON text of the value; value_type is bool, int, float, string or json.

const NS_PER_MS = 1_000_000n;
const INT64_MIN = -(1n << 63n);
const INT64_MAX = (1n << 63n) - 1n;

class Unrepresentable extends Error {}

/** A WinccoaTime as BigInt nanoseconds, or throws Unrepresentable. */
function timeToNs(v) {
  let ns;
  if (v instanceof Date) {
    const ms = v.getTime();
    if (!Number.isSafeInteger(ms)) throw new Unrepresentable('invalid Date');
    ns = BigInt(ms) * NS_PER_MS;
  } else if (typeof v === 'number') {
    // Milliseconds since the epoch. A fractional millisecond is not converted: a double's fraction is not an exact
    // number of nanoseconds in general, and rounding it would invent precision.
    if (!Number.isSafeInteger(v)) throw new Unrepresentable(`time ${v} is not an integer number of milliseconds`);
    ns = BigInt(v) * NS_PER_MS;
  } else if (v !== null && typeof v === 'object' && typeof v.epochNanoseconds === 'bigint') {
    ns = v.epochNanoseconds;     // Temporal.Instant
  } else if (typeof v === 'bigint') {
    throw new Unrepresentable('a BigInt time has no documented unit');
  } else {
    throw new Unrepresentable(`unsupported time value of type ${typeof v}`);
  }
  if (ns < INT64_MIN || ns > INT64_MAX) throw new Unrepresentable('time out of the 64-bit nanosecond range');
  return ns;
}

function nowNs() {
  // Wall-clock nanoseconds. Date.now() is integer milliseconds; process.hrtime.bigint() is monotonic, not wall
  // clock, so the wall clock is anchored once and advanced by the monotonic clock.
  return ANCHOR_WALL + (process.hrtime.bigint() - ANCHOR_MONO);
}
const ANCHOR_WALL = BigInt(Date.now()) * NS_PER_MS;
const ANCHOR_MONO = process.hrtime.bigint();

/** Shortest exact text of a double (round-trips), keeping signed zero; NaN and infinities by name. */
function floatText(x) {
  if (Number.isNaN(x)) return 'NaN';
  if (x === Infinity) return 'Infinity';
  if (x === -Infinity) return '-Infinity';
  if (Object.is(x, -0)) return '-0.0';
  return String(x);
}

function jsonText(v) {
  if (v === null) return 'null';
  switch (typeof v) {
    case 'boolean': return v ? 'true' : 'false';
    case 'bigint': return v.toString();
    case 'string': return JSON.stringify(v);
    case 'number':
      if (!Number.isFinite(v)) throw new Unrepresentable('NaN or an infinity has no JSON form inside a json value');
      return floatText(v);
    case 'object':
      if (Array.isArray(v)) return `[${v.map(jsonText).join(',')}]`;
      if (Object.getPrototypeOf(v) === Object.prototype) {
        return `{${Object.keys(v).map((k) => `${JSON.stringify(k)}:${jsonText(v[k])}`).join(',')}}`;
      }
      throw new Unrepresentable(`a ${v.constructor && v.constructor.name} value has no defined JSON form`);
    default:
      throw new Unrepresentable(`unsupported value of type ${typeof v}`);
  }
}

/** {value_text, value_type} of a value, given the datapoint's value type. Throws Unrepresentable. */
function encodeValue(v, valueType) {
  switch (valueType) {
    case 'bool':
      if (typeof v !== 'boolean') throw new Unrepresentable(`expected a bool, got ${typeof v}`);
      return { value_text: v ? 'true' : 'false', value_type: 'bool' };
    case 'int':
      if (typeof v === 'bigint') return { value_text: v.toString(), value_type: 'int' };
      if (typeof v === 'number' && Number.isSafeInteger(v)) return { value_text: String(v), value_type: 'int' };
      if (typeof v === 'number') return { value_text: floatText(v), value_type: 'float' };   // exact, not rounded
      throw new Unrepresentable(`expected an integer, got ${typeof v}`);
    case 'float':
      if (typeof v === 'number') return { value_text: floatText(v), value_type: 'float' };
      if (typeof v === 'bigint') return { value_text: v.toString(), value_type: 'float' };
      throw new Unrepresentable(`expected a number, got ${typeof v}`);
    case 'string':
      if (typeof v !== 'string') throw new Unrepresentable(`expected a string, got ${typeof v}`);
      return { value_text: JSON.stringify(v), value_type: 'string' };
    case 'json':
      return { value_text: jsonText(v), value_type: 'json' };
    default:
      throw new Unrepresentable(`unknown value type ${valueType}`);
  }
}

/** The protocol value type for a WinccoaElementType (the enum of the loaded API), or null when unsupported. */
function valueTypeFor(elementType, E) {
  if (!E) return null;
  if ([E.Bool].includes(elementType)) return 'bool';
  if ([E.Int, E.UInt, E.Long, E.ULong].includes(elementType)) return 'int';
  if (elementType === E.Float) return 'float';
  if (elementType === E.String) return 'string';
  if ([E.DynBool, E.DynInt, E.DynUInt, E.DynLong, E.DynULong, E.DynFloat, E.DynString].includes(elementType)) return 'json';
  return null;
}

module.exports = { Unrepresentable, timeToNs, nowNs, floatText, jsonText, encodeValue, valueTypeFor };
