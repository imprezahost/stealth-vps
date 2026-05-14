# Tests

Automated tests for the roles and templates.

## Layout

```
tests/
├── molecule/
│   └── default/        # Ansible Molecule scenario — converges site.yml
│                       #   on a Debian 12 + systemd container and runs
│                       #   verify.yml against the artifacts
└── probe-resistance/   # probe-resistance suite (scaffolded in v0.4.0)
    ├── README.md       # threat model + how to run
    ├── scenarios/      # 5 numbered scenario docs (what + why per scenario)
    └── scripts/        # runnable implementations (2 done, 2 scaffolded, 1 manual)
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
| v0.3.0 | Multi-platform matrix: Debian 12 + Ubuntu 22.04 + Ubuntu 24.04 in one scenario (converge + verify + idempotence runs against all three) |
| v0.4.0 | Probe-resistance scaffolding — 5 scenario docs, 2 runnable scripts (HTTPS direct probe, port-scan baseline), 2 scaffolded (TLS fingerprint, active probe), 1 manual (replay resistance). GitLab CI `probe-resistance` job behind manual trigger. |
| v0.4.1 | Probe-resistance scripts 02 + 03 filled in: TLS shape comparison (7 features) using stdlib `ssl` + `openssl x509`; HTTP response-shape comparison (status + header-set + body-bucket) via stdlib `http.client`. |
| **v0.5.1** | **Byte-level JA3 + JA3S** added to scenario 02 via stdlib `ssl.MemoryBIO` + a pure-stdlib TLS record / handshake parser. 9 features compared (7 stdlib + JA3 + JA3S). GREASE values filtered per RFC 8701. JA3 documented as a controller-side sanity check (always matches between dest + VPS probes since the client is the same); JA3S documented with the TLS 1.3 limitation that most extensions migrated to `EncryptedExtensions`. |
| v0.5.2 | JA4 + JA4S in `tls_fingerprint_compare.py` (FoxIO 2023+ spec) — needs cross-validation against `ja4-python` reference impl before claiming compliance. |
| v1.0.0 | Probe-resistance: golden snapshots per dest; HTTP/2 SETTINGS-frame comparison; automated replay-resistance scenario; quarterly refresh tooling. |
