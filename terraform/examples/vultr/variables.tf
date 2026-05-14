variable "vultr_api_key" {
  description = "Vultr API key. Generate at https://my.vultr.com/settings/#settingsapi. Prefer setting via TF_VAR_vultr_api_key env var rather than committing to terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Vultr region slug. ewr/lax/mia = USA, lhr/fra/ams = EU, nrt/sgp/sea = Asia, syd = Sydney, gru = São Paulo. Singapore (sgp) usually has the best mainland-CN routing on Vultr's network."
  type        = string
  default     = "lax"
}

variable "server_name" {
  description = "Instance label visible in the Vultr console + cloud-init hostname."
  type        = string
  default     = "stealth-vps"
}

variable "plan" {
  description = "Vultr plan slug. vc2-1c-1gb (US$6/mo) tight; vc2-1c-2gb (US$12) recommended; vc2-2c-2gb (US$18) better CPU. Vultr also offers vhf-* (high-frequency CPU) and voc-* (cloud GPU); vc2 is the standard line we test against. Vultr does not yet ship arm64 plans in 2026 — every plan here is amd64."
  type        = string
  default     = "vc2-1c-2gb"
}

variable "os_id" {
  description = "Vultr OS ID. 477 = Debian 12 x64 (current at the time of writing). Vultr renumbers OS IDs occasionally; check `curl https://api.vultr.com/v2/os | jq '.os[] | select(.name | test(\"Debian 12\"))'` if 477 stops working."
  type        = number
  default     = 477
}

variable "ssh_public_key_path" {
  description = "Local path to your SSH public key."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_key_label" {
  description = "Label for the public key in Vultr's account-wide SSH key registry."
  type        = string
  default     = "stealth-vps"
}

variable "ssh_port" {
  description = "Non-default SSH port the hardening role moves to. Opened in the Vultr firewall group."
  type        = number
  default     = 22550
}

variable "stealth_version" {
  description = "stealth-vps release tag to pin the cloud-init bootstrap to."
  type        = string
  default     = "v0.5.6"
}

variable "domain" {
  description = "Optional DNS A/AAAA pointing at the new instance → enables Let's Encrypt. Set to null to skip. When set, the firewall group also opens TCP 80 for the HTTP-01 challenge."
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
  description = "SNI hostnames the Reality inbound accepts."
  type        = list(string)
  default     = ["www.microsoft.com"]
}

variable "hysteria_udp_range_min" {
  description = "UDP port-hopping range lower bound for Hysteria2."
  type        = number
  default     = 20000
}

variable "hysteria_udp_range_max" {
  description = "UDP port-hopping range upper bound for Hysteria2."
  type        = number
  default     = 50000
}

variable "allow_ssh_from" {
  description = "CIDR blocks allowed to reach the SSH port. Default 0.0.0.0/0 (anywhere); IPv6 0.0.0.0/0 + ::/0 supplied via separate v4/v6 rules in main.tf since Vultr firewall rules are per-IP-family."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_ipv6" {
  description = "Allocate an IPv6 address to the instance."
  type        = bool
  default     = true
}

variable "enable_backups" {
  description = "Enable Vultr's weekly automated backups (~+20% of the plan price). Off by default to keep the example minimal."
  type        = bool
  default     = false
}
