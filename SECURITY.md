# Security Policy

## Supported versions

StatusCast is currently an early-stage desktop application. Security fixes are applied to the latest `main` branch and the latest release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository rather than opening a public issue.

Include:

- affected version or commit,
- Windows version,
- steps to reproduce,
- expected and observed behavior.

The HTTP API binds to `localhost` only; never expose port `5099` to an untrusted network without adding authentication and a reverse proxy.
