output "vmid" {
  description = "Proxmox VM ID. Use with `qm status <vmid>` on the hypervisor or in the web UI."
  value       = proxmox_vm_qemu.vps.vmid
}

output "default_ipv4" {
  description = "IPv4 picked up from DHCP. Empty until the QEMU guest agent reports back (a minute or two after first boot). If empty, check `qm guest cmd <vmid> network-get-interfaces` on the hypervisor."
  value       = proxmox_vm_qemu.vps.default_ipv4_address
}

output "ssh_command_hint" {
  description = "Skeleton SSH command — fill in the actual IP once DHCP assigns one. Use the default_ipv4 output OR `qm guest cmd <vmid> network-get-interfaces` on the hypervisor."
  value       = "ssh -p ${var.ssh_port} root@<vm-ipv4>"
}

output "snippet_path" {
  description = "Where the cloud-init file lives on the Proxmox node. Useful for debugging — if cloud-init never runs, check this file exists and has the expected content."
  value       = local_file.userdata.filename
}

output "stealth_version" {
  description = "Echoed back."
  value       = module.stealth_vps_bootstrap.stealth_version
}
