variable "pm_api_url" {
  description = "Proxmox VE API endpoint URL — typically https://<hypervisor-host>:8006/api2/json. The TLS cert is the host's self-signed by default; see pm_tls_insecure."
  type        = string
}

variable "pm_api_token_id" {
  description = "Proxmox API token ID, format `user@realm!tokenname` (e.g. `terraform@pve!main`). Create in Datacenter → Permissions → API Tokens; assign a role with VM.Allocate / VM.Config.* / VM.Audit on the target node / pool."
  type        = string
}

variable "pm_api_token_secret" {
  description = "Proxmox API token secret (the UUID returned when you create the token; only shown once). Prefer setting via TF_VAR_pm_api_token_secret env var."
  type        = string
  sensitive   = true
}

variable "pm_tls_insecure" {
  description = "Skip TLS verification on the Proxmox API. Defaults to true because most home/lab Proxmox installs use a self-signed cert. Set false when you've installed a real (LE/internal CA) cert on the hypervisor."
  type        = bool
  default     = true
}

variable "target_node" {
  description = "Proxmox node hostname to create the VM on. Run `pvesh get /nodes` on the hypervisor to list options. Single-node clusters use the node's hostname."
  type        = string
  default     = "pve"
}

variable "vmid" {
  description = "Proxmox VM ID (numeric). Must be unique within the cluster. Pick a free slot above 100 — usually 9000-9999 is reserved for templates so 1000-8999 is fair game."
  type        = number
  default     = 7100
}

variable "server_name" {
  description = "VM name shown in the Proxmox UI + cloud-init hostname."
  type        = string
  default     = "stealth-vps"
}

variable "template_name" {
  description = "Name of an existing Debian 12 cloud-init template on the target node. Create one with `qm create + qm importdisk + qm template` from the Debian generic-cloud image; community guides cover this in ~10 commands. Common conventions: `debian-12-cloudinit`, `tmpl-debian-12`."
  type        = string
  default     = "debian-12-cloudinit"
}

variable "snippets_storage" {
  description = "Proxmox storage ID that has the `snippets` content type enabled. Usually `local`. We write the cloud-init user_data file to <storage>:/snippets/ so the VM can mount it. Verify with `pvesm status` + `pvesm content-list <storage>`."
  type        = string
  default     = "local"
}

variable "snippets_local_path" {
  description = "Filesystem path on the Proxmox node corresponding to the snippets storage. For `local` storage this is typically `/var/lib/vz/snippets/`. The example writes the cloud-init file there via a `local_file` resource — works when running Terraform on the Proxmox node itself OR when this path is mounted via NFS / SSHfs on the controller. For remote-controller setups without that mount, either run Terraform from the node, write the file via a `null_resource + remote-exec` instead, or pre-place a static snippet and skip the local_file step."
  type        = string
  default     = "/var/lib/vz/snippets"
}

variable "cores" {
  description = "vCPU count. The role idles at ~2%; 1 core is enough for a single user, 2 for multi-user comfort."
  type        = number
  default     = 2
}

variable "memory_mb" {
  description = "Memory in MB. 1024 MB is the floor (apt-upgrade peaks); 2048 leaves comfortable headroom."
  type        = number
  default     = 2048
}

variable "disk_gb" {
  description = "Boot disk size in GB. 10 GB fits the role + logs comfortably."
  type        = number
  default     = 10
}

variable "disk_storage" {
  description = "Proxmox storage ID for the VM disk. Common: `local-lvm`, `local-zfs`, `ceph-pool-vm`."
  type        = string
  default     = "local-lvm"
}

variable "network_bridge" {
  description = "Linux bridge for the VM's NIC. `vmbr0` is the default on a typical Proxmox install (the management LAN); use `vmbr1` etc. if you have an isolated VLAN."
  type        = string
  default     = "vmbr0"
}

variable "ssh_public_key_path" {
  description = "Local path to your SSH public key."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_port" {
  description = "Non-default SSH port the hardening role moves to."
  type        = number
  default     = 22550
}

variable "stealth_version" {
  description = "stealth-vps release tag to pin the cloud-init bootstrap to."
  type        = string
  default     = "v0.5.8"
}

variable "domain" {
  description = "Optional DNS name pointing at the VM → enables Let's Encrypt. Proxmox doesn't manage your DNS; you point your domain at whatever public IP the VM ends up with (NAT'd, direct, etc.)."
  type        = string
  default     = null
}

variable "letsencrypt_email" {
  description = "Email for the LE registration. Required when domain is set."
  type        = string
  default     = ""
}

variable "reality_dest" {
  description = "Reality dest."
  type        = string
  default     = "www.microsoft.com:443"
}

variable "reality_servernames" {
  description = "SNI hostnames the Reality inbound accepts."
  type        = list(string)
  default     = ["www.microsoft.com"]
}
