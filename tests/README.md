# Tests

This directory holds automated tests for the roles and templates.

## Layout

```
tests/
├── molecule/          # Ansible Molecule scenarios (integration tests in containers)
│   └── default/       # default scenario — runs site.yml on Debian 12 + Ubuntu 22.04
└── probe-resistance/  # (v1.0 roadmap) automated probe-resistance suite
```

## Running locally

Once Molecule scenarios are wired up:

```bash
pip install "molecule[docker]" "ansible-core>=2.16"
cd tests/molecule/default
molecule test
```

## Status

- v0.1.0: Molecule default scenario boots a Debian 12 container, applies `site.yml`, asserts services are running
- v0.2.0: extended scenarios for Ubuntu 22.04 and 24.04
- v1.0.0: probe-resistance suite (uTLS fingerprint comparison, simulated active-probing patterns, port-scan resistance)
