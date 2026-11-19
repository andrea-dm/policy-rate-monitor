# Security Policy

## Reporting a Vulnerability

Please report a suspected vulnerability **privately** to the maintainer listed in
[CREDITS.md](CREDITS.md) rather than in a public issue. Include the affected version, a
description of the issue and, where possible, the steps to reproduce it.

## Supported Versions

Only the latest release receives fixes.

## Scope

This policy covers the `policy-rate-monitor` package published from this repository.
Vulnerabilities in upstream dependencies should be reported to their maintainers. `pip-audit`
checks the runtime dependencies (CI job `audit`) and every development group (CI job `audit-dev`)
on each pull request (informational) and each push to `main` and `develop` (blocking); see
[CONTRIBUTING.md](CONTRIBUTING.md).

The tool downloads the BIS bulk-download page, finds the link to the configured ZIP on it, and
downloads that ZIP; both URLs in the shipped configuration use HTTPS. It checks that the download
is a ZIP holding exactly one CSV before it reads it. It runs no code from the download and needs
no credentials.
