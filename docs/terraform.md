# Terraform

> v0.5.0 introduces a Terraform module that generates the cloud-init `user_data` for a stealth-vps deployment, provider-agnostic. One worked example (Hetzner Cloud) ships in the repo; the same pattern works with AWS, DigitalOcean, Vultr, OpenStack, Proxmox, Linode, anything that accepts an arbitrary `user_data` string.

## Why Terraform

The four entry points stealth-vps now supports:

| Path | Best for | Configuration unit |
|---|---|---|
| `install.sh` one-shot | "I have a fresh VPS, just run it" | bash env vars |
| `cloud-init/stealth-vps.yaml` (static) | "I create one VPS by hand and paste user-data" | YAML edits |
| Ansible playbook | "I already have a controller, run the role against it" | inventory + extra-vars |
| **Terraform module (v0.5.0)** | **"I want infra-as-code: same `terraform apply` provisions hardware AND configures it"** | **HCL inputs** |

The Terraform path replaces the static cloud-init for users who:

- Manage multiple stealth-vps hosts across regions or providers.
- Already have Terraform code for their cloud (VPC, security groups, DNS).
- Need to recreate a host from spec without manually editing YAML.
- Want pull-request review on infra changes (variables in `.tfvars`, diffs in `terraform plan`).

## Architecture

```
                 ┌──────────────────────┐
   Your HCL ───▶│  module.stealth_vps  │── output: cloud_init (string)
  (variables)   │   .stealth-vps       │
                 │   (templatefile)     │
                 └──────────────────────┘
                            │
                            ▼
              ┌──────────────────────────┐
              │  Your cloud provider's   │
              │  create-server resource: │     Terraform applies →
              │  hcloud_server,          │      provider API spins
              │  aws_instance,           │      up the VPS with the
              │  digitalocean_droplet,   │      cloud-init we generated.
              │  vultr_instance, ...     │
              └──────────────────────────┘
                            │
                            ▼
                     first-boot cloud-init:
                     apt-get + ansible + ansible-pull
                     ↓
                 stealth-vps deployed
```

The module owns no cloud-side resources. Its only output is a string. The user picks the provider.

## Quickstart (Hetzner Cloud)

The worked example lives in [`terraform/examples/hetzner/`](../terraform/examples/hetzner). End-to-end:

```bash
cd terraform/examples/hetzner
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

export TF_VAR_hcloud_token="$(cat ~/.hetzner-token)"
terraform init
terraform plan
terraform apply

# Watch cloud-init progress (~3-5 min)
$(terraform output -raw bootstrap_log_hint)
```

Then once `final_message` fires:

```bash
$(terraform output -raw credentials_hint)   # cats /root/stealth-vps-credentials.txt
```

## Adapting to a different provider

The module has no `required_providers`. Drop it into any Terraform config:

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.5.1"

  stealth_version = "v0.5.1"
  ssh_public_key  = file("~/.ssh/id_ed25519.pub")
  ssh_port        = 22550
  domain          = "vpn.example.com"
  letsencrypt_email = "ops@example.com"
}

# AWS
resource "aws_instance" "vps" {
  ami           = "ami-..."  # Debian 12 AMI in your region
  instance_type = "t4g.small"  # arm64
  user_data     = module.stealth_vps_bootstrap.cloud_init
  vpc_security_group_ids = [aws_security_group.stealth.id]
  # ...
}

# DigitalOcean
resource "digitalocean_droplet" "vps" {
  name      = "stealth-vps-sgp1"
  region    = "sgp1"
  size      = "s-1vcpu-1gb"
  image     = "debian-12-x64"
  user_data = module.stealth_vps_bootstrap.cloud_init
  ssh_keys  = [digitalocean_ssh_key.admin.fingerprint]
}

# Proxmox (telmate/proxmox)
resource "proxmox_vm_qemu" "vps" {
  name        = "stealth-vps"
  target_node = "pve01"
  iso         = "local:iso/debian-12-genericcloud-amd64.iso"
  cicustom    = "user=local:snippets/stealth-vps-userdata.yaml"
  # Write the cloud-init to a snippet file outside Terraform, or use
  # the `local_file` resource to materialize module.stealth_vps_bootstrap.cloud_init.
}
```

## Inputs / outputs

Full reference in [`terraform/modules/stealth-vps/README.md`](../terraform/modules/stealth-vps/README.md).

## Versioning the deploy

```hcl
module "stealth_vps_bootstrap" {
  source          = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.5.1"
  stealth_version = "v0.5.1"
  # ...
}
```

Two version strings, two purposes:

- `?ref=...` pins the module file itself — the cloud-init template, the validation rules, the input/output schema.
- `stealth_version` pins what `ansible-pull -C <tag>` checks out on first boot. That's the actual role code running on the VPS.

The two can drift (a newer module + an older role) but it's unusual; pinning them to the same release tag is the maintenance-default.

## Pulumi

Pulumi reference is on the v0.5.x roadmap but lands as a separate sprint. The mechanism is identical (typed inputs → cloud-init string → provider's create-server call); the language is TypeScript / Python / Go instead of HCL. The same `templates/stealth-vps.cloud-init.tftpl` can be ported to a Pulumi `pulumi.asset.StringAsset` + interpolations.

## CI

The Terraform module doesn't have its own CI yet. `terraform fmt -check` + `terraform validate` will land in the GitLab pipeline as soon as the runner can `apt-get install` (the runner fix is on the v0.4.3 backlog). Until then, local `terraform fmt -check` against the module + example before opening a PR is the de-facto check.

## Limitations

- The example is Hetzner-only at v0.5.0/v0.5.1. AWS / DigitalOcean / Vultr / Proxmox examples land in later v0.5.x sprints; the module itself works against all of them today.
- `extra_role_vars` is `map(any)` — no per-key validation. Override surface is wide; misnames are silently ignored by Ansible.
- No support for *multi-server fleet* state in the example. The pattern (`for_each` over a regions map) works but is out of scope for the minimal example.
- The `hcloud` provider version pin is `~> 1.49` (Q2/2026). Bump explicitly in the example's `versions.tf` if you want newer.
