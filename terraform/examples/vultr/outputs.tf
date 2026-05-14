output "ipv4" {
  description = "Public IPv4 of the new instance. Point your domain's A record here if you set var.domain."
  value       = vultr_instance.vps.main_ip
}

output "ipv6" {
  description = "Public IPv6 of the new instance, when enable_ipv6 is true."
  value       = var.enable_ipv6 ? vultr_instance.vps.v6_main_ip : null
}

output "instance_id" {
  description = "Vultr instance ID. Use with `vultr-cli instance get <id>` if you have the CLI installed."
  value       = vultr_instance.vps.id
}

output "ssh_command" {
  description = "Ready-to-paste SSH command. NOTE: only works AFTER cloud-init finishes (~3-5 min for a fresh deploy)."
  value       = "ssh -p ${var.ssh_port} root@${vultr_instance.vps.main_ip}"
}

output "credentials_hint" {
  description = "Where to find panel + connection details once bootstrap finishes."
  value       = "ssh -p ${var.ssh_port} root@${vultr_instance.vps.main_ip} cat /root/stealth-vps-credentials.txt"
}

output "bootstrap_log_hint" {
  description = "Tail the cloud-init progress while it runs."
  value       = "ssh -p ${var.ssh_port} root@${vultr_instance.vps.main_ip} tail -f /var/log/stealth-vps/bootstrap.log"
}

output "console_url" {
  description = "Direct link to the instance in the Vultr web console — useful for the noVNC console if SSH ever locks you out."
  value       = "https://my.vultr.com/subs/?id=${vultr_instance.vps.id}"
}

output "stealth_version" {
  description = "Echoed back so you can grep `terraform output` for what's deployed."
  value       = module.stealth_vps_bootstrap.stealth_version
}
