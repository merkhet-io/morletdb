// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The adapter on the fake WinCC OA API against a scripted companion stand-in: subscriptions, snapshots, batching,
// retries, refusals, gaps, heartbeats, status and shutdown. The real companion is exercised by test/e2e.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { setup, waitFor, SPEED, LOAD } = require('./helpers');
const { Namespace, NamespaceViolation } = require('../src/namespace');

const GOOD = { bad: false, uncertain: false };
const T1 = 1_794_000_000_123_456_789n;

async function started(t, opts) {
  const s = await setup(opts);
  t.after(s.cleanup);
  await s.adapter.start();
  return s;
}

const obsPosts = (c) => c.requests.filter((r) => r.url === '/v1/observations');
const sent = (c) => obsPosts(c).flatMap((r) => r.body.observations);
const idle = (a) => a.outbox.pending.length === 0 && !a.flushing && !a.flushTimer;

test('start: a gap, a heartbeat with RTT, then the initial values as snapshots with source time and quality', async (t) => {
  const s = await started(t);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  const obs = sent(s.companion);
  assert.deepEqual(obs.map((o) => o.kind), ['gap', 'heartbeat', 'snapshot', 'snapshot']);
  assert.equal(obs[0].asset, 'rig-1');
  assert.equal(obs[0].dpe, undefined);
  assert.match(obs[1].rtt_ns, /^\d+$/);
  assert.match(obs[1].observed_ns, /^\d{19}$/);
  const speed = obs.find((o) => o.dpe === SPEED);
  assert.deepEqual({ ...speed, seq: 0, observed_ns: '' }, { seq: 0, kind: 'snapshot', acquisition: 'acq-1', dpe: SPEED,
    value_text: '1800', value_type: 'int', source_ns: '1700000000000000000',
    quality: { bad: false, uncertain: false, good: true }, observed_ns: '' });
  assert.equal(obs.find((o) => o.dpe === LOAD).value_text, '"high"');
  assert.equal(new Set(obs.map((o) => o.seq)).size, 4);
  assert.ok(s.ctl.options.longAsBigInt);
  assert.equal(s.ctl.options.timeFormat, s.api.WinccoaTimeFormat.Temporal);
});

test('changes keep nanosecond source times; changes close together share one message', async (t) => {
  const s = await started(t);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  const before = obsPosts(s.companion).length;
  s.ctl.change(SPEED, 1200, { stimeNs: T1, attrs: GOOD });
  s.ctl.change(LOAD, 'low', { stimeNs: T1 + 1n, attrs: { bad: true, uncertain: false } });
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 6);
  const posts = obsPosts(s.companion).slice(before);
  assert.equal(posts.length, 1);
  const [a, b] = posts[0].body.observations;
  assert.deepEqual([a.kind, a.value_text, a.source_ns], ['change', '1200', T1.toString()]);
  assert.deepEqual([b.value_text, b.source_ns, b.quality.good], ['"low"', (T1 + 1n).toString(), false]);
});

test('an unacknowledged message is resent with the same seqs and bodies, with backoff', async (t) => {
  let fail = 0;
  const s = await started(t, { companion: { handler: (r) => {
    if (r.url === '/v1/observations' && r.body.observations.some((o) => o.kind === 'change') && fail < 2) {
      fail += 1;
      return fail === 1 ? { status: 503, body: { error: 'service_unavailable' } } : null;   // then a dropped connection
    }
    return undefined;
  } } });
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  s.ctl.change(SPEED, 1500, { stimeNs: T1, attrs: GOOD });
  await waitFor(() => fail === 2 && idle(s.adapter) && !s.adapter.retryTimer
    && obsPosts(s.companion).filter((r) => r.body.observations.some((o) => o.kind === 'change')).length === 3);
  const tries = obsPosts(s.companion).filter((r) => r.body.observations.some((o) => o.kind === 'change'))
    .map((r) => r.body.observations.find((o) => o.kind === 'change'));
  assert.equal(tries.length, 3);
  assert.deepEqual(tries[1], tries[0]);
  assert.deepEqual(tries[2], tries[0]);
  assert.equal(s.adapter.companion, 'ok');
  assert.equal(s.adapter.state(), 'ok');
});

