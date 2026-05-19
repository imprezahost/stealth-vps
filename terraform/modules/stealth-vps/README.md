# Module: `stealth-vps`

Generates the cloud-init `user_data` string that bootstraps a stealth-vps VPS. Provider-agnostic — pass the output to whichever cloud provider's "create server" resource you use.

## Usage

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.8.0"

  stealth_version = "v0.8.0"
  ssh_public_key  = file("~/.ssh/id_ed25519.pub")
  ssh_port        = 22550
  domain          = "vpn.example.com"
  letsencrypt_email = "ops@example.com"
  reality_dest    = "www.microsoft.com:443"

  extra_role_vars = {
    stealth_vps_hysteria_port_hopping = true
  }
}
```

Then in your cloud-specific resource:

```hcl
resource "hcloud_server" "vps" {
  name        = "stealth-vps-fsn1"
  server_type = "cax11"
  image       = "debian-12"
  location    = "fsn1"
  user_data   = module.stealth_vps_bootstrap.cloud_init
  ssh_keys    = [hcloud_ssh_key.admin.id]
}
```

## Inputs

| Name | Type | Default | Required | Description |
|---|---|---|:---:|---|
| `stealth_version` | string | `"v0.8.0"` | no | Release tag the cloud-init bootstrap pins to. Validated against `^v\d+\.\d+\.\d+...$`. |
| `ssh_public_key` | string | — | **yes** | Full SSH public-key line. Validated to start with `ssh-ed25519` / `ssh-rsa` / `ecdsa-sha2-*`. |
| `ssh_port` | number | `22550` | no | Non-default SSH port the hardening role moves to. 1024 < n < 65536. |
| `domain` | string | `null` | no | DNS name pointing at this VPS → enables Let's Encrypt. `null` keeps self-signed Hysteria2 + HTTP panel. |
| `letsencrypt_email` | string | `""` | no | Required when `domain` is set. Validated email-shape when non-empty. |
| `reality_dest` | string | `"www.microsoft.com:443"` | no | Reality `dest` site (TLS 1.3 + X25519 + HTTP/2, not Cloudflare). |
| `reality_servernames` | list(string) | `["www.microsoft.com"]` | no | SNI hostnames Reality accepts on its inbound. |
| `extra_role_vars` | map(any) | `{}` | no | Free-form Ansible role variable overrides. Merged into `/etc/stealth-vps/extra-vars.yml` on the VPS. |
| `log_dir` | string | `"/var/log/stealth-vps"` | no | Where `ansible-pull` stdout/stderr is teed. |
| `repo_url` | string | `"https://github.com/imprezahost/stealth-vps.git"` | no | Override when forking or using a mirror. |

## Outputs

| Name | Description |
|---|---|
| `cloud_init` | The rendered cloud-init `user_data` string. Pass to your provider's create-server resource. |
| `extra_vars_yaml` | Just the merged Ansible extra-vars YAML — useful for inspection or for handing to a non-cloud-init bootstrap. |
| `stealth_version` | Echoed back from input. Useful for tagging the created resource. |

## What the rendered cloud-init does

On first boot, the VPS:

1. `apt-get update` + upgrade
2. Installs `ansible`, `git`, `python3-pip`, `ca-certificates`
3. Drops the SSH pubkey into `/root/.ssh/authorized_keys`
4. Writes the merged extra-vars YAML to `/etc/stealth-vps/extra-vars.yml` (mode 0600, root-owned)
5. Runs `ansible-pull -U <repo_url> -C <stealth_version> -e @/etc/stealth-vps/extra-vars.yml ansible/playbooks/site.yml`
6. Tees the full output to `${log_dir}/bootstrap.log`

Once `final_message` fires, the panel + Reality + Hysteria2 are running. Credentials land in `/root/stealth-vps-credentials.txt`. See the role's `docs/operations.md` for what to do next.

## Versioning

Pin the `?ref=v...` of the module **and** the `stealth_version` variable together when you want a fully reproducible deploy. They can drift (a newer module + an older role) but that's an unusual configuration; the inputs validate the version string but don't enforce module-vs-role compatibility — that's on the operator.

## Provider compatibility

The module produces a plain string. Tested-compatible target resources (anything that accepts `user_data` as a string):

| Provider | Resource | Field |
|---|---|---|
| Hetzner Cloud | `hcloud_server` | `user_data` |
| AWS | `aws_instance` | `user_data` |
| DigitalOcean | `digitalocean_droplet` | `user_data` |
| Vultr | `vultr_instance` | `user_data` |
| OpenStack | `openstack_compute_instance_v2` | `user_data` |
| Proxmox (telmate) | `proxmox_vm_qemu` | `cicustom` (via snippet) |
| Linode | `linode_instance` | `metadata { user_data = base64encode(...) }` |

For providers whose field name differs from `user_data`, see their docs — the string itself is portable.
