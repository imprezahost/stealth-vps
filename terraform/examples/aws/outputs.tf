output "public_ip" {
  description = "Public IPv4 of the new instance. Point your domain's A record here if you set var.domain. Note: this changes if you stop+start the instance; allocate an aws_eip yourself if you need a stable IP."
  value       = aws_instance.vps.public_ip
}

output "public_ipv6" {
  description = "First IPv6 address of the instance, if the default VPC's subnet has IPv6 enabled. AWS accounts created after 2022 typically have IPv6 by default; older accounts may need a manual subnet modification."
  value       = try(aws_instance.vps.ipv6_addresses[0], null)
}

output "instance_id" {
  description = "EC2 instance ID. Useful for `aws ec2 get-console-output --instance-id <id>` while debugging cloud-init."
  value       = aws_instance.vps.id
}

output "ssh_command" {
  description = "Ready-to-paste SSH command. NOTE: only works AFTER cloud-init finishes (~3-5 min for a fresh deploy with all of apt-upgrade + ansible-pull). Tail the bootstrap log via aws_ec2 console output if you want to watch."
  value       = "ssh -p ${var.ssh_port} root@${aws_instance.vps.public_ip}"
}

output "credentials_hint" {
  description = "Reminder of where to find panel + connection details on the VPS once bootstrap finishes."
  value       = "ssh -p ${var.ssh_port} root@${aws_instance.vps.public_ip} cat /root/stealth-vps-credentials.txt"
}

output "bootstrap_log_hint" {
  description = "Tail the cloud-init progress while it runs."
  value       = "ssh -p ${var.ssh_port} root@${aws_instance.vps.public_ip} tail -f /var/log/stealth-vps/bootstrap.log"
}

output "ami_id" {
  description = "Resolved AMI used for the instance. Echoed back so you can see which Debian 12 build went out — Debian publishes new AMIs every couple weeks; pinning explicitly later is one option to stabilise."
  value       = data.aws_ami.debian12.id
}

output "stealth_version" {
  description = "Echoed back so you can grep `terraform output` for what's deployed."
  value       = module.stealth_vps_bootstrap.stealth_version
}
