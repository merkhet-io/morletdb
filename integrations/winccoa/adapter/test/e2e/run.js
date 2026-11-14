// Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
// SPDX-License-Identifier: Apache-2.0
'use strict';
// The end-to-end scenario on the adapter side, driven by test_e2e.py: the REAL adapter code (src/main.js entry
// points, as index.js and bootstrap.js call them) on the fake WinCC OA API, against the real companion.
//
//   node run.js <scenario.json> <result.json>
//
// 1. Bootstrap the namespace (twice: the second run must change nothing).
// 2. Start the adapter; the initial values arrive as update type Answer and must be sent as snapshots.
// 3. Two value changes, with source times taken from the clock (nanoseconds, not on a millisecond boundary).
// 4. An investigation started by writing the slot's fields and changing its trigger, as a panel would.
// 5. A clean shutdown through the exit callback (as PMON's exit signal would run it).

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createFake } = require('../fake-winccoa');
const { run, bootstrapMain } = require('../../src/main');
const { nowNs } = require('../../src/values');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(cond, what, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (!(await cond())) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await sleep(20);
  }
}

async function main() {
  const sc = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const { api, ctl } = createFake();
  const E = api.WinccoaElementType;
  const GOOD = { bad: false, uncertain: false };
  for (const d of sc.datapoints) ctl.define(d.dpe, E[d.element_type], { value: d.initial, stimeNs: BigInt(d.initial_stime_ns), attrs: GOOD });

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'morlet-adapter-e2e-'));
  const cfgFile = path.join(dir, 'adapter.json');
  fs.writeFileSync(cfgFile, JSON.stringify({
    companion: { url: sc.companion_url, token_file: sc.token_file },
    namespace: { prefix: 'Morlet', slots: ['panel1'] },
    heartbeat_interval_s: 0.5,
    flush_linger_ms: 20,
    assets: [{ name: sc.asset }],
    datapoints: sc.datapoints.map((d) => ({ dpe: d.dpe, asset: sc.asset })),
  }));
  const winccoa = new api.WinccoaManager();
  const boot1 = await bootstrapMain(api, ['--config', cfgFile], { winccoa });
  const created = ctl.created.slice();
  const boot2 = await bootstrapMain(api, ['--config', cfgFile], { winccoa });

  const adapter = await run(api, ['--config', cfgFile], { winccoa });
  const idle = () => adapter.outbox.pending.length === 0 && adapter.lastAckSeq >= 0 && !adapter.retryTimer;
  await waitFor(() => idle() && adapter.assets.get(sc.asset).acquisition === sc.acquisition, 'snapshots acknowledged');
  await sleep(150);

  const changes = [];
  for (const c of sc.changes) {
    const stime = (nowNs() / 1000n) * 1000n + 123_457n;      // nanoseconds, deliberately not whole milliseconds
    ctl.change(c.dpe, c.value, { stimeNs: stime, attrs: GOOD });
    changes.push({ dpe: c.dpe, stime_ns: stime.toString() });
    await waitFor(idle, 'change acknowledged');
    await sleep(c.hold_ms);
  }

  const slot = 'Morlet_Inv_panel1';
  const inv = sc.investigation;
  await winccoa.dpSetWait([`${slot}.collection`, `${slot}.recordingId`, `${slot}.startSample`, `${slot}.endSample`,
    `${slot}.topK`], [inv.collection, inv.recording_id, BigInt(inv.start_sample), BigInt(inv.end_sample), 5]);
  await winccoa.dpSetWait(`${slot}.trigger`, 1);
  await waitFor(() => ['done', 'failed'].includes(ctl.value(`${slot}.state`)), 'investigation result', 60000);

  await ctl.triggerExit();
  const namespaceWrites = [...new Set(ctl.writes.flatMap((w) => w.names))].sort();
  const out = {
    session: adapter.session,
    bootstrap: { first: boot1, second: boot2, created },
    changes,
    investigation: Object.fromEntries(['requestId', 'state', 'status', 'detail', 'matchCount', 'result']
      .map((k) => [k, ctl.value(`${slot}.${k}`)])),
    status: Object.fromEntries(['state', 'companion', 'lastAckSeq', 'gaps', 'tokenFingerprint', 'detail']
      .map((k) => [k, ctl.value(`Morlet_Adapter.${k}`)])),
    namespaceWrites,
    logs: ctl.logs.map(([lvl, t]) => `${lvl}: ${t}`),
  };
  fs.writeFileSync(process.argv[3], JSON.stringify(out, (_, v) => (typeof v === 'bigint' ? v.toString() : v), 2));
  fs.rmSync(dir, { recursive: true, force: true });
}

main().then(() => process.exit(0), (e) => {
  console.error(e.stack || e.message);
  process.exit(1);
});
