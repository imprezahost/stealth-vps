# Tests

Automated tests for the roles and templates.

## Layout

```
tests/
├── molecule/
│   └── default/        # Ansible Molecule scenario — converges site.yml
│                       #   on a Debian 12 + systemd container and runs
│                       #   verify.yml against the artifacts
└── probe-resistance/   # (v1.0 roadmap) automated probe-resistance suite
```

## Running Molecule locally

```bash
pip install "molecule[docker]" "ansible-core>=2.14,<2.18"
ansible-galaxy collection install -r ansible/requirements.yml
cd tests/molecule/default
molecule test
```

`molecule test` runs the full sequence: dependency → cleanup → destroy → syntax → create → prepare → converge → idempotence → verify → cleanup → destroy. The `idempotence` step asserts that a second converge ends with `changed=0`.

## Scope

The scenario intentionally toggles off the service-bound tasks (panel, xray, hysteria, UFW, fail2ban, unattended-upgrades, spamhaus) — those rely on a real network stack, public IP, or privileged ipset/iptables ops that Docker doesn't reproduce reliably. What's left and verified:

- Kernel tuning task: `/etc/sysctl.d/99-stealth.conf` content + `/etc/modules-load.d/tcp_bbr.conf`
- SSH hardening drop-in: `/etc/ssh/sshd_config.d/99-stealth-vps.conf`, merged `sshd -T` includes `port 22550`

This catches lint regressions and runtime template / variable errors. Full validation of the service-bound tasks happens on a real VPS — see `docs/development.md` (Path A / Path B).

## Status by version

| Version | Scope |
|---|---|
| v0.2.0 | Default scenario — kernel + SSH artifacts on Debian 12 |
| **v0.3.0** | **Multi-platform matrix: Debian 12 + Ubuntu 22.04 + Ubuntu 24.04 in one scenario** (converge + verify + idempotence runs against all three) |
| v1.0.0 | Probe-resistance suite — uTLS fingerprint comparison, simulated active-probing patterns, port-scan resistance |
