# Security policy

MorletDB is an experimental PostgreSQL extension. Only the newest alpha is a
candidate for fixes; there is no security maintenance SLA or supported production
release. See [support boundaries](SUPPORT.md).

Report suspected vulnerabilities privately to **hello@merkhet.io** with subject
`MorletDB security`. Include the affected commit/release, PostgreSQL version,
minimal reproduction, expected/observed behavior and impact. Do not send secrets
or customer recordings. Do not post exploit details in a public issue.
If private reporting is enabled in GitHub, the repository Security tab is another option.

Maintainers will assess the report and coordinate a fix/disclosure with the
reporter. No response deadline or bounty is promised.

Install extensions as an administrator and use restricted application roles.
The Compose credentials are public demo credentials for an isolated local
database. Keep it bound to localhost. Direct mutation of managed tables can
break cross-table invariants; use the recording APIs. RLS and invoker permissions
have tests but are not an independent security audit. Set caller timeouts and
resource limits; an explicit search budget does not bound execution time.
