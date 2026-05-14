# Probe-resistance test suite

> **5 of 5 scenarios have at least one runnable script as of v0.5.2.** Scenario 02 includes real JA3 + JA3S byte-level fingerprints. Scenario 03 gained an HTTP/2 sub-scenario (`h2_settings_compare.py`) — pure stdlib, captures the server's first `SETTINGS` frame and compares dest vs VPS. Scenario 05 (replay-resistance) is the only one still manual; v1.0 picks up automated replay + golden snapshots + JA4/JA4S.

A focused, evolving set of tests that exercise the *probe-resistance* properties of a deployed stealth-vps host. These tests do not validate that the role installs — that's what Molecule covers. They validate that the *running server* presents to active probers as something benign (the `dest` site Reality borrows from) rather than as a proxy.

## Why a separate suite

Probe-resistance is the headline feature of Reality + Hysteria2 in 2026, but it's hard to verify by reading code:

- A passive observer can't distinguish "this works" from "this is broken in a way I won't notice for weeks".
- The threat model is the **GFW active-probing pipeline** (and similar): unsolicited TLS / HTTPS / UDP probes from middle-boxes that try to provoke a proxy-shaped response.
- Most cases of *broken* stealth come from operator error after install (wrong cert SNI, panel exposed on a public port, dest unreachable so Reality falls back to a generic banner). Tests that re-run after every release catch these.

## Threat model

We test against a probe-and-classify adversary with these capabilities:

| Capability | We test against it? |
|---|---|
| Direct HTTPS GET to the server IP (no SNI / arbitrary SNI) | yes (scenario 01) |
| TLS handshake fingerprinting (JA3 / JA4) compared against `dest` | yes — scenario 02, scaffolded |
| TLS handshake with no valid Reality key → server response shape | yes — scenario 03, scaffolded |
| TCP / UDP port scan with service-fingerprint detection | yes — scenario 04, scaffolded |
| Replay of a captured Reality handshake | scenario 05, manual for now |
| Statistical traffic-shape analysis (inter-packet timing, sizes) | **not in scope** — defer to upstream research |
| Stronger adversary (state-level CA compromise) | **not in scope** — out of project scope |

The first four catch ~90% of real-world breakage we've seen on production deployments. Scenario 05 catches a narrower class of bugs; we keep it manual because automated replay needs a captured handshake from a live client.

## What is *not* a goal of this suite

- **Performance benchmarking.** That's `iperf3` / `goben` territory and changes per route.
- **Functional testing.** "Can a real client connect?" is covered by deployment validation in `docs/operations.md`.
- **Continuous adversarial CI.** A test that fails because GFW changed its probe yesterday is noise, not signal. We pin our probes to specific shapes documented in published research, then update them on a quarterly cadence.

## Layout

```
tests/probe-resistance/
├── README.md                       # this file
├── scenarios/                      # numbered scenario docs (1 per file)
│   ├── 01-https-direct-probe.md
│   ├── 02-tls-fingerprint.md
│   ├── 03-active-probe-no-key.md
│   ├── 04-port-scan-baseline.md
│   └── 05-replay-resistance.md
├── scripts/                        # runnable implementations
│   ├── https_direct_probe.sh
│   ├── tls_fingerprint_compare.py
│   ├── active_probe.py
│   ├── h2_settings_compare.py      # scenario 03 HTTP/2 companion (v0.5.2)
│   └── port_scan_baseline.sh
└── requirements.txt                # Python deps for the scripted scenarios
```

## How to run

### Against a deployed host

```bash
# From a controller machine (anywhere with outbound TCP 443 + UDP to the host):
cd tests/probe-resistance
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# Set the target host (matches the dest expected behind Reality)
export PROBE_TARGET="my-vps.example.com"
export PROBE_REALITY_DEST="www.microsoft.com"        # whatever you configured
export PROBE_REALITY_PORT=443

# Run individual scenarios:
bash scripts/https_direct_probe.sh
python3 scripts/tls_fingerprint_compare.py
python3 scripts/active_probe.py
bash scripts/port_scan_baseline.sh
```

Each script exits 0 on pass, non-zero on fail, and prints a one-line summary plus a `WHY:` block on failure.

### From CI

A manual-trigger GitLab CI job (`probe-resistance`) runs scenarios 01 + 02 + 03 + 03-h2 + 04 against a staging deploy. It is **not** part of the default pipeline — probe-resistance tests need a live VPS, not a Docker runner, and we don't want a network blip flaking the release pipeline. Trigger it from the GitLab UI on demand.

## Scenario status (v0.5.2)

| # | Scenario | Doc | Script | Status |
|---|---|---|---|---|
| 01 | HTTPS direct probe → must return `dest`-shaped HTML | ✅ | ✅ | runnable |
| 02 | TLS shape + **JA3/JA3S** comparison (9 features total) | ✅ | ✅ | runnable (v0.5.1; JA4/JA4S → v1.0; goldens → v1.0) |
| 03 | Active probe with no Reality key → HTTP/1 response shape | ✅ | ✅ | runnable (h1 via `active_probe.py`) |
| 03h2 | Active probe with no Reality key → HTTP/2 SETTINGS frame | ✅ | ✅ | runnable (v0.5.2 via `h2_settings_compare.py`) |
| 04 | Port-scan baseline (only expected ports open) | ✅ | ✅ | runnable |
| 05 | Replay-resistance | ✅ | manual | manual — v1.0 automation |

All scripts use Python stdlib only (no third-party deps). Script 02 captures handshake bytes via `ssl.MemoryBIO`, parses ClientHello / ServerHello in-process, and computes JA3 + JA3S inline. Script 03h2 (`h2_settings_compare.py`) speaks the HTTP/2 connection preface inline and parses SETTINGS frames inline. The contract is locked so a v1.0 JA4/JA4S plug-in (or golden snapshots) lands without changing CI.

## Contributing a scenario

1. Write `scenarios/NN-name.md` first — the doc explains *what* and *why* before code exists.
2. Add a script under `scripts/` matching the doc's `### Implementation` section.
3. Exit code contract: 0 = pass, 1 = fail (printable reason), 2 = inconclusive (skip — e.g. dest unreachable).
4. Print exactly one summary line. Print a `WHY:` block on failure with enough detail to investigate without re-running.
5. Update this README's status table.
6. Run `shellcheck scripts/*.sh` + `ruff check scripts/*.py` locally.
