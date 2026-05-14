# End-to-end AWS example.
#
# What this provisions:
#   * one aws_key_pair (your local pubkey, registered in this region)
#   * one aws_security_group with surgical opens for the four service ports
#     stealth-vps listens on (SSH non-default, Reality TCP 443, Hysteria2
#     UDP range, optionally HTTP 80 for the LE HTTP-01 challenge)
#   * one aws_instance in the default VPC, with the stealth-vps cloud-init
#     as user_data
#   * AMI is looked up dynamically via the official Debian 12 owner so
#     you never paste a region-specific AMI ID into the config
#
# What you still own outside Terraform:
#   * DNS A/AAAA pointing at the instance's public IP (if you set
#     var.domain to enable Let's Encrypt). On AWS the IP is dynamic
#     unless you allocate an aws_eip — that's not done here to keep
#     the example minimal; add `resource "aws_eip"` if you need it.
#   * Backups, snapshots, monitoring — AWS sells them as separate
#     resources; out of scope for the minimal example.

provider "aws" {
  region = var.aws_region
}

# ----------------------------------------------------------------------------
# Look up the latest official Debian 12 AMI for the chosen architecture.
# Debian's owner ID is 136693071363 (verified in the AWS Marketplace docs).
# ----------------------------------------------------------------------------
data "aws_ami" "debian12" {
  most_recent = true
  owners      = ["136693071363"]  # Debian official

  filter {
    name   = "name"
    values = ["debian-12-${var.architecture}-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

# ----------------------------------------------------------------------------
# Default VPC — every AWS account has one per region with public subnets.
# Looking it up dynamically lets the example apply against any account
# without forcing the user to create / pass a VPC ID.
# ----------------------------------------------------------------------------
data "aws_vpc" "default" {
  default = true
}

# ----------------------------------------------------------------------------
# SSH key pair — register the same key the module embeds in cloud-init,
# so AWS's metadata-service injection (if any) and our cloud-init
# `ssh_authorized_keys` block converge on the same key.
# ----------------------------------------------------------------------------
resource "aws_key_pair" "admin" {
  key_name   = var.key_pair_name
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))

  tags = {
    project = "stealth-vps"
  }
}

# ----------------------------------------------------------------------------
# Security group — surgical opens. Closed-by-default with these exceptions:
#   - SSH (non-default port) from var.allow_ssh_from
#   - Reality (TCP 443) from anywhere — that's where clients connect
#   - Hysteria2 (UDP port-hop range) from anywhere — same
#   - HTTP (TCP 80) ONLY when var.domain is set, for LE HTTP-01
#   - Egress fully open (the role needs to reach apt + acme.sh + dest)
# ----------------------------------------------------------------------------
resource "aws_security_group" "stealth" {
  name        = "${var.server_name}-sg"
  description = "stealth-vps: SSH (non-default port), Reality TCP 443, Hysteria2 UDP hop range, optional LE HTTP-01"
  vpc_id      = data.aws_vpc.default.id

  # SSH — non-default port; allow_ssh_from CIDRs only
  ingress {
    description = "stealth-vps SSH (non-default port)"
    from_port   = var.ssh_port
    to_port     = var.ssh_port
    protocol    = "tcp"
    cidr_blocks = var.allow_ssh_from
  }

  # Reality (VLESS-Reality) — TCP 443
  ingress {
    description = "stealth-vps Reality (VLESS-Reality) TCP 443"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  # Hysteria2 port-hop range — UDP
  ingress {
    description      = "stealth-vps Hysteria2 UDP port-hop range"
    from_port        = var.hysteria_udp_range_min
    to_port          = var.hysteria_udp_range_max
    protocol         = "udp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  # HTTP — only when LE is in use (HTTP-01 challenge port 80 must be reachable
  # from the internet for acme.sh to complete issuance / renewal)
  dynamic "ingress" {
    for_each = var.domain == null ? [] : [1]
    content {
      description      = "Let's Encrypt HTTP-01 challenge (TCP 80)"
      from_port        = 80
      to_port          = 80
      protocol         = "tcp"
      cidr_blocks      = ["0.0.0.0/0"]
      ipv6_cidr_blocks = ["::/0"]
    }
  }

  # All egress allowed — the role needs apt + acme.sh + the Reality dest reachable
  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    project = "stealth-vps"
    name    = "${var.server_name}-sg"
  }
}

# ----------------------------------------------------------------------------
# The cloud-init builder module — shared with the Hetzner example.
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
resource "aws_instance" "vps" {
  ami                    = data.aws_ami.debian12.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.admin.key_name
  vpc_security_group_ids = [aws_security_group.stealth.id]
  user_data              = module.stealth_vps_bootstrap.cloud_init

  # IPv6 + public IPv4 — most stealth-vps deployments want both. The
  # default VPC associates a public IPv4 automatically; IPv6 needs an
  # explicit address block on the subnet, which the default VPC has
  # in modern (post-2022) AWS accounts.
  associate_public_ip_address = true

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"  # IMDSv2 only — defence-in-depth
  }

  # user_data changing forces replacement, which would lose state. The
  # stealth-vps cloud-init is meant to run once on first boot; subsequent
  # config changes flow through `ansible-pull -C <new_version>` re-runs
  # by SSHing into the instance, not Terraform replace.
  lifecycle {
    ignore_changes = [user_data]
  }

  tags = {
    project          = "stealth-vps"
    Name             = var.server_name
    stealth_version  = replace(var.stealth_version, ".", "_")
    architecture     = var.architecture
    managed_by       = "terraform"
  }
}
