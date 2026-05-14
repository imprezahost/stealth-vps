# Terraform

> **v0.5.x (alpha).** Provider-agnostic module that generates the cloud-init `user_data` string for any cloud Terraform supports. One concrete worked example (Hetzner Cloud). DigitalOcean / AWS / Vultr / Proxmox examples land as the module proves itself in the field.

## What this is

The static `cloud-init/stealth-vps.yaml` works fine when you create one VPS by hand and paste user-data into the provider's web console. It does *not* work well when you want infrastructure-as-code: variables (SSH key, domain, release pin, dest hostname) get hardcoded into the YAML, and the YAML drifts between repo and the deployed servers as releases ship.

The Terraform module here generates the cloud-init `user_data` dynamically from a single set of variables. You consume it like any other module:

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.5.3"

  stealth_version = "v0.5.3"   # which release the cloud-init bootstrap pins to
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

```
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
    └── hetzner/                         # end-to-end worked example
        ├── README.md
        ├── main.tf                      # hcloud_server + the module
        ├── variables.tf                 # h cloud token, server_type, location
        ├── outputs.tf                   # IP + SSH command
        └── terraform.tfvars.example     # fill in + rename to .tfvars
```

## Why a module, not a root config?

Two reasons:

1. **Cloud choice belongs to the user.** A root config would force everyone onto one provider. The module pattern lets the user pick `hcloud`, `aws`, `digitalocean`, `proxmox`, anything — and reuse the same cloud-init logic across them.

2. **Existing infra reuse.** Most users already have Terraform code for their cloud (VPC, security groups, DNS records). Slotting in `module "stealth_vps_bootstrap"` lets them add the proxy host to an existing stack instead of forking off a parallel Terraform tree.

## Versioning

The module's release cycle matches the role's. To pin both together:

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.5.3"
  stealth_version = "v0.5.3"
  # ...
}
```

Mismatched versions are valid (`?ref=v0.5.0` + `stealth_version = "v0.4.2"` deploys an older role from a current module) but unusual. The module's interface evolves; the role's defaults evolve too.

## Pulumi

Pulumi reference is listed for v0.5.0 in `CHANGELOG.md` but lands as a separate sprint. The mechanism is identical (generate cloud-init string from typed inputs, hand to provider's "create server" call); the language is different (TypeScript / Python / Go instead of HCL).