test('a 409 halts the observation flow in an error state instead of looping', async (t) => {
  const s = await started(t, { companion: { handler: (r) => (r.url === '/v1/observations'
    ? { status: 409, body: { error: 'conflict', seqs: [0], detail: 'conflicting retry for seq 0' } } : undefined) } });
  await waitFor(() => s.adapter.halted);
  const n = obsPosts(s.companion).length;
  s.ctl.change(SPEED, 1200, { stimeNs: T1, attrs: GOOD });
  await s.adapter.tick();
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(obsPosts(s.companion).length, n);
  assert.equal(s.adapter.state(), 'error');
  await s.adapter.writeStatus();
  assert.equal(s.ctl.value('Morlet_Adapter.state'), 'error');
  assert.equal(s.ctl.value('Morlet_Adapter.companion'), 'conflict');
  assert.match(s.ctl.value('Morlet_Adapter.detail'), /conflicting retry/);
});

test('a version mismatch is reported, not retried', async (t) => {
  const s = await started(t, { companion: { handler: (r) => (r.url === '/v1/observations'
    ? { status: 400, body: { error: 'unsupported_schema', supported: ['morlet-winccoa/2'] } } : undefined) } });
  await waitFor(() => s.adapter.halted);
  assert.equal(s.adapter.companion, 'version_mismatch');
  assert.match(s.adapter.halted.detail, /morlet-winccoa\/2/);
});

test('health from another protocol version is a mismatch and sends nothing', async (t) => {
  const s = await started(t, { companion: { handler: (r) => (r.url === '/v1/health'
    ? { status: 200, body: { schema: 'morlet-winccoa/2' } } : undefined) } });
  assert.equal(s.adapter.companion, 'version_mismatch');
  assert.equal(obsPosts(s.companion).length, 0);
  assert.equal(s.adapter.state(), 'degraded');
});

test('401: unauthorized and retried; a rotated token file is picked up', async (t) => {
  const s = await setup();
  t.after(s.cleanup);
  const right = s.tf.token;
  fs.writeFileSync(s.tf.file, `${'0'.repeat(64)}\n`, { mode: 0o600 });
  await s.adapter.start();
  assert.equal(s.adapter.companion, 'unauthorized');
  await new Promise((r) => setTimeout(r, 20));
  fs.writeFileSync(s.tf.file, `${right}\n`, { mode: 0o600 });
  const later = new Date(Date.now() + 5000);
  fs.utimesSync(s.tf.file, later, later);
  await s.adapter.tick();
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  assert.equal(s.adapter.companion, 'ok');
});

test('a token file readable by others is refused, and the token is never logged', async (t) => {
  const s = await setup();
  t.after(s.cleanup);
  fs.chmodSync(s.tf.file, 0o644);
  await s.adapter.start();
  assert.equal(s.adapter.companion, 'token_unreadable');
  assert.match(s.adapter.degraded.get('token'), /mode 0600/);
  assert.equal(s.companion.requests.length, 0);
  fs.chmodSync(s.tf.file, 0o600);
  await s.adapter.tick();
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  s.adapter.log.info(`a careless line with ${s.tf.token}`);
  const log = s.ctl.logs.map((l) => l[1]).join('\n');
  assert.ok(!log.includes(s.tf.token));
  assert.ok(log.includes(s.adapter.tokenFile.fingerprint()));
});

test('without a current acquisition values are only cached; when one appears: gap, heartbeat, snapshots', async (t) => {
  const s = await started(t, { companion: { acquisition: null } });
  s.ctl.change(SPEED, 1200, { stimeNs: T1, attrs: GOOD });
  await new Promise((r) => setTimeout(r, 50));
  await s.adapter.tick();
  assert.equal(obsPosts(s.companion).length, 0);
  s.companion.acquisition = 'acq-2';
  await s.adapter.tick();
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  const obs = sent(s.companion);
  assert.deepEqual(obs.map((o) => o.kind), ['gap', 'heartbeat', 'snapshot', 'snapshot']);
  assert.ok(obs.every((o) => o.acquisition === 'acq-2'));
  assert.equal(obs.find((o) => o.dpe === SPEED).value_text, '1200');
  assert.equal(obs.find((o) => o.dpe === SPEED).source_ns, T1.toString());
});

