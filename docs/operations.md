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

## Troubleshooting

- `journalctl -u xray` — Reality / Xray logs
- `journalctl -u hysteria-server` — Hysteria2 logs
- `journalctl -u x-ui` — panel logs
- `journalctl -u fail2ban` — ban events
- `/var/log/stealth-vps/` — install / playbook output
