# Example: Hetzner Cloud (Pulumi)

End-to-end Pulumi stack that provisions one stealth-vps host on Hetzner Cloud. Same resource shape as `terraform/examples/hetzner/`, ported to TypeScript.

## What it creates

| Resource | Why |
|---|---|
| `hcloud.SshKey` (`admin`) | Registers your local pubkey in your Hetzner project so server creation can reference it. The same pubkey also lands in `/root/.ssh/authorized_keys` via cloud-init — intentional belt-and-suspenders. |
| `hcloud.Server` (`vps`) | The actual VPS. Hands the rendered cloud-init to `userData`. Labels include `stealth_version` so `hcloud server list -l project=stealth-vps` works. IPv4 + IPv6 enabled by default. |

The cloud-init string comes from `buildCloudInit(...)` in the sibling `stealth-vps/` package — same inputs as the Terraform module's variables.

## Quickstart

```bash
# 1. Install the Pulumi CLI (if not already): https://www.pulumi.com/docs/install/

# 2. Install JS deps (both the example AND the linked stealth-vps package)
cd pulumi/examples/hetzner
npm install

# 3. Initialise a Pulumi stack (one-time per stack name)
pulumi stack init dev

# 4. Set required + optional config
pulumi config set --secret hcloudToken "$(cat ~/.hetzner-token)"
pulumi config set serverName        stealth-vps-fsn1
pulumi config set serverType        cax11
pulumi config set location          fsn1
pulumi config set stealthVersion    v0.7.1
# Optional LE:
# pulumi config set domain            vpn.example.com
# pulumi config set letsencryptEmail  ops@example.com

# 5. Provision
pulumi up

# 6. Watch cloud-init progress (takes ~3-5 min)
pulumi stack output bootstrapLogHint --show-secrets
$(pulumi stack output bootstrapLogHint --show-secrets)

# 7. Once "stealth-vps cloud-init bootstrap finished" appears:
$(pulumi stack output credentialsHint --show-secrets)
```

## Outputs

```bash
pulumi stack output                 # list all
pulumi stack output ipv4            # just the IPv4
pulumi stack output -j              # JSON
```

| Output | What |
|---|---|
| `ipv4` | Public IPv4 of the VPS |
| `ipv6` | Public IPv6 |
| `sshCommand` | Ready-to-paste SSH (only works after cloud-init finishes) |
| `credentialsHint` | One-liner to `cat /root/stealth-vps-credentials.txt` over SSH |
| `bootstrapLogHint` | One-liner to `tail -f` the bootstrap log over SSH |
| `stealthVersionOut` | Echo of the deployed release tag |

## ARM versus AMD

`cax11` default = ARM (Ampere Altra cores), 2v/4GB, ~€3.79/mo. Exercises the v0.4.0 arm64 support in the role. AMD alternatives:

- `cx22` — 2v/4GB AMD, ~€4.49/mo
- `cx32` — 4v/8GB AMD, ~€7.99/mo
- `cax21` — 4v/8GB ARM, ~€6.49/mo

The cloud-init is architecture-independent; the role auto-detects via `ansible_facts.architecture`.

## Tearing it down

```bash
pulumi destroy
pulumi stack rm dev    # if you want to remove the stack entirely
```

## Multi-region fleet

For a fleet (one stealth-vps per region), use `pulumi.ComponentResource` to wrap the (key, server) pair and iterate over a regions list at the top level. Out of scope for this minimal example. The same approach works with `for_each` in the Terraform Hetzner example — Pulumi just lets you use real loop constructs.

## Compatibility note

The Pulumi `hcloud` provider is community-maintained (`@pulumi/hcloud`). At v0.7.1 we pin `^1.21.0`. If the upstream provider's resource API changes (renamed properties, etc.), the example may need a port — same risk as the Terraform `hetznercloud/hcloud` provider pin. We track the major versions in the example's `package.json`.