test('a new acquisition gets snapshots of the current values (no gap); later changes go to it', async (t) => {
  const s = await started(t);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  s.companion.acquisition = 'acq-2';
  await s.adapter.tick();
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 7);
  const obs = sent(s.companion).slice(4);
  assert.deepEqual(obs.map((o) => [o.kind, o.acquisition]), [['heartbeat', 'acq-2'], ['snapshot', 'acq-2'], ['snapshot', 'acq-2']]);
});

test('a Refresh update (REDU switch, DIST) is a gap for that datapoint, then a snapshot', async (t) => {
  const s = await started(t);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  s.ctl.refresh(SPEED);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 6);
  const [g, snap] = sent(s.companion).slice(4);
  assert.deepEqual([g.kind, g.dpe, g.asset], ['gap', SPEED, 'rig-1']);
  assert.deepEqual([snap.kind, snap.dpe], ['snapshot', SPEED]);
});

test('attributes refused by dpConnect: value only, sent as untimed and unreadable, status degraded', async (t) => {
  const s = await started(t, { fake: { acceptAttributes: false } });
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  const speed = sent(s.companion).find((o) => o.dpe === SPEED);
  assert.equal(speed.source_ns, undefined);
  assert.equal(speed.source_status, 'unreadable');
  assert.equal(speed.quality_status, 'unreadable');
  assert.equal(speed.quality, undefined);
  assert.equal(s.adapter.state(), 'degraded');
  assert.match([...s.adapter.degraded.values()].join(), /value only/);
});

test('query mode: dpQueryConnectSingle rows (header skipped) give the same fields', async (t) => {
  const s = await started(t, { cfg: { subscription: { mode: 'query' } } });
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  assert.ok(s.ctl.calls.includes('dpQueryConnectSingle'));
  const speed = sent(s.companion).find((o) => o.dpe === SPEED);
  assert.deepEqual([speed.kind, speed.value_text, speed.source_ns, speed.quality.good], ['snapshot', '1800', '1700000000000000000', true]);
  s.ctl.change(SPEED, 900, { stimeNs: T1, attrs: GOOD });
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 5);
  assert.deepEqual(sent(s.companion).slice(-1).map((o) => [o.kind, o.value_text, o.source_ns]), [['change', '900', T1.toString()]]);
});

test('query mode: an invalid query (error in the first callback) falls back to the value only', async (t) => {
  const s = await started(t, { cfg: { subscription: { mode: 'query' } }, fake: { acceptAttributes: false } });
  await waitFor(() => idle(s.adapter) && sent(s.companion).filter((o) => o.kind === 'snapshot').length >= 2);
  const speed = sent(s.companion).find((o) => o.dpe === SPEED && o.kind === 'snapshot');
  assert.equal(speed.source_status, 'unreadable');
});

test('a subscription error, an unrepresentable value, a missing quality attribute and an unreadable time', async (t) => {
  const s = await started(t, { cfg: { datapoints: [{ dpe: SPEED, asset: 'rig-1', value_type: 'bool' },
    { dpe: LOAD, asset: 'rig-1' }] } });
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 3);
  // SPEED holds 1800 but is configured as bool: not representable, so a gap for it instead of a value.
  assert.deepEqual(sent(s.companion).filter((o) => o.dpe === SPEED).map((o) => o.kind), ['gap']);
  s.ctl.connectionError(LOAD, new s.api.WinccoaError(-1, 'lost'));
  await waitFor(() => idle(s.adapter) && sent(s.companion).filter((o) => o.dpe === LOAD && o.kind === 'gap').length === 1);
  s.ctl.change(LOAD, 'x', { stimeNs: T1, attrs: { bad: false, uncertain: undefined } });   // `uncertain` unread
  s.ctl.elements.get('Rig1.load').stimeNs = undefined;
  s.ctl.change(LOAD, 'y', { stimeNs: undefined, attrs: GOOD });
  await waitFor(() => idle(s.adapter) && sent(s.companion).filter((o) => o.dpe === LOAD && o.kind === 'change').length === 2);
  const [x, y] = sent(s.companion).filter((o) => o.dpe === LOAD && o.kind === 'change');
  assert.equal(x.quality_status, 'unreadable');
  assert.equal(x.quality, undefined);
  assert.equal(y.source_status, 'unreadable');
  assert.equal(y.source_ns, undefined);
});

