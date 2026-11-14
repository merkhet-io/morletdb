# Licensing: open core and enterprise

**Status:** decided by the owner on 2026-11-14; in force from the day this file is merged into `main`. The
[Contributor License Agreement](cla.md) applies to contributions from that day. No file is under the enterprise
licence yet: every file in this repository is under the Apache License 2.0, as [LICENSE](../LICENSE) says, until
the first enterprise file is added under `enterprise/`. The enterprise licence text is version 0.1 and is reviewed
by a lawyer before that happens.

MorletDB has two layers:
- **MorletDB, the open-source core:** Apache License 2.0.
- **MorletDB Enterprise:** extra features for running many machines in production, under the
  [MorletDB Enterprise License](../LICENSE-ENTERPRISE.md).

The enterprise source is public too, in this repository, so you can read it, review it and run its tests. Using it
in production is free on up to 3 machines; beyond that it needs a subscription.

## The rule for the boundary

**Everything one machine needs to keep, read and trust its own data is open, and stays open.** Enterprise covers what an
organisation needs to run many machines centrally, with guarantees.

So the core keeps, without exception:
- **Storage and the lossless promise:** the extension, chunk formats, Grizzbit (codec, specification and the
  independent reference reader), write-time verification, the integrity scrub, backup and restore of a single
  database.
- **Getting data in and out:** importers, collectors for open protocols (OPC UA, MQTT and the like), the SDK
  (including the native decoder and the embedded edition), compressed transfer, export, and every documented SQL
  function, including the context functions. No data is ever locked in.
- **Finding things:** envelopes, search, context, episodic memory and roll-ups.
- **Running one machine:** the steward, with its single-node checks, guarded actions, breakers and metrics endpoint;
  the MCP server; the viewer.
- **Evidence:** every study, protocol, harness and result, and the technical report.

Nothing that has been released under Apache 2.0 is ever moved to the enterprise layer. A fix to the core always lands
in the core.

## What enterprise is for

None of this exists yet. The first planned area is the integration with commercial industrial platforms, starting
with WinCC OA (#474); the others are candidates, in the order they are likely to be needed:

| Area | Enterprise candidate | What the core keeps |
|---|---|---|
| Industrial platforms | Integrations with commercial SCADA and HMI platforms, starting with WinCC OA (#474): operating context from the platform, investigations started from its panels | Open-protocol collectors, the importers, the SDK and the documented context and search functions, so the same context can be stored and searched without the platform |
| Fleet | One console for many edge machines: inventory, versions, health, rollout of upgrades in waves | The steward on each machine and its metrics endpoint |
| Automation | Policy-driven remediation across a fleet, with approval workflows | Single-node guarded actions and breakers |
| Backup | Managed off-site backups for a fleet, with scheduled restore drills and reports | Single-database backup, restore and the restore drill |
| Compliance | Evidence packs for change control and IEC 62443 audits: who changed what, when, with which approval | PostgreSQL roles, row-level security, `pgaudit` |
| Identity | Single sign-on for the viewer and the fleet console | PostgreSQL authentication |
| Support | Support with agreed response times, long-term-support releases | Community support in GitHub issues |

A feature goes into enterprise only if the core stays complete without it for a single machine. When in doubt, it
goes into the core.

## How the code is kept apart

- **One directory:** enterprise code lives only under [`enterprise/`](../enterprise/README.md). Every source file
  there starts with the enterprise header (below); no file outside it does.
- **Off by default:** the core never depends on enterprise code. A Rust crate may use an enterprise crate only
  through an optional Cargo feature named `enterprise`, which no default build turns on; Python packages outside
  `enterprise/` never import from it. Release images, wheels and packages built from the core contain no enterprise
  code.
- **Checked in CI:** `tools/license_check.py` enforces these rules on every pull request.

The header of an enterprise file:

```text
// Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
// not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
```

(`#` instead of `//` in Python, shell and TOML files.)

## Contributions

- Contributions to the core are licensed under Apache 2.0, as before.
- From the day this document takes effect, contributors also agree to the
  [Contributor License Agreement](cla.md) by signing off every commit (`git commit -s`). It lets the project use a
  contribution in both layers. A CI check refuses pull requests from outside contributors with commits that are not
  signed off.
- Contributions merged before that day can only ever be used in the core.

## Names, data and the paper

- **Names:** the names MorletDB and Grizzbit and the project's logos are not licensed by either licence beyond fair
  description. A fork may say it is based on MorletDB, but may not call itself MorletDB.
- **Data:** some datasets used in studies have licences of their own. Paderborn's bearing data, for example, is
  CC BY-NC 4.0, so it may not be used in enterprise demos or sales material without the provider's permission.
  [demo-catalog.md](demo-catalog.md) lists each dataset's terms.
- **The technical report** stays under CC BY 4.0.

## Decisions (owner, 2026-11-14)

1. **Licensor:** Merkhet.
2. **Enterprise source:** public, in this repository, under `enterprise/`.
3. **Free tier:** production use of enterprise features on up to 3 machines needs no subscription.
4. **CLA:** applies to contributions from the day this document is merged.
