# MorletDB Enterprise (empty)

**DRAFT layout, no enterprise code yet.** This directory is where enterprise code will live once
[docs/licensing.md](../docs/licensing.md) takes effect. Until then this README is under the Apache License 2.0
like every other file.

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
```

What belongs here and what stays in the core is decided by the rule in
[docs/licensing.md](../docs/licensing.md#the-rule-for-the-boundary): what one machine needs to keep, read and trust its
own data stays open.
