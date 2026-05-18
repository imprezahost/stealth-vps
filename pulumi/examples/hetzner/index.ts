/**
 * Pulumi stack: one stealth-vps host on Hetzner Cloud.
 *
 * Mirrors `terraform/examples/hetzner/main.tf` shape-for-shape — same
 * `hcloud_ssh_key` + `hcloud_server` pair, same cloud-init via the
 * shared builder (`stealth-vps/src/index.ts` → `buildCloudInit`).
 */

import * as fs from "fs";
import * as path from "path";
import * as pulumi from "@pulumi/pulumi";
import * as hcloud from "@pulumi/hcloud";
import { buildCloudInit } from "stealth-vps";

// ----------------------------------------------------------------------------
// Stack config — values come from Pulumi.<stack>.yaml or `pulumi config set`.
// Use `--secret` for hcloudToken so it lands encrypted in the state.
// ----------------------------------------------------------------------------
const config = new pulumi.Config();
const hcloudToken = config.requireSecret("hcloudToken");

const serverName = config.get("serverName") ?? "stealth-vps";
const serverType = config.get("serverType") ?? "cax11";        // ARM 2v/4GB
const location = config.get("location") ?? "fsn1";              // Falkenstein
const image = config.get("image") ?? "debian-12";
const sshPublicKeyPath = config.get("sshPublicKeyPath") ?? `${process.env.HOME}/.ssh/id_ed25519.pub`;
const sshPort = config.getNumber("sshPort") ?? 22550;
const stealthVersion = config.get("stealthVersion") ?? "v0.7.2";

const domain = config.get("domain") ?? null;
const letsencryptEmail = config.get("letsencryptEmail") ?? "";
const realityDest = config.get("realityDest") ?? "www.microsoft.com:443";

// Resolve the SSH pubkey file once.
const resolvedPubkeyPath = sshPublicKeyPath.replace(/^~\//, `${process.env.HOME}/`);
const sshPublicKey = fs.readFileSync(path.normalize(resolvedPubkeyPath), "utf8").trim();

// ----------------------------------------------------------------------------
// Configure the Hetzner provider with the token from the stack config.
// ----------------------------------------------------------------------------
const hcloudProvider = new hcloud.Provider("hcloud", {
  token: hcloudToken,
});

// ----------------------------------------------------------------------------
// Register the SSH key in Hetzner so the API call to create the server
// can reference it. Same belt-and-suspenders pattern as the Terraform
// example: cloud-init *also* writes the key to /root/.ssh/authorized_keys.
// ----------------------------------------------------------------------------
const sshKey = new hcloud.SshKey(
  "admin",
  {
    name: `${serverName}-admin`,
    publicKey: sshPublicKey,
  },
  { provider: hcloudProvider }
);

// ----------------------------------------------------------------------------
// Build the cloud-init via the shared TS function — same inputs and
// output as the Terraform module produces.
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
// The server.
// ----------------------------------------------------------------------------
const server = new hcloud.Server(
  "vps",
  {
    name: serverName,
    serverType,
    image,
    location,
    sshKeys: [sshKey.id],
    userData,
    publicNets: [
      {
        ipv4Enabled: true,
        ipv6Enabled: true,
      },
    ],
    labels: {
      project: "stealth-vps",
      stealth_version: stealthVersion.replace(/\./g, "_"),  // Hetzner labels reject dots
      managed_by: "pulumi",
    },
  },
  { provider: hcloudProvider }
);

// ----------------------------------------------------------------------------
// Outputs — same shape as the Terraform example's outputs.tf.
// ----------------------------------------------------------------------------
export const ipv4 = server.ipv4Address;
export const ipv6 = server.ipv6Address;
export const sshCommand = pulumi.interpolate`ssh -p ${sshPort} root@${server.ipv4Address}`;
export const credentialsHint = pulumi.interpolate`ssh -p ${sshPort} root@${server.ipv4Address} cat /root/stealth-vps-credentials.txt`;
export const bootstrapLogHint = pulumi.interpolate`ssh -p ${sshPort} root@${server.ipv4Address} tail -f /var/log/stealth-vps/bootstrap.log`;
export const stealthVersionOut = stealthVersion;
