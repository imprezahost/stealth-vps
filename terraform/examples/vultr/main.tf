# End-to-end Vultr example.
#
# What this provisions:
#   * one vultr_ssh_key (account-wide pubkey registration)
#   * one vultr_firewall_group with surgical inbound rules
#   * one vultr_instance with the stealth-vps cloud-init as user_data
#
# Vultr firewall vs the other providers:
#   - vultr_firewall_group is a top-level container; vultr_firewall_rule
#     resources attach to it. Each rule is a single (protocol, port,
#     source_cidr) tuple — no port-list shortcut.
#   - IPv4 and IPv6 rules are SEPARATE resources (ip_type = "v4" or
#     "v6"). We emit both for the public-internet opens; SSH stays
#     v4-only by default (override allow_ssh_from to enable v6).
#   - No outbound firewall — Vultr instances have full egress and the
#     provider doesn't expose an outbound rule resource.

provider "vultr" {
  api_key = var.vultr_api_key
}

resource "vultr_ssh_key" "admin" {
  name    = var.ssh_key_label
  ssh_key = trimspace(file(pathexpand(var.ssh_public_key_path)))
}

# ----------------------------------------------------------------------------
# Firewall group + rules
# ----------------------------------------------------------------------------
resource "vultr_firewall_group" "stealth" {
  description = "${var.server_name} — SSH non-default port, Reality TCP 443, Hysteria2 UDP hop range, optional LE HTTP-01"
}

# SSH — non-default port; each allow_ssh_from CIDR becomes one rule.
resource "vultr_firewall_rule" "ssh" {
  for_each          = toset(var.allow_ssh_from)
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet            = split("/", each.value)[0]
  subnet_size       = tonumber(split("/", each.value)[1])
  port              = tostring(var.ssh_port)
  notes             = "stealth-vps SSH (non-default)"
}

# Reality TCP 443 — v4 + v6
resource "vultr_firewall_rule" "reality_v4" {
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet            = "0.0.0.0"
  subnet_size       = 0
  port              = "443"
  notes             = "stealth-vps Reality (VLESS-Reality) TCP 443"
}

resource "vultr_firewall_rule" "reality_v6" {
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "tcp"
  ip_type           = "v6"
  subnet            = "::"
  subnet_size       = 0
  port              = "443"
  notes             = "stealth-vps Reality (VLESS-Reality) TCP 443"
}

# Hysteria2 UDP hop range — v4 + v6
resource "vultr_firewall_rule" "hysteria_v4" {
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "udp"
  ip_type           = "v4"
  subnet            = "0.0.0.0"
  subnet_size       = 0
  port              = "${var.hysteria_udp_range_min}:${var.hysteria_udp_range_max}"
  notes             = "stealth-vps Hysteria2 UDP port-hop range"
}

resource "vultr_firewall_rule" "hysteria_v6" {
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "udp"
  ip_type           = "v6"
  subnet            = "::"
  subnet_size       = 0
  port              = "${var.hysteria_udp_range_min}:${var.hysteria_udp_range_max}"
  notes             = "stealth-vps Hysteria2 UDP port-hop range"
}

# LE HTTP-01 — only when var.domain is set
resource "vultr_firewall_rule" "le_http_v4" {
  count             = var.domain == null ? 0 : 1
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet            = "0.0.0.0"
  subnet_size       = 0
  port              = "80"
  notes             = "Let's Encrypt HTTP-01 challenge"
}

resource "vultr_firewall_rule" "le_http_v6" {
  count             = var.domain == null ? 0 : 1
  firewall_group_id = vultr_firewall_group.stealth.id
  protocol          = "tcp"
  ip_type           = "v6"
  subnet            = "::"
  subnet_size       = 0
  port              = "80"
  notes             = "Let's Encrypt HTTP-01 challenge"
}

# ----------------------------------------------------------------------------
# The cloud-init builder module — shared with the other examples.
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
# The instance itself.
# ----------------------------------------------------------------------------
resource "vultr_instance" "vps" {
  label             = var.server_name
  hostname          = var.server_name
  region            = var.region
  plan              = var.plan
  os_id             = var.os_id
  ssh_key_ids       = [vultr_ssh_key.admin.id]
  user_data         = module.stealth_vps_bootstrap.cloud_init
  enable_ipv6       = var.enable_ipv6
  backups           = var.enable_backups ? "enabled" : "disabled"
  firewall_group_id = vultr_firewall_group.stealth.id

  tags = [
    "project:stealth-vps",
    "stealth_version:${replace(var.stealth_version, ".", "_")}",
    "managed_by:terraform",
  ]

  # Cloud-init runs once on first boot. Subsequent config flows through
  # ansible-pull over SSH, not instance replacement.
  lifecycle {
    ignore_changes = [user_data]
  }
}
