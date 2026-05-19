/**
 * Pulumi stack: one stealth-vps host on AWS EC2.
 *
 * Mirrors `terraform/examples/aws/main.tf` shape-for-shape. The
 * cloud-init body comes from the shared TS builder — identical to
 * what the Terraform module emits given the same inputs.
 *
 * What this provisions:
 *   - aws.ec2.KeyPair from your local SSH pubkey
 *   - aws.ec2.SecurityGroup with surgical opens for stealth-vps's
 *     four listening ports (SSH non-default, Reality TCP 443,
 *     Hysteria2 UDP range, optional HTTP 80 for LE HTTP-01)
 *   - aws.ec2.Instance running the latest Debian 12 AMI for the
 *     chosen architecture, with stealth-vps cloud-init as user_data
 *
 * What you still own outside this stack:
 *   - Elastic IP if you need a stable address (instance IP is dynamic
 *     by default — operators wanting Let's Encrypt under a stable
 *     DNS record will want an aws.ec2.Eip allocation)
 *   - Backups, snapshots, monitoring (separate AWS resources)
 */

import * as fs from "fs";
import * as path from "path";
import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";
import { buildCloudInit } from "stealth-vps";

// ----------------------------------------------------------------------------
// Stack config.
// ----------------------------------------------------------------------------
const config = new pulumi.Config();

const region = config.get("region") ?? "eu-central-1";
const architecture = config.get("architecture") ?? "amd64";   // or "arm64"
const instanceType = config.get("instanceType") ?? "t3.micro";
const serverName = config.get("serverName") ?? "stealth-vps";
const sshPublicKeyPath =
  config.get("sshPublicKeyPath") ?? `${process.env.HOME}/.ssh/id_ed25519.pub`;
const sshPort = config.getNumber("sshPort") ?? 22550;
const stealthVersion = config.get("stealthVersion") ?? "v0.8.0";

const domain = config.get("domain") ?? null;
const letsencryptEmail = config.get("letsencryptEmail") ?? "";
const realityDest = config.get("realityDest") ?? "www.microsoft.com:443";
const hysteriaHopMin = config.getNumber("hysteriaPortHoppingMin") ?? 49152;
const hysteriaHopMax = config.getNumber("hysteriaPortHoppingMax") ?? 65535;

// Resolve the SSH pubkey file once.
const resolvedPubkeyPath = sshPublicKeyPath.replace(/^~\//, `${process.env.HOME}/`);
const sshPublicKey = fs.readFileSync(path.normalize(resolvedPubkeyPath), "utf8").trim();

// ----------------------------------------------------------------------------
// AWS provider pinned to the chosen region.
// ----------------------------------------------------------------------------
const awsProvider = new aws.Provider("aws", { region });

// ----------------------------------------------------------------------------
// Latest official Debian 12 AMI for the chosen architecture. Debian's
// owner ID is 136693071363 (verified in AWS Marketplace).
// ----------------------------------------------------------------------------
const debianAmi = aws.ec2.getAmiOutput(
  {
    mostRecent: true,
    owners: ["136693071363"],
    filters: [
      { name: "name", values: [`debian-12-${architecture}-*`] },
      { name: "virtualization-type", values: ["hvm"] },
      { name: "root-device-type", values: ["ebs"] },
    ],
  },
  { provider: awsProvider }
);

// ----------------------------------------------------------------------------
// SSH key pair registered in this region.
// ----------------------------------------------------------------------------
const keyPair = new aws.ec2.KeyPair(
  "admin",
  {
    keyName: `${serverName}-admin`,
    publicKey: sshPublicKey,
  },
  { provider: awsProvider }
);

// ----------------------------------------------------------------------------
// Security group — surgical opens, no 0.0.0.0/0 for the whole thing.
// SSH on the non-default port, Reality TCP, Hysteria2 UDP range,
// and HTTP 80 only when a domain is set (for the LE HTTP-01 challenge).
// ----------------------------------------------------------------------------
const sg = new aws.ec2.SecurityGroup(
  "stealth",
  {
    name: `${serverName}-stealth`,
    description: "stealth-vps inbound openings",
    ingress: [
      {
        protocol: "tcp",
        fromPort: sshPort,
        toPort: sshPort,
        cidrBlocks: ["0.0.0.0/0"],
        ipv6CidrBlocks: ["::/0"],
        description: "SSH (non-default port)",
      },
      {
        protocol: "tcp",
        fromPort: 443,
        toPort: 443,
        cidrBlocks: ["0.0.0.0/0"],
        ipv6CidrBlocks: ["::/0"],
        description: "VLESS-Reality",
      },
      {
        protocol: "udp",
        fromPort: hysteriaHopMin,
        toPort: hysteriaHopMax,
        cidrBlocks: ["0.0.0.0/0"],
        ipv6CidrBlocks: ["::/0"],
        description: "Hysteria2 port-hopping range",
      },
      ...(domain
        ? [
            {
              protocol: "tcp",
              fromPort: 80,
              toPort: 80,
              cidrBlocks: ["0.0.0.0/0"],
              ipv6CidrBlocks: ["::/0"],
              description: "HTTP-01 challenge (LE)",
            },
          ]
        : []),
    ],
    egress: [
      {
        protocol: "-1",
        fromPort: 0,
        toPort: 0,
        cidrBlocks: ["0.0.0.0/0"],
        ipv6CidrBlocks: ["::/0"],
      },
    ],
    tags: {
      project: "stealth-vps",
      managed_by: "pulumi",
    },
  },
  { provider: awsProvider }
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
// The instance — IMDSv2 required, gp3 encrypted root, default VPC.
// ----------------------------------------------------------------------------
const instance = new aws.ec2.Instance(
  "vps",
  {
    ami: debianAmi.id,
    instanceType,
    keyName: keyPair.keyName,
    vpcSecurityGroupIds: [sg.id],
    userData,
    metadataOptions: {
      httpEndpoint: "enabled",
      httpTokens: "required",  // IMDSv2 only
      httpPutResponseHopLimit: 1,
    },
    rootBlockDevice: {
      volumeType: "gp3",
      volumeSize: 20,
      encrypted: true,
      deleteOnTermination: true,
    },
    ipv6AddressCount: 1,
    tags: {
      Name: serverName,
      project: "stealth-vps",
      stealth_version: stealthVersion,
      managed_by: "pulumi",
    },
  },
  { provider: awsProvider }
);

// ----------------------------------------------------------------------------
// Outputs — same shape as the Terraform example's outputs.tf.
// ----------------------------------------------------------------------------
export const ipv4 = instance.publicIp;
export const ipv6 = instance.ipv6Addresses.apply((a) => (a && a.length > 0 ? a[0] : ""));
export const sshCommand = pulumi.interpolate`ssh -p ${sshPort} admin@${instance.publicIp}`;
export const credentialsHint = pulumi.interpolate`ssh -p ${sshPort} admin@${instance.publicIp} sudo cat /root/stealth-vps-credentials.txt`;
export const bootstrapLogHint = pulumi.interpolate`ssh -p ${sshPort} admin@${instance.publicIp} sudo tail -f /var/log/stealth-vps/bootstrap.log`;
export const stealthVersionOut = stealthVersion;
