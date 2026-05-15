# Security Policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in `stealth-vps`, please report it privately. **Do not open a public GitHub issue.**

### Contact

Pick whichever channel suits you — all three reach the maintainers directly:

- **Email**: [support@imprezahost.com](mailto:support@imprezahost.com)
- **Telegram**: [@imprezahost](https://t.me/imprezahost)
- **Support portal**: [open a ticket in the security department](https://portal.imprezahost.com/submitticket.php?step=2&deptid=7)

Please include:

- A description of the vulnerability
- Steps to reproduce, or a proof-of-concept
- The release version or commit affected
- Your assessment of impact (e.g. credential exposure, code execution, traffic exposure)

### What to expect

- We will acknowledge your report within **3 business days**.
- We aim to provide a status update or fix within **30 days** for high-impact issues.
- We follow a **90-day coordinated disclosure** policy by default. If you need a different timeline (e.g. shorter for actively-exploited vulnerabilities), tell us in your initial report.
- We credit reporters in the release notes unless you ask us not to.

## In scope

- The Ansible roles, cloud-init template, and install script in this repository
- Generated configuration that exposes credentials or weakens defaults
- Insecure defaults that meaningfully degrade the threat model the project claims to deliver (probe resistance, no plaintext leaks, working hardening)

## Out of scope

- Vulnerabilities in upstream dependencies (`Xray-core`, `Hysteria2`, `3X-UI`, etc.) — please report those to their respective projects. We will of course update affected dependencies once disclosed.
- Issues affecting the Impreza Host infrastructure itself (the VPSes, the support portal, the website). Those go through the same channels as customer support — email / Telegram / ticket — and the team will route them internally.
- Social-engineering, DoS, or rate-limit issues against `imprezahost.com` or its subdomains.
- "Best practice" suggestions without a demonstrable security impact (those are welcome as regular issues).

## A note on threat model

This project targets **resistance to active probing and traffic-classification by adversarial network operators**, not formal anonymity. It is not a Tor replacement. The hardening included is meant to reduce VPS compromise risk, not to defend against a determined targeted adversary with kernel-level access.

Reports framed around "this doesn't protect against [out-of-scope threat]" are valid for documentation improvement but not security vulnerabilities.
