// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The adapter's outgoing observations: one seq per observation and session, messages of at most
// `observations_per_message` observations and `body_bytes` bytes, and resending until acknowledged.
//
// - change, snapshot and gap are kept until a 200 acknowledges the message holding them; an unacknowledged
//   observation is resent with the same seq and the same body, which the companion treats as a no-op.
// - Heartbeats are not kept: each message gets fresh heartbeats, created just before it is sent, so their
//   observed time is the time of sending (the companion's clock estimate needs that). A heartbeat whose message
//   failed is not resent; its seq is simply never used again (seqs need not be contiguous, only unique).
// - Heartbeats go after the last gap of a message: a gap ends open states at the last contact before it, and a
//   heartbeat sent now must not count as contact during the outage the gap reports. For the same reason no
//   heartbeat goes into a message before the one holding the last pending gap: while that gap does not fit beside
//   the heartbeats, messages carry none (#496).

class Outbox {
  constructor({ maxPerMessage, maxBytes, maxQueued }) {
    this.maxPerMessage = maxPerMessage;
    this.maxBytes = maxBytes;
    this.maxQueued = maxQueued;
    this.seq = 0;
    this.pending = [];           // [{obs, bytes}]
    this.dropped = 0;
  }

  nextSeq() {
    if (this.seq >= Number.MAX_SAFE_INTEGER) throw new Error('seq space of this session exhausted');
    return this.seq++;
  }

  static size(obs) {
    return Buffer.byteLength(JSON.stringify(obs), 'utf8');
  }

  /** Whether one more observation can be queued (a gap may always be queued: it reports the loss). */
  full() {
    return this.pending.length >= this.maxQueued;
  }

  /** The largest observation that fits in a message beside its envelope and some heartbeats. */
  maxObservationBytes(session, heartbeatCount) {
    return this.maxBytes - envelopeBytes(session) - heartbeatCount * 400;
  }

  /** Queue a reliable observation (change, snapshot or gap); returns it with its seq. */
  push(fields) {
    const obs = { seq: this.nextSeq(), ...fields };
    this.pending.push({ obs, bytes: Outbox.size(obs) });
    return obs;
  }

  /**
   * The next message: a prefix of the pending observations plus `heartbeats` (field objects without seq), within
   * both limits. Returns {body, sent, heartbeats} where `sent` is the number of pending observations included and
   * `heartbeats` the heartbeats actually included.
   *
   * The heartbeats go in only when the prefix chosen WITH them (they take room by count and by bytes) still holds
   * the last pending gap; otherwise the message carries none and their seqs are given back (#496). A heartbeat in an
   * earlier message than that gap would be committed before it and count as contact during the outage.
   */
  message(session, heartbeats = []) {
    const lastGap = this.pending.map((p) => p.obs.kind).lastIndexOf('gap');
    if (heartbeats.length) {
      const seq0 = this.seq;
      const m = this.build(session, heartbeats.map((h) => ({ seq: this.nextSeq(), ...h })));
      if (m.sent > lastGap) return m;
      this.seq = seq0;               // never sent: the next heartbeats take these seqs
    }
    return this.build(session, []);
  }

  build(session, hbs) {
    const hbBytes = hbs.reduce((n, h) => n + Outbox.size(h) + 1, 0);
    let bytes = envelopeBytes(session) + hbBytes;
    let room = this.maxPerMessage - hbs.length;
    const take = [];
    for (const p of this.pending) {
      if (room <= 0 || bytes + p.bytes + 1 > this.maxBytes) break;
      take.push(p.obs);
      bytes += p.bytes + 1;
      room -= 1;
    }
    let lastGap = -1;
    take.forEach((o, i) => { if (o.kind === 'gap') lastGap = i; });
    const observations = [...take.slice(0, lastGap + 1), ...hbs, ...take.slice(lastGap + 1)];
    return { body: { schema: 'morlet-winccoa/1', session, observations }, sent: take.length, heartbeats: hbs };
  }

  /** The first `n` pending observations were acknowledged. */
  acknowledge(n) {
    this.pending.splice(0, n);
  }
}

function envelopeBytes(session) {
  return Buffer.byteLength(JSON.stringify({ schema: 'morlet-winccoa/1', session, observations: [] }), 'utf8');
}

module.exports = { Outbox };
