# End-to-end Hetzner Cloud example.
#
# What this provisions:
#   * one hcloud_server with the stealth-vps cloud-init user_data
#   * one hcloud_ssh_key registered in your Hetzner project, derived
#     from var.ssh_public_key_path so the same key Terraform reads is
#     the one Hetzner injects on first boot
#
# What you still own outside Terraform:
#   * DNS A/AAAA pointing at the server's IPv4 / IPv6 (if you set
#     var.domain to enable Let's Encrypt)
#   * Backups, snapshots, monitoring (Hetzner offers them as separate
#     resources; out of scope for the minimal example)

provider "hcloud" {
  token = var.hcloud_token
}

resource "hcloud_ssh_key" "admin" {
  name       = "${var.server_name}-admin"
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

module "stealth_vps_bootstrap" {
  source = "../../modules/stealth-vps"

  stealth_version     = var.stealth_version
  ssh_public_key      = trimspace(file(pathexpand(var.ssh_public_key_path)))
  ssh_port            = var.ssh_port
  domain              = var.domain
  letsencrypt_email   = var.letsencrypt_email
  reality_dest        = var.reality_dest
  reality_servernames = var.reality_servernames
}

resource "hcloud_server" "vps" {
  name        = var.server_name
  server_type = var.server_type
  image       = var.image
  location    = var.location
  user_data   = module.stealth_vps_bootstrap.cloud_init
  ssh_keys    = [hcloud_ssh_key.admin.id]

  labels = {
    project          = "stealth-vps"
    stealth_version  = replace(var.stealth_version, ".", "_")  # Hetzner label values cannot contain dots
    managed_by       = "terraform"
  }

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }
}
