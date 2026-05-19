# stealth-vps on Proxmox VE — Pulumi example

Mirrors [`terraform/examples/proxmox/`](../../../terraform/examples/proxmox/).

## Prereqs (one-time on the Proxmox node)

1. **Cloud-init-ready Debian 12 template VM.** The official upstream image works:
   ```bash
   # On the Proxmox node:
   wget https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2
   qm create 9000 --name debian-12-cloudinit-template --memory 2048 --cores 2 \
       --net0 virtio,bridge=vmbr0
   qm importdisk 9000 debian-12-genericcloud-amd64.qcow2 local-lvm
   qm set 9000 --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-9000-disk-0
   qm set 9000 --ide2 local-lvm:cloudinit
   qm set 9000 --boot c --bootdisk scsi0
   qm set 9000 --serial0 socket --vga serial0
   qm template 9000
   ```
2. **API token** for Pulumi: in the Proxmox web UI → Datacenter → Permissions → API Tokens. Note the ID (`root@pam!pulumi`) and the secret.
3. **Snippets storage**: confirm a Proxmox storage is enabled for "snippets" content (`Datacenter → Storage → local → Content`).

## What gets provisioned

- `proxmox.storage.File` — uploads the cloud-init YAML as a snippet to the Proxmox node's snippets datastore
- `proxmox.vm.VirtualMachine` — clones the Debian 12 template, applies CPU/RAM, wires the snippet via cicustom

## Quick start

```bash
cd pulumi/examples/proxmox
npm install
pulumi stack init dev
pulumi config set --secret pmApiTokenSecret <token-secret>
cp Pulumi.dev.yaml.example Pulumi.dev.yaml  # then edit
pulumi up
```

## Config keys

| Key | Default | Notes |
|---|---|---|
| `pmApiUrl` | required | e.g. `https://pve1:8006/api2/json` |
| `pmApiTokenId` | required | `user@realm!tokenname` |
| `pmApiTokenSecret` | required (secret) | The opaque value from the web UI |
| `pmTlsInsecure` | `false` | Set `true` for self-signed Proxmox certs |
| `node` | required | Proxmox node hostname (e.g. `pve1`) |
| `vmid` | required | Numeric ID for the new VM |
| `cloneTemplate` | required | vmid of the template to clone (e.g. `9000`) |
| `storage` | `local-lvm` | Disk datastore |
| `snippetsStorage` | `local` | Datastore that holds snippets |
| `cores` / `memory` | `2` / `2048` | |
| `bridge` | `vmbr0` | Network bridge |
| `ipv4Cidr` | `dhcp` | Or `"10.0.0.50/24"` with `ipv4Gateway` |
| `sshPort` | `22550` | |
| `stealthVersion` | `v0.8.0` | |

## After `pulumi up`

```bash
pulumi stack output ipv4Hint        # static IP if you set one, else "check DHCP"
pulumi stack output vmidOut         # the new VM's id
```

Then `ssh -p 22550 root@<ip>` once the VM is up. Bootstrap takes 5-10 minutes — `tail -f /var/log/stealth-vps/bootstrap.log` over SSH to watch progress.

## Notes

- Proxmox provider is `@muhlba91/pulumi-proxmoxve` (community-maintained, well-tested). No official Pulumi provider exists for Proxmox.
- Firewall is **out of scope** — Proxmox's per-VM / datacenter firewall is configured via the web UI. Apply the same surgical opens (SSH non-default, TCP 443, UDP port-hopping range, optional 80 for LE) there.
- IPv6: `auto` uses SLAAC. For static IPv6, set `ipv6Cidr` to a `2001:...:1/64` form.
