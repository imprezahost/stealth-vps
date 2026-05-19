/**
 * Pulumi stack: one stealth-vps VM on Proxmox VE.
 *
 * Mirrors `terraform/examples/proxmox/main.tf`. Proxmox isn't a cloud
 * provider — it's a hypervisor that clones a template VM and wires up
 * cloud-init via a snippet file. The shape differs from the cloud
 * examples:
 *
 *   - No SSH-key resource — cloud-init's user_data carries the key
 *   - No firewall resource — Proxmox's datacenter / VM firewall is
 *     typically configured via the web UI; out of scope here
 *   - Cloud-init delivered via a "snippet" file written to the
 *     Proxmox node's snippets storage path. This works when Pulumi
 *     runs on the Proxmox node itself OR when the snippets path is
 *     network-mounted on the workstation
 */

import * as fs from "fs";
import * as path from "path";
import * as pulumi from "@pulumi/pulumi";
import * as proxmox from "@muhlba91/pulumi-proxmoxve";
import { buildCloudInit } from "stealth-vps";

const config = new pulumi.Config();

// Provider config — Proxmox API URL + token. Token format is
// `<user>@<realm>!<tokenid>` for `id`, and the random secret for `secret`.
const pmApiUrl = config.require("pmApiUrl");
const pmApiTokenId = config.require("pmApiTokenId");
const pmApiTokenSecret = config.requireSecret("pmApiTokenSecret");
const pmTlsInsecure = config.getBoolean("pmTlsInsecure") ?? false;

const node = config.require("node");                          // Proxmox node name (e.g. "pve1")
const vmid = config.requireNumber("vmid");                    // VM ID (numeric)
const vmName = config.get("vmName") ?? "stealth-vps";
const cloneTemplate = config.require("cloneTemplate");        // template VM name to clone
const storage = config.get("storage") ?? "local-lvm";
const snippetsStorage = config.get("snippetsStorage") ?? "local";  // datastore-id holding snippets
const cores = config.getNumber("cores") ?? 2;
const memory = config.getNumber("memory") ?? 2048;
const bridge = config.get("bridge") ?? "vmbr0";
const ipv4Cidr = config.get("ipv4Cidr") ?? "dhcp";            // or "10.0.0.50/24" with gateway
const ipv4Gateway = config.get("ipv4Gateway");                 // required when ipv4Cidr is static
const ipv6Cidr = config.get("ipv6Cidr") ?? "auto";

const sshPublicKeyPath =
  config.get("sshPublicKeyPath") ?? `${process.env.HOME}/.ssh/id_ed25519.pub`;
const sshPort = config.getNumber("sshPort") ?? 22550;
const stealthVersion = config.get("stealthVersion") ?? "v0.7.4";

const domain = config.get("domain") ?? null;
const letsencryptEmail = config.get("letsencryptEmail") ?? "";
const realityDest = config.get("realityDest") ?? "www.microsoft.com:443";

const resolvedPubkeyPath = sshPublicKeyPath.replace(/^~\//, `${process.env.HOME}/`);
const sshPublicKey = fs.readFileSync(path.normalize(resolvedPubkeyPath), "utf8").trim();

const pxProvider = new proxmox.Provider("proxmox", {
  endpoint: pmApiUrl,
  apiToken: pulumi.interpolate`${pmApiTokenId}=${pmApiTokenSecret}`,
  insecure: pmTlsInsecure,
});

// ----------------------------------------------------------------------------
// Build cloud-init body.
// ----------------------------------------------------------------------------
const userData = buildCloudInit({
  stealthVersion,
  sshPublicKey,
  sshPort,
  domain,
  letsencryptEmail,
  realityDest,
});

// ----------------------------------------------------------------------------
// Upload the snippet to the Proxmox snippets datastore. The provider's
// `download.File` resource handles content-uploads to snippet storage —
// this is the v0.71+ way (older provider versions required a `local_file`
// dance on the workstation).
// ----------------------------------------------------------------------------
const userdataSnippet = new proxmox.storage.File(
  "userdata",
  {
    contentType: "snippets",
    datastoreId: snippetsStorage,
    nodeName: node,
    sourceRaw: {
      data: userData,
      fileName: `stealth-vps-${vmid}-userdata.yaml`,
    },
  },
  { provider: pxProvider }
);

// ----------------------------------------------------------------------------
// The VM — cloned from a Debian 12 cloud-init template, with our snippet
// wired via cicustom.
// ----------------------------------------------------------------------------
const vm = new proxmox.vm.VirtualMachine(
  "vps",
  {
    nodeName: node,
    vmId: vmid,
    name: vmName,
    clone: {
      vmId: parseInt(cloneTemplate),  // template VMs in pulumi-proxmoxve are referenced by ID
      full: true,
    },
    cpu: { cores },
    memory: { dedicated: memory },
    networkDevices: [{ bridge, model: "virtio" }],
    diskInterface: "scsi0",
    disks: [
      {
        datastoreId: storage,
        interface: "scsi0",
        size: 20,
      },
    ],
    initialization: {
      datastoreId: storage,
      ipConfigs: [
        {
          ipv4:
            ipv4Cidr === "dhcp"
              ? { address: "dhcp" }
              : { address: ipv4Cidr, gateway: ipv4Gateway! },
          ipv6: ipv6Cidr === "auto" ? { address: "auto" } : { address: ipv6Cidr },
        },
      ],
      userDataFileId: userdataSnippet.id,
    },
    tags: ["stealth-vps", "managed-by-pulumi"],
  },
  { provider: pxProvider, dependsOn: [userdataSnippet] }
);

// ----------------------------------------------------------------------------
// Outputs. Proxmox doesn't return the VM's runtime IP directly — clients
// reach the VM via whatever DHCP / static assignment the network gives.
// The `ipv4Cidr` config value is what the operator set; the actual lease
// is visible through `pulumi stack output ipv4Hint` after first boot.
// ----------------------------------------------------------------------------
export const vmidOut = vm.vmId;
export const vmNameOut = vm.name;
export const ipv4Hint =
  ipv4Cidr === "dhcp"
    ? "(dhcp — check the Proxmox web UI or your DHCP lease table)"
    : ipv4Cidr.split("/")[0];
export const sshCommand = pulumi.interpolate`ssh -p ${sshPort} root@<vm-ip>  # see ipv4Hint`;
export const stealthVersionOut = stealthVersion;