test('a full queue during an outage: one gap, drops counted, snapshots after draining', async (t) => {
  let down = true;
  const s = await started(t, { cfg: { limits: { max_queued: 6 } }, companion: { handler: (r) => (
    r.url === '/v1/observations' && down ? { status: 503, body: {} } : undefined) } });
  for (let i = 0; i < 10; i += 1) s.ctl.change(SPEED, 1000 + i, { stimeNs: T1 + BigInt(i), attrs: GOOD });
  await waitFor(() => s.adapter.outbox.dropped > 0 && s.adapter.assets.get('rig-1').overflow);
  const kinds = s.adapter.outbox.pending.map((p) => p.obs.kind);
  assert.equal(kinds.filter((k) => k === 'gap').length, 2);                     // the start gap and the overflow gap
  down = false;
  await waitFor(() => idle(s.adapter) && !s.adapter.retryTimer && !s.adapter.assets.get('rig-1').overflow
    && sent(s.companion).slice(-2).every((o) => o.kind === 'snapshot'), { timeoutMs: 5000 });
  const ok = obsPosts(s.companion);
  const delivered = ok.flatMap((r) => r.body.observations);
  const last = delivered.slice(-2);
  assert.deepEqual(last.map((o) => [o.kind, o.dpe]), [['snapshot', SPEED], ['snapshot', LOAD]]);
  assert.equal(last[0].value_text, '1009');                                    // the current value, from the cache
  // No heartbeat may precede the overflow gap in its message (it would count as contact during the loss).
  for (const r of ok) {
    const ks = r.body.observations.map((o) => o.kind);
    const lastGap = ks.lastIndexOf('gap');
    assert.ok(ks.indexOf('heartbeat') === -1 || ks.indexOf('heartbeat') > lastGap, `${r.body.observations.length}: ${ks.join()}`);
  }
  assert.ok(s.adapter.outbox.dropped >= 4, String(s.adapter.outbox.dropped));
});

test('message limits from the companion are respected', async (t) => {
  const s = await started(t, { companion: { } });
  s.companion.limits = { observations_per_message: 2, body_bytes: 1048576 };
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  await s.adapter.tick();
  for (let i = 0; i < 5; i += 1) s.ctl.change(SPEED, i, { stimeNs: T1 + BigInt(i), attrs: GOOD });
  await waitFor(() => idle(s.adapter) && sent(s.companion).filter((o) => o.kind === 'change').length === 5);
  const last = obsPosts(s.companion).slice(-3);
  assert.ok(last.every((r) => r.body.observations.length <= 2));
});

test('Temporal refused: Date (milliseconds) is used and reported', async (t) => {
  const s = await started(t, { fake: { temporal: false } });
  assert.equal(s.ctl.options.timeFormat, s.api.WinccoaTimeFormat.Date);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  s.ctl.change(SPEED, 1, { stimeNs: T1, attrs: GOOD });
  await waitFor(() => idle(s.adapter) && sent(s.companion).some((o) => o.kind === 'change'));
  assert.equal(sent(s.companion).find((o) => o.kind === 'change').source_ns, ((T1 / 1_000_000n) * 1_000_000n).toString());
  assert.match(s.adapter.degraded.get('time_format'), /milliseconds/);
});

