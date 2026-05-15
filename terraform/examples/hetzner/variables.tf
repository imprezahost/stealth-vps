variable "hcloud_token" {
  description = "Hetzner Cloud API token. Generate at https://console.hetzner.cloud → Project → Security → API tokens. Prefer setting via TF_VAR_hcloud_token env var rather than committing to terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "server_name" {
  description = "Server hostname in the Hetzner console. Also used in cloud-init's hostname."
  type        = string
  default     = "stealth-vps"
}

variable "server_type" {
  description = "Hetzner instance type. cax11 = ARM 2-vCPU/4GB (cheap, arm64). cx22 = AMD 2-vCPU/4GB (amd64). cax21 = ARM 4-vCPU/8GB."
  type        = string
  default     = "cax11"
}

variable "location" {
  description = "Hetzner datacenter location. fsn1 / nbg1 = Germany, hel1 = Finland, ash = US-east, hil = US-west, sin = Singapore."
  type        = string
  default     = "fsn1"
}

variable "image" {
  description = "OS image. The role supports Debian 12 + Ubuntu 22.04/24.04 on amd64 and arm64."
  type        = string
  default     = "debian-12"
}

variable "ssh_public_key_path" {
  description = "Local path to your SSH public key. The key contents go into Hetzner's project-wide SSH key registry AND into cloud-init's root authorized_keys."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_port" {
  description = "Non-default SSH port the hardening role moves to. Hetzner Cloud has no managed firewall by default; the role's UFW config opens this."
  type        = number
  default     = 22550
}

variable "stealth_version" {
  description = "stealth-vps release tag to pin the cloud-init bootstrap to."
  type        = string
  default     = "v0.6.0"
}

variable "domain" {
  description = "Optional DNS A/AAAA pointing at the new server → enables Let's Encrypt. Set to null to skip (self-signed Hysteria2 + HTTP panel)."
  type        = string
  default     = null
}

variable "letsencrypt_email" {
  description = "Email for the LE registration. Required when domain is set."
  type        = string
  default     = ""
}

variable "reality_dest" {
  description = "Reality dest. host:port, TLS 1.3 + X25519 + HTTP/2, not Cloudflare."
  type        = string
  default     = "www.microsoft.com:443"
}

variable "reality_servernames" {
  description = "SNI hostnames the Reality inbound accepts. Should include reality_dest's bare hostname."
  type        = list(string)
  default     = ["www.microsoft.com"]
}
