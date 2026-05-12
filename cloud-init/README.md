# cloud-init bootstrap

This directory contains a `cloud-init` user-data template that bootstraps the full stealth-vps stack on first boot.

## Usage

### With a cloud provider that accepts user-data

Paste the contents of `stealth-vps.yaml` as user-data when creating the VPS. Adjust the `extra-vars` block to override role defaults if you need to.

### With Proxmox

```bash
qm set <VMID> --cicustom "user=local:snippets/stealth-vps.yaml"
qm cloudinit update <VMID>
```

### Pinning a version

Edit the `ansible-pull -C v0.1.0` line to the release tag you want to install. **Always pin** in production — `main` may break on you between deploys.

## What it does

1. Installs `ansible`, `git`, `python3-pip` via the distro package manager.
2. Writes `/etc/stealth-vps/extra-vars.yml` with your overrides.
3. Runs `ansible-pull` against this repo at the pinned tag, applying the `stealth-vps` playbook locally.
4. Logs everything to `/var/log/stealth-vps/bootstrap.log`.
5. Writes connection credentials to `/root/stealth-vps-credentials.txt` (mode 0600).

## Caveats

- The bootstrap assumes Debian 12 or Ubuntu 22.04+.
- The default `extra-vars.yml` uses sensible Reality defaults. For best resistance, change `reality_dest` to a different real site so your fingerprint isn't identical to every other deployment.