test('every write goes to the namespace; writes elsewhere are refused', async (t) => {
  const s = await started(t);
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  await s.adapter.writeStatus();
  const names = s.ctl.writes.flatMap((w) => w.names);
  assert.ok(names.length > 0);
  assert.ok(names.every((n) => n.startsWith('Morlet_Adapter.') || n.startsWith('Morlet_Inv_panel1.')), names.join());
  assert.equal(s.ctl.value('Morlet_Adapter.state'), 'ok');
  assert.equal(s.ctl.value('Morlet_Adapter.companion'), 'ok');
  assert.equal(s.ctl.value('Morlet_Adapter.session'), s.adapter.session);
  const ns = new Namespace('Morlet', ['panel1']);
  for (const bad of [SPEED, 'Rig1.speed', 'Morlet_Adapter', 'Morlet_Adapter.nope', 'Morlet_Inv_other.state',
    'Morlet_Adapter.state:_original.._value', 'MorletX_Adapter.state']) {
    assert.throws(() => ns.check(bad), NamespaceViolation, bad);
  }
  ns.check('System1:Morlet_Adapter.state');
  await assert.rejects(ns.write(s.winccoa, 'Rig1', { speed: 1 }), NamespaceViolation);
  assert.equal(s.ctl.value(SPEED), 1800);
});

test('clean shutdown: backlog and a last heartbeat, then a gap; connections closed; state stopped', async (t) => {
  const s = await setup();
  t.after(s.cleanup);
  await s.adapter.start();
  await waitFor(() => idle(s.adapter) && sent(s.companion).length >= 4);
  s.ctl.change(SPEED, 1300, { stimeNs: T1, attrs: GOOD });
  await s.ctl.triggerExit();                           // no exit callback registered by the bare adapter
  await s.adapter.stop();
  const posts = obsPosts(s.companion);
  assert.deepEqual(posts.at(-1).body.observations.map((o) => o.kind), ['gap']);
  assert.ok(posts.at(-2).body.observations.some((o) => o.kind === 'heartbeat'));
  assert.ok(sent(s.companion).some((o) => o.kind === 'change' && o.value_text === '1300'));
  assert.equal(s.adapter.outbox.pending.length, 0);
  assert.equal(s.ctl.connections.size, 0);
  assert.equal(s.ctl.value('Morlet_Adapter.state'), 'stopped');
  assert.equal(s.adapter.timer, null);
});

// The companion's health does database work, so its RTT includes database latency; heartbeats carry the RTT of
// GET /v1/clock instead, which does none. A slow health stands in for a loaded database.
const SLOW_MS = 150;
const slowHealth = (clock) => async (r) => {
  if (r.url === '/v1/health') await new Promise((res) => setTimeout(res, SLOW_MS));
  if (r.url === '/v1/clock' && clock) return clock;
  return undefined;
};
const heartbeatsSent = (c) => sent(c).filter((o) => o.kind === 'heartbeat');

test('heartbeat RTT comes from GET /v1/clock, not from the slower health request', async (t) => {
  const s = await started(t, { companion: { handler: slowHealth() } });
  await waitFor(() => idle(s.adapter) && heartbeatsSent(s.companion).length >= 1);
  const urls = s.companion.requests.map((r) => r.url);
  assert.ok(urls.indexOf('/v1/clock') > urls.indexOf('/v1/health'));
  assert.ok(urls.indexOf('/v1/clock') < urls.indexOf('/v1/observations'));
  const rtt = BigInt(heartbeatsSent(s.companion)[0].rtt_ns);
  assert.ok(rtt < BigInt(SLOW_MS) * 1_000_000n, `rtt ${rtt} ns includes the health request's delay`);
  assert.equal(s.adapter.clockEndpoint, true);
});

test('an older companion without /v1/clock (404): the health RTT is used, logged once', async (t) => {
  const s = await started(t, { companion: { handler: slowHealth({ status: 404, body: { error: 'not_found' } }) } });
  await waitFor(() => idle(s.adapter) && heartbeatsSent(s.companion).length >= 1);
  await s.adapter.tick();
  await waitFor(() => idle(s.adapter) && heartbeatsSent(s.companion).length >= 2);
  for (const hb of heartbeatsSent(s.companion)) assert.ok(BigInt(hb.rtt_ns) >= BigInt(SLOW_MS) * 1_000_000n);
  assert.equal(s.companion.requests.filter((r) => r.url === '/v1/clock').length, 1);
  assert.equal(s.ctl.logs.filter((l) => /no GET \/v1\/clock/.test(l[1])).length, 1);
  assert.equal(s.adapter.clockEndpoint, false);
  assert.equal(s.adapter.state(), 'ok');
});
