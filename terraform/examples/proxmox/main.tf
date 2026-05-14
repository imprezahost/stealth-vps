# End-to-end Proxmox VE example.
#
# Proxmox doesn't have a managed firewall service / object-storage /
# snapshot-as-a-service like a cloud provider — it's a hypervisor that
# clones a template VM with cloud-init wired up. So the resource shape
# differs from the cloud examples:
#
#   * No "ssh_key" resource — cloud-init's user_data carries the key
#   * No "firewall" resource — Proxmox has its own datacenter/VM firewall
#     that you typically configure once via the web UI; we don't pretend
#     to manage it via Terraform here. The VM's host network bridge
#     (vmbr0 etc.) decides reachability.
#   * Cloud-init delivered via a "snippet" file that the VM mounts
#     through `cicustom`. We write the file with a local_file resource;
#     this works when Terraform runs on the Proxmox node itself OR when
#     the snippets path is mounted (NFS / SSHfs) on the controller.
#     For remote controllers without that mount, see the README for the
#     null_resource + remote-exec alternative.

provider "proxmox" {
  pm_api_url          = var.pm_api_url
  pm_api_token_id     = var.pm_api_token_id
  pm_api_token_secret = var.pm_api_token_secret
  pm_tls_insecure     = var.pm_tls_insecure
}

# ----------------------------------------------------------------------------
# Cloud-init builder — shared module.
# ----------------------------------------------------------------------------
module "stealth_vps_bootstrap" {
  source = "../../modules/stealth-vps"

  stealth_version     = var.stealth_version
  ssh_public_key      = trimspace(file(pathexpand(var.ssh_public_key_path)))
  ssh_port            = var.ssh_port
  domain              = var.domain
  letsencrypt_email   = var.letsencrypt_email
  reality_dest        = var.reality_dest
  reality_servernames = var.reality_servernames
}

# ----------------------------------------------------------------------------
# Write the rendered cloud-init to a snippet file on the Proxmox node.
#
# The file must be present at <snippets_local_path>/<filename> before the VM
# boots. local_file works when Terraform runs on the node itself (most
# home-lab setups), OR when <snippets_local_path> is NFS-mounted on the
# controller. For remote-controller setups without that mount, replace
# this block with a null_resource + provisioner "file" + connection {host
# = pm_api_host}.
# ----------------------------------------------------------------------------
resource "local_file" "userdata" {
  content  = module.stealth_vps_bootstrap.cloud_init
  filename = "${var.snippets_local_path}/stealth-vps-${var.vmid}-userdata.yaml"

  file_permission      = "0644"
  directory_permission = "0755"
}

# ----------------------------------------------------------------------------
# The VM itself — cloned from a pre-existing Debian 12 cloud-init template.
# ----------------------------------------------------------------------------
resource "proxmox_vm_qemu" "vps" {
  name        = var.server_name
  target_node = var.target_node
  vmid        = var.vmid
  clone       = var.template_name
  full_clone  = true

  cores   = var.cores
  sockets = 1
  memory  = var.memory_mb

  # Boot disk (cloud-init template comes with a sized image; we resize
  # the cloned disk to var.disk_gb)
  disk {
    type    = "disk"
    storage = var.disk_storage
    size    = "${var.disk_gb}G"
    slot    = "scsi0"
    iothread = true
    discard  = true
  }

  # Cloud-init drive — Proxmox auto-creates this when cloning a
  # cloud-init template; we just reference the slot.
  disk {
    type    = "cloudinit"
    storage = var.disk_storage
    slot    = "ide2"
  }

  network {
    id     = 0
    model  = "virtio"
    bridge = var.network_bridge
  }

  # Cloud-init config:
  #   cicustom: points at our snippet file (the rendered user_data)
  #   ipconfig0 = "ip=dhcp" — VM picks up IP via DHCP on the bridge
  #   nameserver / searchdomain: skipped, cloud-init defaults are fine
  cicustom = "user=${var.snippets_storage}:snippets/${basename(local_file.userdata.filename)}"

  ipconfig0 = "ip=dhcp,ip6=auto"

  agent     = 1   # enable QEMU guest agent — Debian cloud images ship it
  onboot    = true
  os_type   = "cloud-init"
  scsihw    = "virtio-scsi-single"

  # cloud-init re-render mustn't recreate the VM — config post-first-boot
  # flows through ansible-pull over SSH.
  lifecycle {
    ignore_changes = [
      cicustom,
      ciuser,
      cipassword,
      disk,
    ]
  }

  tags = "stealth-vps;stealth_version-${replace(var.stealth_version, ".", "_")};managed_by-terraform"

  depends_on = [local_file.userdata]
}
