# Example: AWS

End-to-end Terraform that provisions one stealth-vps host on AWS EC2.

## What it creates

| Resource | Why |
|---|---|
| `aws_key_pair.admin` | Registers your local pubkey in the chosen AWS region. AWS injects the key into the instance via metadata service AND our cloud-init's `ssh_authorized_keys` block puts it in `/root/.ssh/authorized_keys` — intentional belt-and-suspenders. |
| `aws_security_group.stealth` | Surgical opens: SSH non-default port (var.ssh_port) from `var.allow_ssh_from`, Reality TCP 443 from anywhere, Hysteria2 UDP hop range from anywhere, optional HTTP 80 when `var.domain` is set (LE HTTP-01). All egress allowed. IMDSv2 required on the instance. |
| `aws_instance.vps` | The actual VPS in the default VPC. Hands `module.stealth_vps_bootstrap.cloud_init` to `user_data`. Lifecycle ignores `user_data` changes — cloud-init runs once on first boot; later config changes go through `ansible-pull` re-runs over SSH, not Terraform replace. |

What you still own outside Terraform:

- **DNS** A/AAAA records pointing at `public_ip` (when `domain` is set for LE).
- **Elastic IP** if you want a stable address (`aws_eip` + association — out of scope for the minimal example; the default public IP changes on stop+start).
- **Backups** / snapshots / monitoring (`aws_backup_plan`, etc.).
- **Reverse-DNS PTR** (separate AWS support ticket for production use).

## Quickstart

```bash
# 1. Credentials in env (or use `aws configure` profile)
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."

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

## ARM versus AMD

Default `t4g.small` (Graviton ARM, 2v/2GB) exercises the v0.4.0 arm64 support and is the cheapest path. Swap to `t3.small` (or larger) for x86_64 — **and change `architecture = "amd64"`** to match. The AMI lookup filters by architecture; mismatched type vs arch fails at apply.

## Cost guidance (us-west-2 published list, Q2/2026)

| Instance | vCPU | RAM | Arch | On-demand $/hr | Approx $/mo |
|---|---|---|---|---|---|
| t4g.nano   | 2 | 0.5 GB | ARM | $0.0042 | $3 |
| t4g.micro  | 2 | 1 GB   | ARM | $0.0084 | $6 |
| **t4g.small** | 2 | 2 GB | ARM | **$0.0168** | **$12** |
| t4g.medium | 2 | 4 GB   | ARM | $0.0336 | $24 |
| t3.small   | 2 | 2 GB   | AMD | $0.0208 | $15 |
| t3.medium  | 2 | 4 GB   | AMD | $0.0416 | $30 |

Plus EBS (~$0.08/GB·month, gp3 10 GB ≈ $0.80) and 1 GB data egress ≈ $0.09. The role idles at ~200-300 MB RAM and ~2% CPU — `t4g.small` is comfortable; `t4g.nano` works for a single-user setup but starts to struggle when client traffic ramps up.

**Heads-up**: AWS data-egress is the gotcha. ~$90/TB outbound after the first GB. Hetzner / OVH / DigitalOcean ship 1-20 TB free with their flat plans, which is why those providers tend to be cheaper for a stealth-vps node serving any real traffic. AWS makes sense when you're tying this into an existing AWS estate (VPC peering, IAM, CloudWatch logs already in place) — not for greenfield single-VPS deploys.

## Tearing it down

```bash
terraform destroy
```

Removes the instance, security group, key pair. The Debian AMI obviously stays available globally; no per-account cleanup needed.

## Multi-region fleet

For a fleet (one stealth-vps per region), wrap the module + resources in a `for_each` map keyed on region. The provider needs `alias` per region (`provider "aws" { alias = "tokyo" region = "ap-northeast-1" }`). Out of scope for this minimal example.

## Compatibility notes

- `architecture` validation accepts `"arm64"` and `"amd64"` only. To use Graviton2/3, set `arm64` + a `t4g.*` / `m6g.*` / `c7g.*` instance.
- IMDSv2 is required (`http_tokens = "required"`). The role doesn't talk to IMDS for anything; this is just AWS-side hardening.
- Default VPC IPv6: AWS accounts created after late 2022 have an IPv6 CIDR on the default subnet. Older accounts may need `aws_default_subnet.default.assign_ipv6_address_on_creation = true` first; the `public_ipv6` output will be `null` if no v6 is assigned.
- The example doesn't allocate an Elastic IP. Add one if you need a stable address:
  ```hcl
  resource "aws_eip" "vps" {
    domain   = "vpc"
    instance = aws_instance.vps.id
  }
  ```
