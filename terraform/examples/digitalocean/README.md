# Example: DigitalOcean

End-to-end Terraform that provisions one stealth-vps host on DigitalOcean.

## What it creates

| Resource | Why |
|---|---|
| `digitalocean_ssh_key.admin` | Registers your local pubkey in DO's account-wide SSH key registry. The droplet boots with the key injected via DO metadata AND our cloud-init's `ssh_authorized_keys` block writes it to `/root/.ssh/authorized_keys` — intentional belt-and-suspenders. |
| `digitalocean_firewall.stealth` | Surgical opens: SSH non-default port (var.ssh_port) from `var.allow_ssh_from`, Reality TCP 443 + Hysteria2 UDP hop range from anywhere, optional HTTP 80 when `var.domain` is set (LE HTTP-01), ICMP allowed for troubleshooting. All outbound allowed. |
| `digitalocean_droplet.vps` | The droplet. Hands `module.stealth_vps_bootstrap.cloud_init` to `user_data`. IPv6 enabled. Monitoring agent on (free). Backups off (toggle `enable_backups = true` for +20% of the size price). Lifecycle ignores `user_data` changes — config post-first-boot flows through `ansible-pull` over SSH. |

What you still own outside Terraform:

- **DNS** A/AAAA records pointing at the `ipv4` output (when `domain` is set for LE).
- **Reverse-DNS PTR** — DO auto-sets PTR to the droplet name; change in the web console if needed.
- **VPC** / **Spaces** / **Load Balancers** — none needed for a single stealth-vps droplet; add when scaling.

## Quickstart

```bash
# 1. Token in env (preferred over committing to tfvars)
export TF_VAR_do_token="$(cat ~/.do-token)"

# 2. Copy + edit the inputs
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 3. Provision
terraform init
terraform plan
terraform apply

# 4. Watch cloud-init (takes ~3-5 min)
$(terraform output -raw bootstrap_log_hint)

# 5. Once "stealth-vps cloud-init bootstrap finished" appears:
$(terraform output -raw credentials_hint)
```

## Architecture note — DO is amd64-only (as of Q2/2026)

DigitalOcean does not yet offer arm64 droplets. Every size in the price table below is amd64. The role's v0.4.0 arm64 support is exercised by the Hetzner (`cax11`) and AWS (`t4g.small`) examples instead. If/when DO ships arm64, swap the `image` slug + size and the `architecture` will Just Work since the role auto-detects.

## Cost guidance (Q2/2026 published list)

| Size slug | vCPU | RAM | Disk | $/mo | Notes |
|---|---|---|---|---|---|
| s-1vcpu-512mb-10gb | 1 | 512 MB | 10 GB | $4 | Doesn't quite fit — role idles at ~250 MB but apt+ansible-pull peak above |
| **s-1vcpu-1gb**    | 1 | 1 GB   | 25 GB | **$6** | Tight but workable for one user |
| **s-1vcpu-2gb**    | 1 | 2 GB   | 50 GB | **$12** | **Recommended baseline** for multi-user setups |
| s-2vcpu-2gb        | 2 | 2 GB   | 60 GB | $18 | Better CPU for TLS handshake bursts |
| s-2vcpu-4gb        | 2 | 4 GB   | 80 GB | $24 | Comfortable for ~50 active clients |

DO ships **1 TB outbound transfer free** with each droplet (overage at $0.01/GB). Compared to AWS (~$90/TB), this is the main reason DO is a friendlier cost story for a stealth-vps node carrying real traffic.

## Tearing it down

```bash
terraform destroy
```

Removes the droplet, the firewall, and the SSH key from your DO account. Snapshots and backups (if enabled) survive separately — delete them via the DO console if you want full cleanup.

## Multi-region fleet

The same pattern works with `for_each` over a regions map. The DO provider doesn't require per-region aliases (DO's API is global, not regional), which makes the multi-region fan-out simpler than the AWS equivalent. Out of scope for this minimal example.

## Compatibility notes

- The DO firewall resource is its own top-level object (not attached to a VPC) and gets bound to droplets by ID or tag. We bind by ID; for a fleet, swap to `tags = ["project:stealth-vps"]` on both droplet and firewall.
- ICMP is allowed inbound in this example for troubleshooting. Drop the icmp rule if you want maximum invisibility (Reality + Hysteria2 don't need ICMP).
- DO's "monitoring" agent (`enable_monitoring = true`) is independent of the role's `prometheus-node-exporter`. Both can run; they don't conflict.
