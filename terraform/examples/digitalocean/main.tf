# End-to-end DigitalOcean example.
#
# What this provisions:
#   * one digitalocean_ssh_key (registers your local pubkey in DO so
#     droplet creation can reference it)
#   * one digitalocean_firewall with surgical opens for the four
#     service ports (SSH non-default, Reality TCP 443, Hysteria2 UDP
#     range, optional HTTP 80 for LE HTTP-01)
#   * one digitalocean_droplet with the stealth-vps cloud-init as user_data
#
# DO firewall vs the AWS / Hetzner equivalents:
#   - Firewall is its own top-level resource (not attached to a VPC) and
#     gets bound to droplets by ID or tag. We bind by ID here — simplest
#     for the one-droplet case.
#   - Inbound rules use port_range as a STRING ("443" or "20000-50000")
#     and source_addresses as a CIDR list. The provider validates the
#     ranges at plan time.
#   - Outbound is fully open by default; we explicitly enumerate it so
#     "what gets out" is visible in the config.
#
# What you still own outside Terraform:
#   * DNS A/AAAA records (when `domain` is set for LE).
#   * Reverse-DNS PTR (DO sets PTR to your droplet name by default —
#     you can change it in the console).
#   * VPC, Spaces, Load Balancers — none of these are needed for a
#     single stealth-vps droplet; add them when you scale.

provider "digitalocean" {
  token = var.do_token
}

resource "digitalocean_ssh_key" "admin" {
  name       = var.ssh_key_name
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

# ----------------------------------------------------------------------------
# The cloud-init builder module — shared with hetzner/ and aws/ examples.
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# The droplet itself.
# ----------------------------------------------------------------------------
resource "digitalocean_droplet" "vps" {
  name       = var.server_name
  region     = var.region
  size       = var.size
  image      = var.image
  ssh_keys   = [digitalocean_ssh_key.admin.id]
  user_data  = module.stealth_vps_bootstrap.cloud_init
  ipv6       = true
  monitoring = var.enable_monitoring
  backups    = var.enable_backups

  tags = [
    "project:stealth-vps",
    "stealth_version:${replace(var.stealth_version, ".", "_")}",
    "managed_by:terraform",
  ]

  # Cloud-init runs once on first boot. Subsequent config flows through
  # ansible-pull over SSH, not droplet replacement.
  lifecycle {
    ignore_changes = [user_data]
  }
}

# ----------------------------------------------------------------------------
# Firewall — surgical opens. Bound to the droplet ID above.
# ----------------------------------------------------------------------------
resource "digitalocean_firewall" "stealth" {
  name        = "${var.server_name}-fw"
  droplet_ids = [digitalocean_droplet.vps.id]

  # --- Inbound ---

  # SSH on the non-default port the hardening role moves to.
  inbound_rule {
    protocol         = "tcp"
    port_range       = tostring(var.ssh_port)
    source_addresses = var.allow_ssh_from
  }

  # Reality (VLESS-Reality) — TCP 443
  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # Hysteria2 port-hop range — UDP
  inbound_rule {
    protocol         = "udp"
    port_range       = "${var.hysteria_udp_range_min}-${var.hysteria_udp_range_max}"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # HTTP — only when LE is in use (HTTP-01 challenge must be reachable
  # from the public internet for acme.sh to complete issuance / renewal).
  dynamic "inbound_rule" {
    for_each = var.domain == null ? [] : [1]
    content {
      protocol         = "tcp"
      port_range       = "80"
      source_addresses = ["0.0.0.0/0", "::/0"]
    }
  }

  # ICMP — used by DO's own health probes and by anyone troubleshooting
  # reachability. Drop if you want maximum invisibility (Reality + Hysteria2
  # don't need ICMP); kept on by default because it's harmless.
  inbound_rule {
    protocol         = "icmp"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # --- Outbound (fully open) ---

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  tags = [
    "project:stealth-vps",
  ]
}
