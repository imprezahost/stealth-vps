# Probe-resistance test suite

> **4 of 5 scenarios runnable as of v0.4.1.** Scenario 05 (replay-resistance) stays manual; v1.0 will plug in raw-packet JA4/JA4S + automated replay.

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

A manual-trigger GitLab CI job (`probe-resistance`) runs scenarios 01 + 02 + 03 against a staging deploy. It is **not** part of the default pipeline — probe-resistance tests need a live VPS, not a Docker runner, and we don't want a network blip flaking the release pipeline. Trigger it from the GitLab UI on demand.

## Scenario status (v0.4.1)

| # | Scenario | Doc | Script | Status |
|---|---|---|---|---|
| 01 | HTTPS direct probe → must return `dest`-shaped HTML | ✅ | ✅ | runnable |
| 02 | TLS shape comparison (server vs dest, 7 features) | ✅ | ✅ | runnable (v0.4.1; true JA3/JA4 deferred to v1.0) |
| 03 | Active probe with no Reality key → response shape | ✅ | ✅ | runnable (h1; h2 frame check deferred to v1.0) |
| 04 | Port-scan baseline (only expected ports open) | ✅ | ✅ | runnable |
| 05 | Replay-resistance | ✅ | manual | manual — v1.0 automation |

Scripts 02 and 03 compare TLS handshake + HTTP response shapes using Python stdlib only (no third-party deps yet); the contract is locked so a v1.0 plug-in for byte-level JA3/JA4 can subsume the scenario without changing how CI calls it.

## Contributing a scenario

1. Write `scenarios/NN-name.md` first — the doc explains *what* and *why* before code exists.
2. Add a script under `scripts/` matching the doc's `### Implementation` section.
3. Exit code contract: 0 = pass, 1 = fail (printable reason), 2 = inconclusive (skip — e.g. dest unreachable).
4. Print exactly one summary line. Print a `WHY:` block on failure with enough detail to investigate without re-running.
5. Update this README's status table.
6. Run `shellcheck scripts/*.sh` + `ruff check scripts/*.py` locally.
