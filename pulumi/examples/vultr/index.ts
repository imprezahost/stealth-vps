/**
 * Pulumi stack: one stealth-vps host on Vultr.
 *
 * Mirrors `terraform/examples/vultr/main.tf`. Vultr's firewall model
 * is per-rule (vs DO's tag-bound rule list), so this stack creates
 * one firewall group plus N rules — same shape the Terraform example
 * uses.
 */

import * as fs from "fs";
import * as path from "path";
import * as pulumi from "@pulumi/pulumi";
import * as vultr from "@ediri/vultr";
import { buildCloudInit } from "stealth-vps";

const config = new pulumi.Config();
const vultrApiKey = config.requireSecret("vultrApiKey");

const serverName = config.get("serverName") ?? "stealth-vps";
const region = config.get("region") ?? "fra";                   // Frankfurt
const plan = config.get("plan") ?? "vc2-1c-1gb";                // ~$6/mo, amd64 only
const osId = config.getNumber("osId") ?? 477;                   // Debian 12 x64 — see vultr.com/api/#operation/list-os
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

const vultrProvider = new vultr.Provider("vultr", { apiKey: vultrApiKey });

// ----------------------------------------------------------------------------
// SSH key.
// ----------------------------------------------------------------------------
const sshKey = new vultr.SshKey(
  "admin",
  {
    name: `${serverName}-admin`,
    sshKey: sshPublicKey,
  },
  { provider: vultrProvider }
);

// ----------------------------------------------------------------------------
// Firewall group + per-rule openings. Vultr's API is one rule per
// (group, protocol, port, source-family), so we have to write each
// rule out. Two rules per port (v4 + v6).
// ----------------------------------------------------------------------------
const firewallGroup = new vultr.FirewallGroup(
  "stealth",
  { description: `${serverName}-stealth` },
  { provider: vultrProvider }
);

const sources: { suffix: string; ipType: string; subnet: string; subnetSize: number }[] = [
  { suffix: "v4", ipType: "v4", subnet: "0.0.0.0", subnetSize: 0 },
  { suffix: "v6", ipType: "v6", subnet: "::", subnetSize: 0 },
];

for (const src of sources) {
  new vultr.FirewallRule(
    `ssh-${src.suffix}`,
    {
      firewallGroupId: firewallGroup.id,
      protocol: "tcp",
      ipType: src.ipType,
      subnet: src.subnet,
      subnetSize: src.subnetSize,
      port: String(sshPort),
      notes: "SSH (non-default port)",
    },
    { provider: vultrProvider }
  );
  new vultr.FirewallRule(
    `reality-${src.suffix}`,
    {
      firewallGroupId: firewallGroup.id,
      protocol: "tcp",
      ipType: src.ipType,
      subnet: src.subnet,
      subnetSize: src.subnetSize,
      port: "443",
      notes: "VLESS-Reality",
    },
    { provider: vultrProvider }
  );
  new vultr.FirewallRule(
    `hysteria-${src.suffix}`,
    {
      firewallGroupId: firewallGroup.id,
      protocol: "udp",
      ipType: src.ipType,
      subnet: src.subnet,
      subnetSize: src.subnetSize,
      port: `${hysteriaHopMin}:${hysteriaHopMax}`,
      notes: "Hysteria2 port-hopping range",
    },
    { provider: vultrProvider }
  );
  if (domain) {
    new vultr.FirewallRule(
      `le-http-${src.suffix}`,
      {
        firewallGroupId: firewallGroup.id,
        protocol: "tcp",
        ipType: src.ipType,
        subnet: src.subnet,
        subnetSize: src.subnetSize,
        port: "80",
        notes: "HTTP-01 challenge (LE)",
      },
      { provider: vultrProvider }
    );
  }
}

// ----------------------------------------------------------------------------
// Cloud-init.
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
// The instance. Vultr passes user_data base64-encoded.
// ----------------------------------------------------------------------------
const instance = new vultr.Instance(
  "vps",
  {
    label: serverName,
    region,
    plan,
    osId,
    sshKeyIds: [sshKey.id],
    firewallGroupId: firewallGroup.id,
    enableIpv6: true,
    backups: "disabled",
    userData: Buffer.from(userData).toString("base64"),
    tags: ["stealth-vps", "managed-by-pulumi"],
  },
  { provider: vultrProvider }
);

// ----------------------------------------------------------------------------
// Outputs.
// ----------------------------------------------------------------------------
export const ipv4 = instance.mainIp;
export const ipv6 = instance.v6MainIp;
export const sshCommand = pulumi.interpolate`ssh -p ${sshPort} root@${instance.mainIp}`;
export const credentialsHint = pulumi.interpolate`ssh -p ${sshPort} root@${instance.mainIp} cat /root/stealth-vps-credentials.txt`;
export const bootstrapLogHint = pulumi.interpolate`ssh -p ${sshPort} root@${instance.mainIp} tail -f /var/log/stealth-vps/bootstrap.log`;
export const stealthVersionOut = stealthVersion;
