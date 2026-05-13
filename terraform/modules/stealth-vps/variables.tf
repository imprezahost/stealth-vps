variable "stealth_version" {
  description = "Release tag of stealth-vps to pin the cloud-init bootstrap to. Must be a tag that exists on https://github.com/imprezahost/stealth-vps."
  type        = string
  default     = "v0.4.2"

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+(-[a-z0-9.]+)?$", var.stealth_version))
    error_message = "stealth_version must be a SemVer tag like 'v0.4.2' or 'v0.5.0-rc.1'."
  }
}

variable "ssh_public_key" {
  description = "SSH public key (full line, e.g. 'ssh-ed25519 AAAA... user@host') to inject for root login. The stealth-hardening role then disables password auth and moves SSH to ssh_port."
  type        = string

  validation {
    condition     = length(regexall("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) ", var.ssh_public_key)) > 0
    error_message = "ssh_public_key must start with a supported key type (ssh-ed25519, ssh-rsa, ecdsa-sha2-*)."
  }
}

variable "ssh_port" {
  description = "Non-default SSH port the hardening role will move to. Match stealth_hardening_ssh_port (default 22550)."
  type        = number
  default     = 22550

  validation {
    condition     = var.ssh_port > 1024 && var.ssh_port < 65536
    error_message = "ssh_port must be a non-privileged port (1024 < n < 65536)."
  }
}

variable "domain" {
  description = "DNS name whose A/AAAA record will point at this VPS — enables Let's Encrypt cert issuance for Hysteria2 + 3X-UI panel. Set null to keep self-signed Hysteria2 + HTTP panel (the v0.1.0 baseline)."
  type        = string
  default     = null
}

variable "letsencrypt_email" {
  description = "Email registered with Let's Encrypt for expiry notices. Required when domain is set."
  type        = string
  default     = ""

  validation {
    condition     = var.letsencrypt_email == "" || can(regex("^[^@]+@[^@]+\\.[^@]+$", var.letsencrypt_email))
    error_message = "letsencrypt_email must look like name@example.com (or be empty when domain is null)."
  }
}

variable "reality_dest" {
  description = "Real-internet site whose TLS handshake Reality borrows. host:port, TLS 1.3 + X25519 + HTTP/2, not Cloudflare-fronted. www.microsoft.com:443 is the default the role validates against."
  type        = string
  default     = "www.microsoft.com:443"
}

variable "reality_servernames" {
  description = "List of SNI hostnames Reality accepts on its inbound. Must include the bare hostname from reality_dest."
  type        = list(string)
  default     = ["www.microsoft.com"]
}

variable "extra_role_vars" {
  description = "Free-form map of additional Ansible role variables to write into /etc/stealth-vps/extra-vars.yml. Override any default from ansible/roles/stealth-vps/defaults/main.yml or stealth-hardening's defaults — e.g. {stealth_vps_hysteria_port_hopping = true, stealth_hardening_ufw_extra_ports = [\"80/tcp\"]}."
  type        = map(any)
  default     = {}
}

variable "log_dir" {
  description = "Where ansible-pull stdout/stderr is teed to on the VPS during bootstrap."
  type        = string
  default     = "/var/log/stealth-vps"
}

variable "repo_url" {
  description = "Override the stealth-vps Git repo URL — useful when forking or pinning to a mirror. The cloud-init bootstrap runs `ansible-pull -U <this>`."
  type        = string
  default     = "https://github.com/imprezahost/stealth-vps.git"
}
