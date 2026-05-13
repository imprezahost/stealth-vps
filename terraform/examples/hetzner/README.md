# Example: Hetzner Cloud

End-to-end Terraform that provisions one stealth-vps host on Hetzner Cloud.

## What it creates

| Resource | Why |
|---|---|
| `hcloud_ssh_key.admin` | Registers your local pubkey in your Hetzner project so the API call to create the server can reference it. The same pubkey also lands in `/root/.ssh/authorized_keys` via cloud-init — Hetzner's metadata-service injection AND the cloud-init `ssh_authorized_keys` block both fire, which is intentional belt-and-suspenders. |
| `hcloud_server.vps` | The actual VPS. Hands `module.stealth_vps_bootstrap.cloud_init` to `user_data` and lets first-boot do the work. Labels include `stealth_version` so you can `hcloud server list -l project=stealth-vps`. |

What you still own outside Terraform:

- DNS A/AAAA records (if `domain` is set for Let's Encrypt).
- Snapshots / backups (Hetzner's `hcloud_volume`, `hcloud_snapshot` — out of scope for the minimal example).
- Reverse-DNS PTR (optional, set in the Hetzner console once the server has an IP).

## Quickstart

```bash
# 1. Token in env (preferred over committing to tfvars)
export TF_VAR_hcloud_token="$(cat ~/.hetzner-token)"

# 2. Copy + edit the inputs you care about
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 3. Provision
terraform init
terraform plan
terraform apply

# 4. Watch cloud-init finish (takes ~3-5 min)
terraform output bootstrap_log_hint   # gives you the right ssh+tail command

# 5. Once "stealth-vps cloud-init bootstrap finished" appears in the log:
terraform output credentials_hint     # gives you the cat /root/...credentials.txt cmd
```

## ARM versus AMD

Default is `cax11` (ARM 2-vCPU/4GB, ~€3.79/mo). The role supports arm64 since v0.4.0 — `stealth_vps_arch_map` auto-detects. If you'd rather have AMD, swap to `cx22` (~€4.49/mo, 2-vCPU/4GB) — same playbook, same UX.

## Cost guidance (Q2/2026 published list price)

| Server type | vCPU | RAM | Arch | Monthly (Hetzner published) |
|---|---|---|---|---|
| cax11 | 2 | 4 GB | ARM | €3.79 |
| cax21 | 4 | 8 GB | ARM | €6.49 |
| cx22 | 2 | 4 GB | AMD | €4.49 |
| cx32 | 4 | 8 GB | AMD | €7.99 |

The role's footprint (Reality + Hysteria2 + 3X-UI + node_exporter) idles around 200-300 MB RAM and ~2% CPU on a cax11. Sized for a small team; scale up only for sustained throughput.

## Tearing it down

```bash
terraform destroy
```

Removes the server and the SSH key from your Hetzner project. State on the (now-deleted) server is gone with it; if you set `stealth_vps_domain`, you'll keep your LE cert in Let's Encrypt's account history but the next deploy regenerates one (rate-limited to 5/week per registered domain).

## Multi-server fleets

For a fleet (one stealth-vps per region), wrap the module + server in a `for_each` map. The cloud-init is identical per-server except for per-region overrides (you might want different `reality_dest` per route). Out of scope for the minimal example; the module supports it (call the module N times with different inputs, hand each output to a different `hcloud_server` instance).
