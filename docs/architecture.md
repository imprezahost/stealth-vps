# Architecture

> Placeholder — to be expanded as the v0.1.0 implementation lands.

## Overview

`stealth-vps` is structured as two cooperating Ansible roles plus a thin installer / cloud-init layer:

```text
┌──────────────────────────────────────────────────────────────────┐
│                       Entry point (one of)                       │
│   scripts/install.sh   cloud-init/stealth-vps.yaml   ansible-pull│
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ stealth-hardening    │   SSH, fail2ban, UFW,
              │ (Ansible role)       │   unattended-upgrades,
              │                      │   sysctl/BBR, Spamhaus
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ stealth-vps          │   Xray-core (Reality),
              │ (Ansible role)       │   Hysteria2, 3X-UI panel,
              │                      │   observability exporters
              └──────────────────────┘
```

Each role is independently usable. You can apply `stealth-hardening` to any VPS regardless of whether you want the proxy stack, and you can apply `stealth-vps` to a VPS already hardened by other tooling.

## Why separate roles?

So you can reuse `stealth-hardening` on infrastructure that isn't a proxy VPS — your monitoring host, your build server, anything where the SSH/fail2ban/UFW baseline applies. The split mirrors how the team uses it internally.

## Component choices

- **Xray-core for Reality** — current best-in-class active-probing-resistant transport. See [XTLS/Xray-core](https://github.com/XTLS/Xray-core).
- **Hysteria2 in parallel** — different transport profile (QUIC, throughput-first) that fails in different network conditions than Reality. Running both gives clients automatic fallback.
- **3X-UI for the panel** — community standard, multi-user, traffic accounting. We add a working fail2ban filter for it that upstream issues have not resolved.
- **fail2ban over CrowdSec / WAF** — boring, predictable, no telemetry.

## What this is *not*

- Not a Tor replacement. Reality + Hysteria2 resist active probing and traffic-classification, but the server still knows who you are. Use Tor for anonymity.
- Not a multi-tenant reseller platform. For that, look at [Marzban](https://github.com/Gozargah/Marzban) or [PasarGuard/panel](https://github.com/PasarGuard/panel).
- Not a one-click "everything" panel. It's a deliberate, auditable subset.
