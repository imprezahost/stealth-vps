# Example: Proxmox VE

End-to-end Terraform that provisions one stealth-vps VM on a Proxmox VE hypervisor — typical home-lab / on-prem / colo setup.

## What it creates

| Resource | Why |
|---|---|
| `local_file.userdata` | Writes the rendered cloud-init `user_data` to a snippet file under `<snippets_storage>:/snippets/` on the Proxmox node. The VM picks this up at first boot via `cicustom`. |
| `proxmox_vm_qemu.vps` | Clones a pre-existing Debian 12 cloud-init template. Disk + cloud-init drive auto-created. NIC on a configurable bridge. DHCP. QEMU guest agent enabled so Proxmox can report back IPs and clean shutdowns. `lifecycle { ignore_changes = [cicustom, ciuser, cipassword, disk] }` so cloud-init re-rendering doesn't trigger VM recreation. |

What you **don't** get from this example (different model than the cloud providers):

- **No firewall resource.** Proxmox's datacenter / VM firewall is typically configured once via the web UI; we don't pretend to manage it via Terraform. The bridge the NIC sits on (`vmbr0`, `vmbr1`, etc.) determines reachability.
- **No DNS automation.** Proxmox doesn't host your domain; point your A record at whatever public IP the VM ends up with (port-forwarded from the hypervisor's WAN, NAT, direct on a bridged interface — depends on your network).
- **No automated template creation.** You must pre-create a Debian 12 cloud-init template once per cluster. The `terraform.tfvars.example` ships the 8-command recipe inline; it takes ~5 minutes the first time.

## Prerequisites

1. **Proxmox VE 8.x cluster** (single-node clusters are fine — just use the node's hostname for `target_node`).
2. **A Debian 12 cloud-init template** present on the target node. Recipe in `terraform.tfvars.example`.
3. **API token** with `VM.Allocate`, `VM.Config.*`, `VM.Audit`, `Datastore.AllocateSpace`, `Datastore.Audit` on the target node + storage. Create via Datacenter → Permissions → API Tokens.
4. **`snippets` content type enabled** on the storage you'll use. On a stock Proxmox install, `local` already has it. Verify with `pvesm status` + `pvesm content-list <storage>`.
5. **Terraform with access to the snippets path.** Two options:
   - **Run Terraform on the Proxmox node itself.** Simplest. `local_file` writes directly.
   - **Mount the snippets path on the controller** (NFS / SSHfs from the node). Then `local_file` works the same.
   - **Remote controller without mount.** Swap the `local_file` block in `main.tf` for a `null_resource` + `provisioner "file"` + `connection { type = "ssh", host = pm_host }` — out of scope for the minimal example.

## Quickstart

```bash
export TF_VAR_pm_api_token_secret="$(cat ~/.proxmox-token)"

cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

terraform init
terraform plan
terraform apply

# After apply, the QEMU guest agent reports the DHCP-assigned IPv4 back
# to Proxmox within a minute or two. Get it with:
terraform output default_ipv4

# Or directly on the hypervisor:
qm guest cmd <vmid> network-get-interfaces

# Then SSH:
ssh -p 22550 root@<vm-ipv4>
```

Cloud-init runs ~3-5 minutes after first boot — `apt-get update + upgrade` is the slow part. Tail progress:

```bash
ssh -p 22550 root@<vm-ipv4> tail -f /var/log/stealth-vps/bootstrap.log
```

## Cost guidance

Proxmox VE itself is free (community subscription). Your costs are:

- **Hardware** — depends on what you have. A home lab repurposing an old PC: ~$0/mo electricity + a one-time NVMe upgrade if needed. Colo: $30-100/mo for 1U.
- **Bandwidth** — your ISP / colo provider, not Proxmox.
- **No per-VM fee.** You can run as many stealth-vps VMs as fit in RAM (~250 MB idle each).

This is what makes Proxmox interesting for fleet operators: 5-10 nodes across regions cost about the same as one beefy cloud VM, with full control over routing and no egress meter.

## Network model notes

The example assumes a flat bridge model — `vmbr0` is the LAN-facing bridge, VM gets an IP via DHCP on it. Common variations:

- **NAT'd setup**: the hypervisor port-forwards public 443 + Hysteria2 UDP range to the VM's internal IP. The VM's `cicustom` cloud-init does not change; the operator's router does the forwarding.
- **Direct IPv4 on the WAN bridge**: VM gets a real public IP on `vmbr0` (typical at colos). Cleanest setup.
- **Isolated VLAN / SDN zone**: set `network_bridge` to the VLAN bridge name; you handle inter-VLAN routing elsewhere.

## Multi-VM fleet

`for_each` over a map of `vmid → server_name → target_node`. Each iteration writes its own snippet file + creates its own VM. The shared cloud-init module is reused with per-VM input overrides.

## Tearing it down

```bash
terraform destroy
```

Removes the VM. The snippet file in `<snippets_local_path>/` is also removed by Terraform (it owns the `local_file`). The Debian 12 cloud-init template stays — that's an immutable artefact you reuse across VMs.

## Comparison vs the cloud examples

| Aspect | Cloud (Hetzner / AWS / DO / Vultr) | Proxmox |
|---|---|---|
| Provider abstracts firewall | yes | no — configure once in the web UI |
| Provider abstracts SSH key registry | yes | no — cloud-init `ssh_authorized_keys` is the only path |
| Provisioning time | ~30-60s | ~30s (cloning is fast) + ~3-5min cloud-init |
| Cost model | $/month per instance | hardware + electricity |
| Where the snippet/user_data lives | provider API | filesystem on the hypervisor |
| Public IP discovery | direct from resource attribute | needs QEMU guest agent + `qm guest cmd` |
