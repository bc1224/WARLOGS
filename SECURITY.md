# Security policy

## Supported versions

Only the latest version on `main` is supported. The app updates itself from there
(**More**, **Install updates automatically**), so fixes reach everyone on the next update.

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

- use **Report a vulnerability** on the repository's
  [Security tab](https://github.com/bc1224/WARLOGS/security/advisories/new).

Include what you found, how to reproduce it, and what it could let someone do. You can
expect a reply within a week.

Areas that matter most:

- the local dashboard (it listens on `127.0.0.1` only and checks the Host and Origin of
  every request);
- the updater, which downloads code from this repository;
- the report relay (`relay/`), which turns **Report an issue** into a GitHub issue.
