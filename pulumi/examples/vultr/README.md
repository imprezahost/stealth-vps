# stealth-vps on Vultr — Pulumi example

Mirrors [`terraform/examples/vultr/`](../../../terraform/examples/vultr/).

## What gets provisioned

- `vultr.SshKey` — your local SSH pubkey
- `vultr.FirewallGroup` + per-port-and-family `vultr.FirewallRule` entries (Vultr's API is one rule per (proto, port, source family), so SSH/Reality/Hysteria each get 2 rules — IPv4 + IPv6)
- `vultr.Instance` — Debian 12 x64, IPv6 on, base64-encoded cloud-init

## Quick start

```bash
cd pulumi/examples/vultr
npm install
pulumi stack init dev
pulumi config set --secret vultrApiKey <your-vultr-api-key>
cp Pulumi.dev.yaml.example Pulumi.dev.yaml  # then edit
pulumi up
```

## Config keys

| Key | Default | Notes |
|---|---|---|
| `vultrApiKey` | required | Vultr API key (read+write scope) |
| `region` | `fra` | `fra`/`ams`/`lon`/`sea`/`atl`/`sgp`/... |
| `plan` | `vc2-1c-1gb` | amd64 only on Vultr's compute plans |
| `osId` | `477` | Debian 12 x64. Check `https://api.vultr.com/v2/os` for current IDs |
| `sshPort` | `22550` | |
| `stealthVersion` | `v0.7.4` | |
| `domain` | `null` | Enables LE |
| `letsencryptEmail` | `""` | |

## Cost

- `vc2-1c-1gb`: $6/mo
- `vc2-1c-2gb`: $12/mo (recommended)
- Bandwidth: 1 TB included; $0.01/GB overage

Total **$6/mo** for the minimal config.

## Vultr quirks

- The provider is `@ediri/vultr` (community-maintained) since Vultr doesn't ship an official Pulumi provider. Stable for the resources we use here.
- Vultr's firewall is one rule per (protocol, port, source family) — this stack creates 6 rules for the minimum (SSH+Reality+Hysteria × IPv4+IPv6) and 8 with LE on. Not a Pulumi quirk, a Vultr API design choice.
- `userData` is **base64**-encoded for Vultr (vs raw YAML for AWS / DO / Hetzner). The example handles the encoding inline.
