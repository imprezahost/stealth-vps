output "ipv4" {
  description = "Public IPv4 of the new VPS. Point your domain's A record here if you set var.domain."
  value       = hcloud_server.vps.ipv4_address
}

output "ipv6" {
  description = "Public IPv6 of the new VPS."
  value       = hcloud_server.vps.ipv6_address
}

output "ssh_command" {
  description = "Ready-to-paste SSH command. NOTE: only works AFTER cloud-init finishes (typically 2-4 min for a fresh deploy with all of apt-upgrade + ansible-pull). Tail /var/log/stealth-vps/bootstrap.log via the Hetzner web console first if you want to watch."
  value       = "ssh -p ${var.ssh_port} root@${hcloud_server.vps.ipv4_address}"
}

output "credentials_hint" {
  description = "Reminder of where to find panel + connection details on the VPS once bootstrap finishes."
  value       = "ssh -p ${var.ssh_port} root@${hcloud_server.vps.ipv4_address} cat /root/stealth-vps-credentials.txt"
}

output "bootstrap_log_hint" {
  description = "Tail the cloud-init progress while it runs."
  value       = "ssh -p ${var.ssh_port} root@${hcloud_server.vps.ipv4_address} tail -f /var/log/stealth-vps/bootstrap.log"
}

output "stealth_version" {
  description = "Echoed back so you can grep `terraform output` for what's deployed."
  value       = module.stealth_vps_bootstrap.stealth_version
}
