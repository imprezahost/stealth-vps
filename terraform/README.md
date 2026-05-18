# Terraform

> **v0.5.x (alpha).** Provider-agnostic module that generates the cloud-init `user_data` string for any cloud Terraform supports. **Five concrete worked examples**: **Hetzner Cloud** (ARM-by-default, flat €3.79/mo), **AWS EC2** (ARM Graviton or x86, pay-as-you-go), **DigitalOcean** (amd64, flat $6+/mo, 1 TB transfer included), **Vultr** (amd64, flat $6+/mo, regions optimised for Asia/CN routing), and **Proxmox VE** (self-hosted, cloud-init via snippet file, free + your own hardware). Pulumi reference lands next.

## What this is

The static `cloud-init/stealth-vps.yaml` works fine when you create one VPS by hand and paste user-data into the provider's web console. It does *not* work well when you want infrastructure-as-code: variables (SSH key, domain, release pin, dest hostname) get hardcoded into the YAML, and the YAML drifts between repo and the deployed servers as releases ship.

The Terraform module here generates the cloud-init `user_data` dynamically from a single set of variables. You consume it like any other module:

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.7.3"

  stealth_version = "v0.7.3"   # which release the cloud-init bootstrap pins to
  ssh_public_key  = file("~/.ssh/id_ed25519.pub")
  ssh_port        = 22550      # matches stealth_hardening_ssh_port default
  domain          = "vpn.example.com"  # null for no LE
  reality_dest    = "www.microsoft.com:443"
}

# Then hand the output to whatever cloud's "create server" resource:
resource "hcloud_server" "vps" {
  name        = "stealth-vps-tokyo"
  server_type = "cax11"        # Hetzner ARM
  image       = "debian-12"
  location    = "fsn1"
  user_data   = module.stealth_vps_bootstrap.cloud_init
  ssh_keys    = [hcloud_ssh_key.admin.id]
}
```

The module has no provider dependencies of its own. It produces a string. You take that string and pass it to AWS's `aws_instance.user_data`, DigitalOcean's `digitalocean_droplet.user_data`, Hetzner's `hcloud_server.user_data`, Vultr's `vultr_instance.user_data`, OpenStack's `openstack_compute_instance_v2.user_data`, or Proxmox's `proxmox_vm_qemu.cicustom`. All of them accept arbitrary strings.

## Layout

```text
terraform/
├── README.md                            # this file
├── modules/
│   └── stealth-vps/
│       ├── README.md                    # module-specific quickstart + var reference
│       ├── main.tf                      # templatefile() invocation
│       ├── variables.tf                 # inputs + validation
│       ├── outputs.tf                   # `cloud_init` (the user_data string)
│       ├── versions.tf                  # required_version + required_providers (none)
│       └── templates/
│           └── stealth-vps.cloud-init.tftpl   # the cloud-init template
└── examples/
    ├── hetzner/                         # end-to-end worked example (Hetzner Cloud)
    │   ├── README.md
    │   ├── main.tf                      # hcloud_server + the module
    │   ├── variables.tf                 # hcloud token, server_type, location
    │   ├── outputs.tf                   # IP + SSH command
    │   └── terraform.tfvars.example     # fill in + rename to .tfvars
    ├── aws/                             # end-to-end worked example (AWS EC2)
    │   ├── README.md
    │   ├── main.tf                      # aws_key_pair + aws_security_group + aws_instance + Debian 12 AMI lookup
    │   ├── variables.tf                 # region, instance_type, architecture (arm64/amd64), allow_ssh_from CIDRs
    │   ├── outputs.tf                   # public IP + SSH command + AMI ID resolved
    │   ├── versions.tf                  # required_providers aws ~> 5.70
    │   └── terraform.tfvars.example
    ├── digitalocean/                    # end-to-end worked example (DigitalOcean)
    │   ├── README.md
    │   ├── main.tf                      # digitalocean_ssh_key + digitalocean_firewall + digitalocean_droplet
    │   ├── variables.tf                 # region, size (amd64 only on DO), allow_ssh_from CIDRs, monitoring/backups toggles
    │   ├── outputs.tf                   # IPv4 + IPv6 + SSH command + DO console URL
    │   ├── versions.tf                  # required_providers digitalocean ~> 2.40
    │   └── terraform.tfvars.example
    ├── vultr/                           # end-to-end worked example (Vultr)
    │   ├── README.md
    │   ├── main.tf                      # vultr_ssh_key + vultr_firewall_group + per-IP-family rules + vultr_instance
    │   ├── variables.tf                 # region (sgp = best CN routing), plan, allow_ssh_from CIDRs, enable_ipv6/backups
    │   ├── outputs.tf                   # IPv4 + IPv6 + Vultr console URL
    │   ├── versions.tf                  # required_providers vultr ~> 2.21
    │   └── terraform.tfvars.example
    └── proxmox/                         # end-to-end worked example (Proxmox VE, self-hosted)
        ├── README.md
        ├── main.tf                      # local_file (cloud-init snippet) + proxmox_vm_qemu (cloned from a Debian 12 cloud-init template)
        ├── variables.tf                 # pm_api_* auth, target_node, vmid, template_name, snippets storage + path, cores/memory/disk
        ├── outputs.tf                   # vmid + default_ipv4 (via QEMU guest agent)
        ├── versions.tf                  # required_providers Telmate/proxmox ~> 3.0 + hashicorp/local ~> 2.5
        └── terraform.tfvars.example     # includes the qm template-creation recipe inline
```

## Why a module, not a root config?

Two reasons:

1. **Cloud choice belongs to the user.** A root config would force everyone onto one provider. The module pattern lets the user pick `hcloud`, `aws`, `digitalocean`, `proxmox`, anything — and reuse the same cloud-init logic across them.

2. **Existing infra reuse.** Most users already have Terraform code for their cloud (VPC, security groups, DNS records). Slotting in `module "stealth_vps_bootstrap"` lets them add the proxy host to an existing stack instead of forking off a parallel Terraform tree.

## Versioning

The module's release cycle matches the role's. To pin both together:

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.7.3"
  stealth_version = "v0.7.3"
  # ...
}
```

Mismatched versions are valid (`?ref=v0.5.0` + `stealth_version = "v0.4.2"` deploys an older role from a current module) but unusual. The module's interface evolves; the role's defaults evolve too.

## Pulumi

Pulumi reference is listed for v0.5.0 in `CHANGELOG.md` but lands as a separate sprint. The mechanism is identical (generate cloud-init string from typed inputs, hand to provider's "create server" call); the language is different (TypeScript / Python / Go instead of HCL).
