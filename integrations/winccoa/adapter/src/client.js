// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The HTTP client of protocol `morlet-winccoa/1` (docs/winccoa.md section 4) over loopback, with Node's built-in
// fetch. The bearer token comes from a file with mode 0600 (section 5); it is re-read when the file changes, so a
// rotated token is picked up, and only its fingerprint (the first 8 hex digits of its SHA-256) is ever logged.

const crypto = require('node:crypto');
const fs = require('node:fs');

const SCHEMA = 'morlet-winccoa/1';

class TokenError extends Error {}

function fingerprint(token) {
  return crypto.createHash('sha256').update(token, 'utf8').digest('hex').slice(0, 8);
}

class TokenFile {
  constructor(file, { platform = process.platform } = {}) {
    this.file = file;
    this.platform = platform;
    this.mtimeMs = null;
    this.token = null;
    this.onLoad = null;
  }

  /** The current token (the first line). Throws TokenError if the file is missing, too open or too short. */
  read() {
    let st;
    try {
      st = fs.statSync(this.file);
    } catch (e) {
      throw new TokenError(`token file ${this.file} cannot be read: ${e.code || e.message}`);
    }
    // POSIX mode bits; on Windows they carry no access information, and the file's ACL must be checked by the
    // operator instead (docs/winccoa.md, troubleshooting).
    if (this.platform !== 'win32' && (st.mode & 0o077) !== 0) {
      throw new TokenError(`token file ${this.file} must have mode 0600 (it is ${(st.mode & 0o777).toString(8)})`);
    }
    if (this.token !== null && st.mtimeMs === this.mtimeMs) return this.token;
    const line = fs.readFileSync(this.file, 'utf8').split(/\r?\n/).map((s) => s.trim()).find((s) => s);
    if (!line || line.length < 32) throw new TokenError(`token file ${this.file} holds no token of at least 32 characters`);
    const changed = line !== this.token;
    this.token = line;
    this.mtimeMs = st.mtimeMs;
    if (changed && this.onLoad) this.onLoad(line, fingerprint(line));
    return line;
  }

  fingerprint() {
    return this.token ? fingerprint(this.token) : null;
  }
}

class Client {
  /**
   * @param {object} o
   * @param {string} o.url  loopback base URL, e.g. http://127.0.0.1:8770
   * @param {TokenFile} o.tokenFile
   * @param {number} o.timeoutMs
   * @param {Function} [o.fetch]
   */
  constructor({ url, tokenFile, timeoutMs, fetch = globalThis.fetch }) {
    this.base = url.replace(/\/$/, '');
    this.tokenFile = tokenFile;
    this.timeoutMs = timeoutMs;
    this.fetch = fetch;
  }

  /** {status, body, rttNs, error}. status 0: no HTTP response (connection refused, timeout, ...). */
  async request(method, path, body) {
    let token;
    try {
      token = this.tokenFile.read();
    } catch (e) {
      return { status: 0, body: null, rttNs: null, error: e.message, tokenError: true };
    }
    const headers = { Authorization: `Bearer ${token}`, Accept: 'application/json' };
    let data;
    if (body !== undefined) {
      data = typeof body === 'string' ? body : JSON.stringify(body);
      headers['Content-Type'] = 'application/json';
    }
    const t0 = process.hrtime.bigint();
    let res;
    try {
      res = await this.fetch(this.base + path, {
        method, headers, body: data, signal: AbortSignal.timeout(this.timeoutMs), redirect: 'error',
      });
    } catch (e) {
      return { status: 0, body: null, rttNs: null, error: describe(e) };
    }
    let text = '';
    try {
      text = await res.text();
    } catch (e) {
      return { status: 0, body: null, rttNs: null, error: describe(e) };
    }
    const rttNs = process.hrtime.bigint() - t0;
    let parsed = null;
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch (_) {
      parsed = { raw: text.slice(0, 200) };
    }
    return { status: res.status, body: parsed, rttNs, retryAfter: res.headers.get('retry-after') };
  }
}

function describe(e) {
  if (!e) return 'unknown error';
  const cause = e.cause && (e.cause.code || e.cause.message);
  return cause ? `${e.name}: ${cause}` : `${e.name}: ${e.message}`;
}

module.exports = { SCHEMA, Client, TokenFile, TokenError, fingerprint };
