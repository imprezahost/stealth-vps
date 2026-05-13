# Operations

> Placeholder — to be expanded as v0.1.0 lands.

Day-to-day operations once the stack is installed.

## Adding a user

(To be documented.)

## Rotating credentials

(To be documented.)

## Upgrading to a new release

```bash
# pin the new version and rerun
ansible-pull -U https://github.com/imprezahost/stealth-vps.git \
  -C v0.2.0 \
  -i 'localhost,' \
  -c local \
  ansible/playbooks/site.yml
```

Or, with the local checkout workflow:

```bash
cd stealth-vps
git fetch --tags
git checkout v0.2.0
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml
```

## Rolling back

The role is idempotent and supports re-running an older version. To downgrade, re-run `ansible-pull` against the older tag. Generated credentials persist — they are not regenerated unless you explicitly trigger a rotation task.

## Monitoring

Grafana dashboard is at `http://<your-vps>:3000` once the observability bundle is enabled. Default credentials are written to `/root/stealth-vps-credentials.txt` on first run.

## Running on arm64

`stealth-vps` runs on amd64 and arm64 hosts from v0.4.0 onward. Same `ansible-playbook` invocation; the role detects the architecture and pulls the right binary variants automatically.

Tested concretely on:

| Provider / class | Image | Notes |
|---|---|---|
| Oracle Cloud Free Tier (Ampere A1) | Ubuntu 22.04 / 24.04 arm64 | 4 OCPU + 24 GB free for life; recommended starting point for arm64 evaluations |
| AWS Graviton2/3 (`*g.*`) | Debian 12 / Ubuntu 24.04 arm64 | Production-grade; smoke-tested |
| Hetzner ARM (CAX line) | Debian 12 arm64 | EU-located, BBR works out of the box |
| Raspberry Pi 4 / 5 (Debian 12) | 64-bit | Works but not recommended as a stealth-vps host — uplink + thermal limits |

The architecture map lives in `defaults/main.yml` as `stealth_vps_arch_map`. The role today maps `x86_64 → amd64` and `aarch64 → arm64`. If you want to try an unvalidated arch (armv7, 386, riscv64 once upstream publishes binaries), extend the map and rerun — every binary URL is derived from this fact, so adding a row is the only change needed at the role level.

Caveats specific to arm64 hosts:

- **3X-UI panel arm64 tarball** comes from the same `MHSanaei/3x-ui` release pin (`stealth_vps_panel_version`). Verified to publish `x-ui-linux-arm64.tar.gz` for every release the role currently pins to.
- **Hysteria2 arm64 binary** is published per release at `apernet/hysteria` as `hysteria-linux-arm64`. No source build needed.
- **Kernel BBR** works the same on arm64 as on amd64 — the `tcp_bbr` module is in the standard Debian/Ubuntu arm64 kernel.
- **Molecule scenario** still runs only on amd64 in CI; arm64 hosts get validated manually until we add an arm64 runner. See `tests/README.md`.

## Troubleshooting

- `journalctl -u xray` — Reality / Xray logs
- `journalctl -u hysteria-server` — Hysteria2 logs
- `journalctl -u x-ui` — panel logs
- `journalctl -u fail2ban` — ban events
- `/var/log/stealth-vps/` — install / playbook output
