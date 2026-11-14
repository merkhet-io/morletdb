# MorletDB Enterprise (empty)

**No enterprise code yet.** This directory is where enterprise code will live, under the
[MorletDB Enterprise License](../LICENSE-ENTERPRISE.md) and the rules of [docs/licensing.md](../docs/licensing.md).
This README itself is under the Apache License 2.0 like every file without the enterprise header.

Rules, enforced by `tools/license_check.py` in CI:
- every source file here starts with the MorletDB Enterprise License header;
- no file outside this directory carries that header;
- the core never depends on code here: a Rust crate outside `enterprise/` may use an enterprise crate only through an
  optional Cargo feature named `enterprise`, and Python code outside `enterprise/` never imports from it.

Layout once there is code:

```text
enterprise/
  crates/<name>/     Rust crates (fleet console back end, ...), outside the core's default workspace build
  python/<name>/     Python packages (fleet agent, compliance reports, ...)
  winccoa/           the WinCC OA integration (#474): adapter/ (WinCC OA JavaScript Manager) and companion/ (Python, on the SDK)
```

What belongs here and what stays in the core is decided by the rule in
[docs/licensing.md](../docs/licensing.md#the-rule-for-the-boundary): what one machine needs to keep, read and trust its
own data stays open.
