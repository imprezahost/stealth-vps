/**
 * Pulumi stack: one stealth-vps host on DigitalOcean.
 *
 * Mirrors `terraform/examples/digitalocean/main.tf`. Same droplet +
 * firewall + ssh key resources, identical cloud-init body via the
 * shared TS builder.
 */

import * as fs from "fs";
import * as path from "path";
import * as pulumi from "@pulumi/pulumi";
import * as digitalocean from "@pulumi/digitalocean";
import { buildCloudInit } from "stealth-vps";

const config = new pulumi.Config();
const doToken = config.requireSecret("doToken");

const serverName = config.get("serverName") ?? "stealth-vps";
const region = config.get("region") ?? "fra1";                  // Frankfurt
const size = config.get("size") ?? "s-1vcpu-1gb";               // ~$6/mo
const image = config.get("image") ?? "debian-12-x64";
const sshPublicKeyPath =
  config.get("sshPublicKeyPath") ?? `${process.env.HOME}/.ssh/id_ed25519.pub`;
const sshPort = config.getNumber("sshPort") ?? 22550;
const stealthVersion = config.get("stealthVersion") ?? "v0.7.4";

const domain = config.get("domain") ?? null;
const letsencryptEmail = config.get("letsencryptEmail") ?? "";
const realityDest = config.get("realityDest") ?? "www.microsoft.com:443";
const hysteriaHopMin = config.getNumber("hysteriaPortHoppingMin") ?? 49152;
const hysteriaHopMax = config.getNumber("hysteriaPortHoppingMax") ?? 65535;

const resolvedPubkeyPath = sshPublicKeyPath.replace(/^~\//, `${process.env.HOME}/`);
const sshPublicKey = fs.readFileSync(path.normalize(resolvedPubkeyPath), "utf8").trim();

const doProvider = new digitalocean.Provider("digitalocean", { token: doToken });

// ----------------------------------------------------------------------------
// SSH key registered in the account.
// ----------------------------------------------------------------------------
const sshKey = new digitalocean.SshKey(
  "admin",
  {
    name: `${serverName}-admin`,
    publicKey: sshPublicKey,
  },
  { provider: doProvider }
);

// ----------------------------------------------------------------------------
// Cloud-init user_data via the shared builder.
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
// The droplet — IPv6 on, monitoring off (we ship our own Prometheus
// exporter via the role).
// ----------------------------------------------------------------------------
const droplet = new digitalocean.Droplet(
  "vps",
  {
    name: serverName,
    region,
    size,
    image,
    sshKeys: [sshKey.fingerprint],
    userData,
    ipv6: true,
    monitoring: false,
    tags: ["stealth-vps", "managed-by-pulumi"],
  },
  { provider: doProvider }
);

// ----------------------------------------------------------------------------
// Cloud firewall — surgical opens for stealth-vps's listening ports.
// DO firewalls are tag-based, so the droplet's "stealth-vps" tag binds
// the rules automatically.
// ----------------------------------------------------------------------------
const firewall = new digitalocean.Firewall(
  "stealth",
  {
    name: `${serverName}-stealth`,
    dropletIds: [droplet.id.apply((id) => parseInt(id))],
    inboundRules: [
      {
        protocol: "tcp",
        portRange: String(sshPort),
        sourceAddresses: ["0.0.0.0/0", "::/0"],
      },
      {
        protocol: "tcp",
        portRange: "443",
        sourceAddresses: ["0.0.0.0/0", "::/0"],
      },
      {
        protocol: "udp",
        portRange: `${hysteriaHopMin}-${hysteriaHopMax}`,
        sourceAddresses: ["0.0.0.0/0", "::/0"],
      },
      ...(domain
        ? [
            {
              protocol: "tcp",
              portRange: "80",
              sourceAddresses: ["0.0.0.0/0", "::/0"],
            },
          ]
        : []),
    ],
    outboundRules: [
      {
        protocol: "tcp",
        portRange: "1-65535",
        destinationAddresses: ["0.0.0.0/0", "::/0"],
      },
      {
        protocol: "udp",
        portRange: "1-65535",
        destinationAddresses: ["0.0.0.0/0", "::/0"],
      },
      {
        protocol: "icmp",
        destinationAddresses: ["0.0.0.0/0", "::/0"],
      },
    ],
  },
  { provider: doProvider }
);

// ----------------------------------------------------------------------------
// Outputs.
// ----------------------------------------------------------------------------
export const ipv4 = droplet.ipv4Address;
export const ipv6 = droplet.ipv6Address;
export const sshCommand = pulumi.interpolate`ssh -p ${sshPort} root@${droplet.ipv4Address}`;
export const credentialsHint = pulumi.interpolate`ssh -p ${sshPort} root@${droplet.ipv4Address} cat /root/stealth-vps-credentials.txt`;
export const bootstrapLogHint = pulumi.interpolate`ssh -p ${sshPort} root@${droplet.ipv4Address} tail -f /var/log/stealth-vps/bootstrap.log`;
export const stealthVersionOut = stealthVersion;
