# stealth-vps on AWS EC2 — Pulumi example

End-to-end Pulumi stack that mirrors [`terraform/examples/aws/`](../../../terraform/examples/aws/). Same resources, same cloud-init body (via the shared `pulumi/stealth-vps/src` builder), same operator outputs.

## What gets provisioned

- `aws.ec2.KeyPair` — your local SSH pubkey, registered in the chosen region
- `aws.ec2.SecurityGroup` — surgical opens:
  - SSH on the non-default port (default `22550`)
  - VLESS-Reality on TCP `443`
  - Hysteria2 port-hopping UDP range (default `49152-65535`)
  - HTTP `80` — **only** when `domain` is set, for Let's Encrypt HTTP-01
- `aws.ec2.Instance` — latest Debian 12 AMI (looked up dynamically by the Debian official owner ID `136693071363`), gp3 encrypted root 20 GB, IMDSv2 required, IPv6 assigned

## Quick start

```bash
cd pulumi/examples/aws
npm install
cp Pulumi.dev.yaml.example Pulumi.dev.yaml
# edit Pulumi.dev.yaml — set region, serverName, sshPort; add domain + letsencryptEmail if you want LE
pulumi stack init dev
pulumi up
```

AWS credentials come from the standard chain (`AWS_PROFILE`, `~/.aws/credentials`, or an IAM role on your workstation).

## Config keys

| Key | Default | Notes |
|---|---|---|
| `region` | `eu-central-1` | Any AWS region |
| `architecture` | `amd64` | Or `arm64` — pair with a `t4g.*` instanceType |
| `instanceType` | `t3.micro` | `t3.*` for amd64, `t4g.*` for arm64 |
| `serverName` | `stealth-vps` | Tag + key-pair name prefix |
| `sshPort` | `22550` | Non-default SSH port the hardening role moves to |
| `stealthVersion` | `v0.7.4` | Release tag pinned in cloud-init |
| `domain` | `null` | DNS name; enables Let's Encrypt |
| `letsencryptEmail` | `""` | Required when `domain` is set |
| `realityDest` | `www.microsoft.com:443` | TLS 1.3 + X25519 + HTTP/2 dest |
| `hysteriaPortHoppingMin` / `Max` | `49152` / `65535` | UDP range opened in the security group |

## Outputs

```bash
pulumi stack output sshCommand
pulumi stack output credentialsHint
pulumi stack output bootstrapLogHint
```

## Cost (eu-central-1, outside free tier)

- `t3.micro` ~$7-9/mo
- gp3 20GB root ~$1.60/mo
- Public IPv4: $3.65/mo per ENI (since Feb 2024)
- Egress: 100GB/mo free, then $0.09/GB

Total: **~$12-15/mo** plus traffic. Free tier eligible for the first 12 months on a new account.

## Notes vs the Terraform version

| Concern | Terraform | Pulumi |
|---|---|---|
| State | `terraform.tfstate` or remote | Pulumi Service / S3 / etc. |
| Secrets | `terraform.tfvars` (plaintext) | `pulumi config set --secret` (encrypted) |
| Plan | `terraform plan` | `pulumi preview` |

Both produce **byte-identical** `user_data`. Pick whichever your team already runs.
