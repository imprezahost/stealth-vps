# stealth-vps on DigitalOcean — Pulumi example

Mirrors [`terraform/examples/digitalocean/`](../../../terraform/examples/digitalocean/).

## What gets provisioned

- `digitalocean.SshKey` — your local SSH pubkey, registered in the account
- `digitalocean.Droplet` — Debian 12 x64, IPv6 on, in your chosen region
- `digitalocean.Firewall` — surgical opens (SSH non-default, Reality TCP 443, Hysteria2 UDP range, optional HTTP 80 for LE)

## Quick start

```bash
cd pulumi/examples/digitalocean
npm install
pulumi stack init dev
pulumi config set --secret doToken <your-do-api-token>
cp Pulumi.dev.yaml.example Pulumi.dev.yaml  # then edit
pulumi up
```

## Config keys

| Key | Default | Notes |
|---|---|---|
| `doToken` | required | DigitalOcean API token, scope = write |
| `region` | `fra1` | `fra1`/`ams3`/`nyc3`/`sfo3`/`sgp1`/... |
| `size` | `s-1vcpu-1gb` | `s-1vcpu-1gb` (~$6/mo) up to whatever |
| `image` | `debian-12-x64` | Debian 12 x64 only — DO doesn't have arm64 droplets |
| `sshPort` | `22550` | |
| `stealthVersion` | `v0.7.4` | |
| `domain` | `null` | Enables Let's Encrypt when set |
| `letsencryptEmail` | `""` | Required when `domain` is set |
| `realityDest` | `www.microsoft.com:443` | |

## Cost

- `s-1vcpu-1gb` Droplet: $6/mo (1 vCPU / 1 GB / 25 GB SSD / 1 TB transfer)
- `s-1vcpu-2gb`: $12/mo (recommended if you want headroom)
- Bandwidth: 1 TB included; overage $0.01/GB

Total **$6/mo** for the minimal config.

## Notes

- DO doesn't have ARM droplets in 2026 — `architecture: arm64` is a no-op here (config key isn't even exposed). Use the AWS or Hetzner examples for arm64.
- DO firewalls are tag-based; the droplet's tags bind the firewall automatically. No security-group-attach dance.
