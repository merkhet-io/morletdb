// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { timeToNs, encodeValue, floatText, valueTypeFor, Unrepresentable } = require('../src/values');
const { createFake } = require('./fake-winccoa');

test('times: Temporal nanoseconds exact, Date and integer milliseconds scaled, the rest refused', () => {
  assert.equal(timeToNs({ epochNanoseconds: 1_794_681_178_586_256_501n }), 1_794_681_178_586_256_501n);
  assert.equal(timeToNs(new Date(1_700_000_000_123)), 1_700_000_000_123_000_000n);
  assert.equal(timeToNs(1_700_000_000_123), 1_700_000_000_123_000_000n);
  assert.throws(() => timeToNs(1_700_000_000_123.5), Unrepresentable);          // never rounded
  assert.throws(() => timeToNs(new Date(NaN)), Unrepresentable);
  assert.throws(() => timeToNs(undefined), Unrepresentable);
  assert.throws(() => timeToNs(17n), Unrepresentable);
  assert.throws(() => timeToNs({ epochNanoseconds: 1n << 63n }), Unrepresentable);
});

test('floats keep their exact shortest text, signed zero and the special values', () => {
  assert.equal(floatText(0.1), '0.1');
  assert.equal(floatText(-0), '-0.0');
  assert.equal(floatText(0), '0');
  assert.equal(floatText(1e21), '1e+21');
  assert.equal(floatText(5e-324), '5e-324');
  assert.equal(floatText(NaN), 'NaN');
  assert.equal(floatText(-Infinity), '-Infinity');
  for (const x of [0.1, 1 / 3, -2.5e-300, 123456789.12345678, Number.MAX_VALUE]) assert.equal(Number(floatText(x)), x);
  assert.ok(Object.is(JSON.parse(floatText(-0)), -0));
});

test('values by type: integers exact (BigInt for 64-bit), strings as JSON text, nothing coerced', () => {
  assert.deepEqual(encodeValue(true, 'bool'), { value_text: 'true', value_type: 'bool' });
  assert.deepEqual(encodeValue(9_007_199_254_740_993n, 'int'), { value_text: '9007199254740993', value_type: 'int' });
  assert.deepEqual(encodeValue(42, 'int'), { value_text: '42', value_type: 'int' });
  assert.deepEqual(encodeValue(2.5, 'int'), { value_text: '2.5', value_type: 'float' });     // exact, not rounded
  assert.deepEqual(encodeValue('a "b"\n', 'string'), { value_text: '"a \\"b\\"\\n"', value_type: 'string' });
  assert.deepEqual(encodeValue([1, 2.5, -0, 3n], 'json'), { value_text: '[1,2.5,-0.0,3]', value_type: 'json' });
  assert.throws(() => encodeValue('1', 'int'), Unrepresentable);
  assert.throws(() => encodeValue(1, 'bool'), Unrepresentable);
  assert.throws(() => encodeValue([NaN], 'json'), Unrepresentable);
  assert.throws(() => encodeValue(new Date(0), 'json'), Unrepresentable);
});

test('element types map to value types; unsupported ones to none', () => {
  const E = createFake().api.WinccoaElementType;
  assert.equal(valueTypeFor(E.Int, E), 'int');
  assert.equal(valueTypeFor(E.Long, E), 'int');
  assert.equal(valueTypeFor(E.Float, E), 'float');
  assert.equal(valueTypeFor(E.Bool, E), 'bool');
  assert.equal(valueTypeFor(E.String, E), 'string');
  assert.equal(valueTypeFor(E.DynFloat, E), 'json');
  assert.equal(valueTypeFor(E.Time, E), null);
});
