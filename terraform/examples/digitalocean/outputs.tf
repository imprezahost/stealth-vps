output "ipv4" {
  description = "Public IPv4 of the new droplet. Point your domain's A record here if you set var.domain."
  value       = digitalocean_droplet.vps.ipv4_address
}

output "ipv6" {
  description = "Public IPv6 of the new droplet."
  value       = digitalocean_droplet.vps.ipv6_address
}

output "droplet_id" {
  description = "DO droplet ID. Useful for the DO console URL and for cross-referencing with `doctl compute droplet ...` if you have the CLI installed."
  value       = digitalocean_droplet.vps.id
}

output "ssh_command" {
  description = "Ready-to-paste SSH command. NOTE: only works AFTER cloud-init finishes (~3-5 min for a fresh deploy)."
  value       = "ssh -p ${var.ssh_port} root@${digitalocean_droplet.vps.ipv4_address}"
}

output "credentials_hint" {
  description = "Reminder of where to find panel + connection details on the VPS once bootstrap finishes."
  value       = "ssh -p ${var.ssh_port} root@${digitalocean_droplet.vps.ipv4_address} cat /root/stealth-vps-credentials.txt"
}

output "bootstrap_log_hint" {
  description = "Tail the cloud-init progress while it runs."
  value       = "ssh -p ${var.ssh_port} root@${digitalocean_droplet.vps.ipv4_address} tail -f /var/log/stealth-vps/bootstrap.log"
}

output "console_url" {
  description = "Direct link to the droplet in the DigitalOcean web console — useful for the recovery / serial console if SSH ever locks you out."
  value       = "https://cloud.digitalocean.com/droplets/${digitalocean_droplet.vps.id}"
}

output "stealth_version" {
  description = "Echoed back so you can grep `terraform output` for what's deployed."
  value       = module.stealth_vps_bootstrap.stealth_version
}
