variable "do_token" {
  description = "DigitalOcean API token. Generate at https://cloud.digitalocean.com/account/api/tokens with full read+write scope. Prefer setting via TF_VAR_do_token env var rather than committing to terraform.tfvars."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "DigitalOcean region slug. nyc1/nyc3 = NYC, sfo3 = San Francisco, tor1 = Toronto, lon1 = London, fra1 = Frankfurt, ams3 = Amsterdam, sgp1 = Singapore (best for mainland CN), blr1 = Bangalore, syd1 = Sydney."
  type        = string
  default     = "sfo3"
}

variable "server_name" {
  description = "Droplet name visible in the DO console. Also used as the firewall name suffix."
  type        = string
  default     = "stealth-vps"
}

variable "size" {
  description = "Droplet size slug. s-1vcpu-1gb (US$6/mo, comfortable for a single-user setup), s-1vcpu-2gb (US$12, recommended for multi-user), s-2vcpu-2gb (US$18, headroom for bursts). DO does NOT yet ship arm64 droplets in 2026 — every size here is amd64."
  type        = string
  default     = "s-1vcpu-2gb"
}

variable "image" {
  description = "Droplet image slug. debian-12-x64 is the role's primary target."
  type        = string
  default     = "debian-12-x64"
}

variable "ssh_public_key_path" {
  description = "Local path to your SSH public key. Same key gets registered in DO (so the droplet boots with it injected via metadata) AND embedded in cloud-init's authorized_keys."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_key_name" {
  description = "Name to register the public key under in DO's account-wide SSH key registry. Visible in the console."
  type        = string
  default     = "stealth-vps"
}

variable "ssh_port" {
  description = "Non-default SSH port the hardening role moves to. Opened in the DO firewall."
  type        = number
  default     = 22550
}

variable "stealth_version" {
  description = "stealth-vps release tag to pin the cloud-init bootstrap to."
  type        = string
  default     = "v0.6.0"
}

variable "domain" {
  description = "Optional DNS A/AAAA pointing at the new droplet → enables Let's Encrypt. Set to null to skip (self-signed Hysteria2 + HTTP panel). When set, the firewall also opens TCP 80 for the HTTP-01 challenge."
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

variable "hysteria_udp_range_min" {
  description = "UDP port-hopping range lower bound for Hysteria2. Opened in the DO firewall."
  type        = number
  default     = 20000
}

variable "hysteria_udp_range_max" {
  description = "UDP port-hopping range upper bound for Hysteria2."
  type        = number
  default     = 50000
}

variable "allow_ssh_from" {
  description = "CIDR blocks allowed to reach the SSH port. Default 0.0.0.0/0 + ::/0 (anywhere) because the hardening role moves SSH to a non-default port with key-only auth + fail2ban. Lock down further if your operations IPs are stable."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "enable_monitoring" {
  description = "Enable DO's free metrics agent (CPU / memory / disk graphs in the console). Independent of the stealth-vps observability stack — both can run."
  type        = bool
  default     = true
}

variable "enable_backups" {
  description = "Enable DO automated weekly backups (+20% on the droplet price). Off by default to keep the example minimal; recommended for production deploys."
  type        = bool
  default     = false
}
