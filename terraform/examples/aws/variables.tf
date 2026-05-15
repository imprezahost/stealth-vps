variable "aws_region" {
  description = "AWS region to deploy into. Pick one with good China routing if that's your target (us-west-2 for west-coast CN routes; ap-northeast-1 for Tokyo). EU options: eu-central-1 (Frankfurt), eu-west-1 (Ireland)."
  type        = string
  default     = "us-west-2"
}

variable "ssh_public_key_path" {
  description = "Local path to your SSH public key. Same key gets imported into AWS as a key pair AND into cloud-init's authorized_keys."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "key_pair_name" {
  description = "Name to register the public key under in AWS. Visible in the EC2 console; not user-facing."
  type        = string
  default     = "stealth-vps"
}

variable "server_name" {
  description = "EC2 Name tag and cloud-init hostname."
  type        = string
  default     = "stealth-vps"
}

variable "instance_type" {
  description = "EC2 instance type. Default t4g.small (ARM 2v/2GB, ~US$0.0168/hr or US$12/mo in us-west-2) exercises the v0.4.0 arm64 support. amd64 alternatives: t3.small (~US$0.0208/hr), t3.medium (~US$0.0416/hr)."
  type        = string
  default     = "t4g.small"
}

variable "architecture" {
  description = "CPU architecture for the AMI lookup. Must match the instance_type — t4g.* and m6g.* are arm64; t3.* and m5.* are amd64. Set to 'arm64' or 'amd64'."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "amd64"], var.architecture)
    error_message = "architecture must be 'arm64' or 'amd64'."
  }
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GB. The role fits in 8 GB; 10 GB leaves room for logs."
  type        = number
  default     = 10
}

variable "ssh_port" {
  description = "Non-default SSH port the hardening role moves to. Opened in the security group."
  type        = number
  default     = 22550
}

variable "stealth_version" {
  description = "stealth-vps release tag to pin the cloud-init bootstrap to."
  type        = string
  default     = "v0.6.1"
}

variable "domain" {
  description = "Optional DNS A/AAAA pointing at the new server → enables Let's Encrypt. Set to null to skip (self-signed Hysteria2 + HTTP panel). When set, the security group also opens TCP 80 for the HTTP-01 challenge."
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
  description = "UDP port-hopping range lower bound for Hysteria2. Opened in the security group."
  type        = number
  default     = 20000
}

variable "hysteria_udp_range_max" {
  description = "UDP port-hopping range upper bound for Hysteria2."
  type        = number
  default     = 50000
}

variable "allow_ssh_from" {
  description = "CIDR blocks allowed to reach the SSH port. Default 0.0.0.0/0 (anywhere) because the hardening role moves SSH to a non-default port with key-only auth + fail2ban. Lock down further if your operations IPs are stable."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
