# Security Policy

## Reporting a vulnerability

Email **info@duke.se** with "SimulationsMCP security" in the subject. Please do not
open a public GitHub issue for a suspected vulnerability.

Include what you need to make the problem reproducible: the version (see
`CHANGELOG.md` or `package.json`), the transport in use (stdio or HTTP), the tool call
or sequence that triggers it, and what you observed. A proof of concept helps; a
working exploit is not required.

You will get an acknowledgement within five working days. We will tell you whether the
report is accepted, and if it is, keep you informed until a fix ships. Please give us a
reasonable window to release before disclosing publicly.

## Supported versions

Only the latest release receives fixes. Versions match the installer and
`package.json`; see `CHANGELOG.md`.

## Scope

This server runs entirely on one Windows machine and drives a local ExtendSim
installation over COM. `docs/DESIGN_DOCUMENT.md` §5 and §10 document the full attack
surface, trust boundaries and threat model — read those first, as they explain what is
a deliberate design decision and what would be a genuine flaw.

In scope:

- Command or ModL injection through tool parameters that escape input sanitization
- Path traversal in model or database file handling
- Anything reachable over the network in HTTP transport mode
- Leakage of model content, file paths or user data into telemetry, which is
  designed to record none of it

Out of scope, by design:

- **The server executes ExtendSim commands on behalf of its AI client.** That is the
  product. An AI client that sends a destructive command is a trust decision made when
  the client was connected, not a vulnerability here.
- **stdio transport has no authentication.** It communicates over process-local pipes
  with a parent process that already has the user's privileges.
- **HTTP transport binds localhost and is unauthenticated.** It exists for ChatGPT and
  is documented as requiring a reverse proxy for any exposure beyond the machine.
  Reports that it is "exposed" when deliberately published without one are not
  vulnerabilities.
- Vulnerabilities in ExtendSim itself — report those to
  [Imagine That, Inc.](https://extendsim.com)

## Not affiliated with the vendor

This is an independent third-party integration by Duke Systems AB. ExtendSim is a
registered trademark of Imagine That, Inc., a subsidiary of ANDRITZ Inc. Security
reports about this server should come to us, not to them.
