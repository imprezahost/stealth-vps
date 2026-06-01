# v0.12.1 — onboarding/subscription fixes (patch)

> **Patch release.** Three field-test findings from enabling the v0.12.0
> onboarding bridge on a real VPS (`test-tokyo.imprezahost.com`, Tokyo).
> No new features; correctness + UX only. The headline bug (#42) makes
> the subscription + onboarding features **non-functional on single-node
> CLI-only deployments** — the most common shape — so this ships promptly.

## How we got here

Enabling onboarding end-to-end on the Tokyo test box surfaced three real
defects (and one deferred architecture item). The button-import path was
validated working (V2Box imported + connected over Reality/Hysteria); the
bugs are in the surrounding plumbing.

## The defects

### #42 — single-node CLI never writes the subscription `.txt`  🔴

**Root cause.** `cli.py:_post_mutation_sync` (the post-mutation hook called
by `cmd_user_add` / `cmd_user_rotate` / `cmd_user_revoke`) early-returns
before the only `write_subscription_file` call in the whole CLI:

```python
nodes = _fleet.load_fleet()
if not nodes:
    return                       # <-- single-node box bails here
...
if affected_user and affected_user.get("sub_token"):
    ... write_subscription_file(...)   # <-- never reached single-node
```

Compounding it, that write builds URIs via `build_uris_for_user_multinode(
rec, nodes=[])`, which returns `[]` for empty nodes — so even past the
early-return there'd be nothing to write.

**Impact.** On a single-node box (no fleet registered) with subscription
enabled, `s-vps user add` mints the token but **never materialises
`/var/lib/stealth-vps/subscriptions/<token>.txt`** → the subscription URL
404s → the onboarding deep-links + QR point at a dead sub. Breaks the
subscription **and** onboarding features for single-node, CLI-driven hosts
with no Telegram bot. Almost certainly a regression from the v0.10 fleet
refactor that moved the `.txt` write inside the fleet-only hook.

**Reference impl.** The **bot** does it correctly
(`stealth_vps_bot.py:_build_uris_for_user`, line ~323):

```python
if is_control_mode(...):
    nodes = load_fleet()
    if nodes:
        return build_uris_for_user_multinode(rec, nodes, label=label)
return build_uris_for_user(rec, _uri_render_config())   # single-node fallback
```

The CLI is missing this single-node fallback. The fix ports it to the CLI
via a shared, tested helper.

### #41 — `s-vps update` can't enable onboarding (+ tls_email gap)  🟡

`files/s-vps:s_cmd_update` builds its `-e` overrides from a fixed
`extra_vars=( ... )` array (domain / panel / hysteria / bot /
subscription_{enabled,expose}). It **omits**:

- `stealth_vps_onboard_enabled` → the v0.12.0 onboarding bridge is
  **unreachable** through the blessed `installer.env` + `s-vps update`
  path; it only worked via a hand-rolled `ansible-pull -e` (which is what
  we used on Tokyo).
- `stealth_vps_tls_email` → `defaults/main.yml` ships
  `stealth_vps_tls_email: ""`, and `tasks/tls.yml` asserts
  `tls_email | length > 0` whenever a domain is set. So **`s-vps update`
  on any domain-configured host fails the assert** unless the operator
  re-exports the email by hand. Same class of bug; fixed together.

### #43 — onboarding QR opens raw subscription text on a camera scan  🟢 (done)

Field test: scanning the QR with the **phone camera** opened the raw
base64 subscription page (confusing), because the QR encodes the
subscription URL — which is correct for an **in-app** scanner but not for
the camera. Decision (operator): keep the universal sub-URL QR, clarify
the copy. **Already implemented** in `files/onboard/onboard.js` (new
`scanQr` heading + a `qrHint` line under the QR) and deployed to the Tokyo
bundle; needs commit + a docs note.

## Locked ADRs

- **ADR-1 (#42 config source).** The CLI builds a single-node
  `UriRenderConfig` from the host's **state files** (`reality.state.yml`,
  `hysteria.state.yml`, and best-effort the v0.11 protocol state files
  when present) + the host from `installer.env`
  (`STEALTH_DOMAIN`/`STEALTH_VPS_PUBLIC_HOST`). Factored as a shared,
  unit-tested helper in `bot_core` (e.g. `local_uri_config(...)`) so the
  config *sourcing* is tested in one place and the URI *building* stays
  `build_uris_for_user` (already shared with the bot).
  *Rejected:* reality+hysteria-only minimal write (ships a new
  partial-coverage bug); a brand-new role-rendered uri-config file (too
  invasive for a patch).

- **ADR-2 (#42 write trigger).** Refactor `_post_mutation_sync` so the
  subscription `.txt` refresh is **unconditional** (single-node + control
  both write it). Only the SSH *fleet push* stays fleet-gated. Single-node
  → `build_uris_for_user(rec, local_cfg)`; control/fleet → existing
  `build_uris_for_user_multinode`. `--no-sync` still skips everything.

- **ADR-3 (#41 scope).** Plumb **both** `stealth_vps_onboard_enabled` and
  `stealth_vps_tls_email` through `files/s-vps` (`extra_vars`) **and** the
  `installer.env` template (`cli_wrapper.yml`). One coherent fix for the
  "wrapper extra_vars is incomplete" class.

- **ADR-4 (acme.sh vs Caddy on :443 — DEFERRED).** A domain-configured
  host runs **two** ACME clients for the same name: acme.sh (HTTP-01 :80,
  Hysteria/x-ui cert) and Caddy (TLS-ALPN-01 :443, subscription/onboarding
  cert). With Caddy holding :80+:443, acme.sh standalone renewals can't
  bind :80. The proper fix (Caddy issues once and Hysteria points at
  Caddy's cert dir, **or** the role skips acme.sh when Caddy fronts the
  domain) is a design-level change — **out of scope for this patch**.
  Documented as a known limitation. On the Tokyo test box the onboarding
  validation stays wired via direct `ansible-pull` until this lands.

- **ADR-5 (host resolution).** Unchanged: env-driven
  (`STEALTH_DOMAIN`/`STEALTH_VPS_PUBLIC_HOST`). No auto-IP fallback. Note:
  subscription-expose **requires** a domain (existing assert), so any
  publicly-served `.txt` always carries a real host — the
  `your.vps.example` placeholder only appears on a no-domain box, where
  the sub endpoint is loopback-only anyway.

## Block plan (each block green on pytest before the next)

**Block A — #42 (the serious one).**
- `bot_core.local_uri_config(installer_env, state_dir)` → `UriRenderConfig`
  from state files + host. Reality + Hysteria + best-effort v0.11 protocols.
- Refactor `cli._post_mutation_sync`: write the `.txt` unconditionally;
  single-node via `build_uris_for_user(rec, local_uri_config(...))`.
- Tests: single-node `user add` writes a non-empty `.txt` with the right
  host + protocols; control/fleet path unchanged; `--no-sync` skips.

**Block B — #41 (wrapper plumbing).**
- `files/s-vps`: add `stealth_vps_onboard_enabled=${STEALTH_ONBOARD_ENABLED:-false}`
  and `stealth_vps_tls_email=${STEALTH_TLS_EMAIL:-}` to `extra_vars`.
- `cli_wrapper.yml` installer.env template: add the two `:= ` default
  lines (`STEALTH_ONBOARD_ENABLED`, `STEALTH_TLS_EMAIL`).
- Tests: a wrapper-shape test asserting both vars are passed (grep the
  rendered argv / the s-vps script); installer.env template renders the
  new keys.

**Block C — #43 + docs + cut.**
- Commit the `onboard.js` copy change (already on the box).
- `docs/onboarding.md`: QR section — "scan from the app, not the camera".
- `docs/operations.md`: note single-node `.txt` is now written by the CLI;
  the acme/Caddy :443 known-limitation (ADR-4).
- Version bump v0.12.0 → v0.12.1, CHANGELOG, README/pulumi "v0.12.0" refs.
- Cut: feature branch → merge --no-ff → annotated tag `v0.12.1` → GitLab →
  GitHub mirror (PAT) → Tokyo smoke → GitHub release page → scrub PAT.

## Test + release gates

- pytest green at each block (current baseline 540).
- Tokyo smoke for v0.12.1: `s-vps update` path now accepts onboard +
  tls_email; **single-node `s-vps user add` writes a working `.txt`**
  (the regression test, live).
- **No Claude attribution** on any commit / tag / release note.
- GitLab = source of truth; GitHub = release mirror (PAT scrubbed after).
