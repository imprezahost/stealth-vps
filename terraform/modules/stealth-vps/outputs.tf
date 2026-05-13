output "cloud_init" {
  description = "Cloud-init `user_data` string. Pass to your cloud provider's create-server resource (hcloud_server.user_data, aws_instance.user_data, digitalocean_droplet.user_data, etc.). The Ansible bootstrap runs unattended on first boot; check /var/log/stealth-vps/bootstrap.log on the instance for progress."
  value       = local.cloud_init
}

output "extra_vars_yaml" {
  description = "Just the merged Ansible extra-vars YAML block, for inspection or for handing to a different bootstrap mechanism (e.g. an in-house image-baker that runs ansible-pull outside cloud-init)."
  value       = local.extra_vars_yaml
}

output "stealth_version" {
  description = "Echoed back from input — useful for tagging the created resource so you can see at-a-glance which release a given server runs."
  value       = var.stealth_version
}
