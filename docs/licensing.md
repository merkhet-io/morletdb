# Licensing: open core and enterprise

**Status: DRAFT (2026-11-14), not in force.** It takes effect only when the owner approves it and the licence texts
have been reviewed by a lawyer. Until then every file in this repository is under the Apache License 2.0, as
[LICENSE](../LICENSE) says.

MorletDB has two layers:
- **MorletDB, the open-source core:** Apache License 2.0.
- **MorletDB Enterprise:** extra features for running many machines in production, under the
  [MorletDB Enterprise License](../LICENSE-ENTERPRISE.md).

The enterprise source is public too, so you can read it, review it and run its tests. Using it in production needs
a subscription.

## The rule for the boundary

**Everything one machine needs to keep, read and trust its own data is open, and stays open.** Enterprise covers what an
organisation needs to run many machines centrally, with guarantees.

So the core keeps, without exception:
- **Storage and the lossless promise:** the extension, chunk formats, Grizzbit (codec, specification and the
  independent reference reader), write-time verification, the integrity scrub, backup and restore of a single
  database.
- **Getting data in and out:** importers, collectors, the SDK (including the native decoder and the embedded
  edition), compressed transfer, export, and every documented SQL function. No data is ever locked in.
- **Finding things:** envelopes, search, context, episodic memory and roll-ups.
- **Running one machine:** the steward, with its single-node checks, guarded actions, breakers and metrics endpoint;
  the MCP server; the viewer.
- **Evidence:** every study, protocol, harness and result, and the technical report.

Nothing that has been released under Apache 2.0 is ever moved to the enterprise layer. A fix to the core always lands
in the core.

## What enterprise is for

None of this exists yet. These are candidates, in the order they are likely to be needed:

| Area | Enterprise candidate | What the core keeps |
|---|---|---|
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
// not under the Apache License 2.0. Production use requires a subscription.
```

(`#` instead of `//` in Python, shell and TOML files.)

## Contributions

- Contributions to the core are licensed under Apache 2.0, as before.
- From the date this document takes effect, contributors also sign the
  [Contributor License Agreement](cla.md). It lets the project use a contribution in both layers. Without it, a
  contribution can only ever be used in the core.
- Contributions to `enterprise/` need the agreement in any case.

## Names, data and the paper

- **Names:** the names MorletDB and Grizzbit and the project's logos are not licensed by either licence beyond fair
  description. A fork may say it is based on MorletDB, but may not call itself MorletDB.
- **Data:** some datasets used in studies have licences of their own. Paderborn's bearing data, for example, is
  CC BY-NC 4.0, so it may not be used in enterprise demos or sales material without the provider's permission.
  [demo-catalog.md](demo-catalog.md) lists each dataset's terms.
- **The technical report** stays under CC BY 4.0.

## Open questions for the owner

1. The licensor's legal name in the headers and the enterprise licence (Merkhet is a placeholder).
2. Whether enterprise source stays public in this repository (source-available, as above) or moves to a private
   one.
3. Whether a free tier allows production use on a small number of machines (for example up to 3), so a single plant
   can try fleet features.
4. The date the CLA starts. Contributions merged before it stay usable in the core only.
