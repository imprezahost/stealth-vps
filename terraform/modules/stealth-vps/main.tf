# stealth-vps cloud-init builder.
#
# This module produces no cloud-side resources. Its single output is a
# rendered cloud-init `user_data` string, derived from the typed inputs
# in variables.tf and the template under templates/.
#
# The caller hands the output to whatever provider creates the actual VPS
# (hcloud_server, aws_instance, digitalocean_droplet, etc.). The module
# stays cloud-agnostic because that's where the value is — a user
# already on AWS doesn't want a module that pulls in the hcloud provider.

locals {
  # Merge the convenience inputs (domain, reality_dest, etc.) with the
  # free-form extra_role_vars escape hatch. Convenience inputs lose to
  # explicit overrides in extra_role_vars — matches how Ansible's
  # `-e @file -e key=val` precedence works.
  base_role_vars = merge(
    {
      stealth_vps_reality_dest        = var.reality_dest
      stealth_vps_reality_servernames = var.reality_servernames
    },
    var.domain == null ? {} : {
      stealth_vps_domain    = var.domain
      stealth_vps_tls_email = var.letsencrypt_email
    },
    {
      stealth_hardening_ssh_port = var.ssh_port
    },
  )

  merged_role_vars = merge(local.base_role_vars, var.extra_role_vars)

  # YAML-encode the merged map for the cloud-init `write_files` content
  # block. yamlencode handles quoting + scalar/list distinctions, which
  # is what we want for an Ansible extra-vars file.
  extra_vars_yaml = yamlencode(local.merged_role_vars)

  cloud_init = templatefile("${path.module}/templates/stealth-vps.cloud-init.tftpl", {
    stealth_version  = var.stealth_version
    repo_url         = var.repo_url
    log_dir          = var.log_dir
    ssh_public_key   = trimspace(var.ssh_public_key)
    extra_vars_yaml  = local.extra_vars_yaml
  })
}
