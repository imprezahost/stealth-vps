# Example: Vultr

End-to-end Terraform that provisions one stealth-vps host on Vultr.

## What it creates

| Resource | Why |
|---|---|
| `vultr_ssh_key.admin` | Registers your local pubkey in Vultr's account-wide SSH registry. The instance boots with the key injected AND our cloud-init writes it to `/root/.ssh/authorized_keys`. |
| `vultr_firewall_group.stealth` + 6-10 `vultr_firewall_rule.*` | Each Vultr firewall rule is one (protocol, IPv4-or-IPv6, port, source) tuple. We emit a v4 + v6 pair for the public-internet opens (Reality, Hysteria2, optional LE HTTP-01), one rule per SSH source CIDR, and rely on Vultr's "all egress" default (no outbound rule resource exists in the provider). |
| `vultr_instance.vps` | The instance. Hands `module.stealth_vps_bootstrap.cloud_init` to `user_data`. `enable_ipv6 = true` by default. Backups toggle off (+20% of plan). Lifecycle ignores `user_data` changes — config post-first-boot flows through `ansible-pull` over SSH. |

## Architecture note — Vultr is amd64-only (Q2/2026)

Like DigitalOcean, Vultr does not yet offer arm64 plans. Every `vc2-*` and `vhf-*` plan in the price table below is amd64. ARM coverage stays with the Hetzner (`cax11`) + AWS (`t4g.small`) examples.

## Quickstart

```bash
export TF_VAR_vultr_api_key="$(cat ~/.vultr-token)"

cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

terraform init
terraform plan
terraform apply

$(terraform output -raw bootstrap_log_hint)   # tail cloud-init for ~3-5 min
$(terraform output -raw credentials_hint)     # once "bootstrap finished" appears
```

## Cost guidance (Q2/2026 published list, us-west `lax`)

| Plan slug | vCPU | RAM | Disk | $/mo | Notes |
|---|---|---|---|---|---|
| vc2-1c-1gb       | 1 | 1 GB | 25 GB | $6 | Tight |
| **vc2-1c-2gb**   | 1 | 2 GB | 55 GB | **$12** | **Recommended baseline** |
| vc2-2c-2gb       | 2 | 2 GB | 60 GB | $18 | Better TLS handshake CPU |
| vc2-2c-4gb       | 2 | 4 GB | 80 GB | $24 | Multi-user comfort |
| vhf-1c-2gb       | 1 | 2 GB | 60 GB | $13 | High-freq CPU (~30% faster per-core) |
| vhf-2c-4gb       | 2 | 4 GB | 128 GB | $32 | High-freq, comfortable |

**Bandwidth included** scales by plan size: ~1-5 TB outbound free, $0.01/GB over. Roughly the same model as DO; cheaper than AWS for any real traffic.

## Region notes for CN routing

Vultr's `sgp` (Singapore) has the best mainland-CN routing on their network in our experience; `nrt` (Tokyo) is competitive but transits through KIX which adds latency. Avoid `lax` for CN traffic — it's good for US-west users but the Pacific underwater cables are congested.

## Vultr-specific quirks

- **Per-IP-family firewall rules**: each rule is either `ip_type = "v4"` OR `"v6"`. Adding an open to "the whole internet" means emitting both rules. The example does this automatically for Reality + Hysteria2; SSH stays v4-only by default (override `allow_ssh_from` to add v6 entries — you'll also want to duplicate the resource block for `ip_type = "v6"` if you do).
- **OS IDs renumber**: Vultr periodically renumbers their OS catalog. The example pins `os_id = 477` for Debian 12 x64. If that stops working, query the current ID with:
  ```bash
  curl -s https://api.vultr.com/v2/os | jq '.os[] | select(.name | test("Debian 12"))'
  ```
- **No outbound firewall**: Vultr instances have full egress. The role's `unattended-upgrades` + `acme.sh` + Reality-dest reachability all work without explicit egress rules.

## Tearing it down

```bash
terraform destroy
```

Removes the instance, all firewall rules, the firewall group, and the SSH key from your Vultr account.

## Multi-region fleet

Same pattern as DO — `for_each` over a regions map. Vultr's provider is single-account / multi-region without aliases. Out of scope for the minimal example.
