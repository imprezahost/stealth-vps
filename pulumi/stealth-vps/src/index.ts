/**
 * stealth-vps cloud-init builder — pure-TypeScript port of
 * terraform/modules/stealth-vps/.
 *
 * Mechanically identical to the Terraform module: typed inputs go in,
 * a single cloud-init YAML string comes out. The caller hands the
 * string to whatever Pulumi cloud provider's create-server resource
 * they're using (`hcloud.Server.userData`, `aws.ec2.Instance.userData`,
 * `digitalocean.Droplet.userData`, …). No cloud-side resources are
 * created in this function — it's just a string builder.
 */

/**
 * Inputs to {@link buildCloudInit}. Mirrors variables.tf of the
 * Terraform module 1-to-1.
 */
export interface StealthVpsArgs {
  /**
   * Release tag of stealth-vps to pin the cloud-init bootstrap to.
   * Must match `^v\d+\.\d+\.\d+(-[a-z0-9.]+)?$`. Default: "v0.7.3".
   */
  stealthVersion?: string;

  /**
   * SSH public key line (e.g. "ssh-ed25519 AAAA... user@host").
   * Required. Must start with a supported key type.
   */
  sshPublicKey: string;

  /**
   * Non-default SSH port the hardening role moves to.
   * Must be in (1024, 65536). Default: 22550.
   */
  sshPort?: number;

  /**
   * DNS name whose A/AAAA record points at this VPS — enables
   * Let's Encrypt via acme.sh HTTP-01. Leave undefined / null to
   * keep self-signed Hysteria2 + HTTP panel.
   */
  domain?: string | null;

  /**
   * Email registered with Let's Encrypt. Required when domain is set.
   * Default: "".
   */
  letsencryptEmail?: string;

  /**
   * Reality dest — host:port, TLS 1.3 + X25519 + HTTP/2, not
   * Cloudflare-fronted. Default: "www.microsoft.com:443".
   */
  realityDest?: string;

  /**
   * List of SNI hostnames Reality accepts on its inbound. Must
   * include the bare hostname from realityDest.
   * Default: ["www.microsoft.com"].
   */
  realityServernames?: string[];

  /**
   * Free-form map of additional Ansible role variables to write into
   * /etc/stealth-vps/extra-vars.yml. Override any default from
   * ansible/roles/stealth-vps/defaults/main.yml.
   * Default: {}.
   */
  extraRoleVars?: Record<string, unknown>;

  /**
   * Where ansible-pull stdout/stderr is teed on the VPS during
   * bootstrap. Default: "/var/log/stealth-vps".
   */
  logDir?: string;

  /**
   * Override the stealth-vps Git repo URL — useful when forking
   * or pinning to a mirror.
   * Default: "https://github.com/imprezahost/stealth-vps.git".
   */
  repoUrl?: string;
}

const DEFAULTS = {
  stealthVersion: "v0.7.3",
  sshPort: 22550,
  realityDest: "www.microsoft.com:443",
  realityServernames: ["www.microsoft.com"],
  logDir: "/var/log/stealth-vps",
  repoUrl: "https://github.com/imprezahost/stealth-vps.git",
};

const SEMVER_TAG_RE = /^v\d+\.\d+\.\d+(-[a-z0-9.]+)?$/;
const SSH_KEY_PREFIX_RE = /^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) /;
const EMAIL_RE = /^[^@]+@[^@]+\.[^@]+$/;

function validate(args: StealthVpsArgs): void {
  const version = args.stealthVersion ?? DEFAULTS.stealthVersion;
  if (!SEMVER_TAG_RE.test(version)) {
    throw new Error(
      `stealthVersion must be a SemVer tag like 'v0.7.3' or 'v0.7.3-rc.1', got: ${version}`
    );
  }

  if (!args.sshPublicKey || !SSH_KEY_PREFIX_RE.test(args.sshPublicKey)) {
    throw new Error(
      "sshPublicKey must start with a supported key type (ssh-ed25519, ssh-rsa, ecdsa-sha2-*)"
    );
  }

  const port = args.sshPort ?? DEFAULTS.sshPort;
  if (!Number.isInteger(port) || port <= 1024 || port >= 65536) {
    throw new Error(`sshPort must be a non-privileged integer port (1024 < n < 65536), got: ${port}`);
  }

  if (args.letsencryptEmail && args.letsencryptEmail !== "" && !EMAIL_RE.test(args.letsencryptEmail)) {
    throw new Error(
      `letsencryptEmail must look like name@example.com (or be empty), got: ${args.letsencryptEmail}`
    );
  }
}

/**
 * Render a JavaScript value as YAML. Minimal subset sufficient for
 * the extra-vars file: strings, numbers, booleans, arrays of those,
 * nested maps. Indentation in spaces (default 0 at top level).
 *
 * Not a general-purpose YAML serializer — keeps the dependency footprint
 * at zero (the rest of the package is also stdlib-only-equivalent).
 */
function toYaml(value: unknown, indent: number = 0): string {
  const pad = " ".repeat(indent);
  if (value === null || value === undefined) {
    return "null";
  }
  if (typeof value === "string") {
    // Quote strings that contain YAML-meaningful chars.
    if (/[:#&*!|>'"%@`?\-{[\],]/.test(value) || /^\s|\s$/.test(value) || value === "") {
      return JSON.stringify(value);
    }
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return value.map((item) => `\n${pad}- ${toYaml(item, indent + 2)}`).join("");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.length === 0) return "{}";
    return entries
      .map(([k, v]) => {
        const rendered = toYaml(v, indent + 2);
        // Multi-line values (arrays / nested maps) go on next line indented.
        if (rendered.startsWith("\n")) {
          return `\n${pad}${k}:${rendered}`;
        }
        return `\n${pad}${k}: ${rendered}`;
      })
      .join("");
  }
  throw new Error(`toYaml: unsupported value type: ${typeof value}`);
}

/**
 * Indent every line of `text` by `n` spaces. Matches what the Terraform
 * module's `indent(6, ...)` does inside the tftpl template.
 */
function indentLines(text: string, n: number): string {
  const pad = " ".repeat(n);
  return text
    .split("\n")
    .map((line) => (line.length > 0 ? pad + line : line))
    .join("\n");
}

/**
 * Build the merged Ansible extra-vars map.
 *
 * Mirrors the locals.base_role_vars + extra_role_vars merge in
 * `terraform/modules/stealth-vps/main.tf`. Convenience inputs lose
 * to explicit overrides in `extraRoleVars` — matches how Ansible's
 * `-e @file -e key=val` precedence works.
 */
function buildExtraVars(args: StealthVpsArgs): Record<string, unknown> {
  const base: Record<string, unknown> = {
    stealth_vps_reality_dest: args.realityDest ?? DEFAULTS.realityDest,
    stealth_vps_reality_servernames: args.realityServernames ?? DEFAULTS.realityServernames,
    stealth_hardening_ssh_port: args.sshPort ?? DEFAULTS.sshPort,
  };
  if (args.domain) {
    base.stealth_vps_domain = args.domain;
    base.stealth_vps_tls_email = args.letsencryptEmail ?? "";
  }
  return { ...base, ...(args.extraRoleVars ?? {}) };
}

/**
 * Generate the cloud-init YAML user-data string for a stealth-vps
 * deployment.
 *
 * The output is identical in shape to what
 * `terraform/modules/stealth-vps/templates/stealth-vps.cloud-init.tftpl`
 * renders given the same inputs.
 *
 * @param args inputs — see {@link StealthVpsArgs}
 * @returns cloud-init user_data string ready to hand to a cloud
 *   provider's "create server" resource (`hcloud.Server.userData`,
 *   `aws.ec2.Instance.userData`, …)
 */
export function buildCloudInit(args: StealthVpsArgs): string {
  validate(args);

  const stealthVersion = args.stealthVersion ?? DEFAULTS.stealthVersion;
  const repoUrl = args.repoUrl ?? DEFAULTS.repoUrl;
  const logDir = args.logDir ?? DEFAULTS.logDir;
  const sshPublicKey = args.sshPublicKey.trim();

  const extraVars = buildExtraVars(args);
  const extraVarsYaml = toYaml(extraVars).trimStart();

  // Match the Terraform tftpl shape exactly. Even indentation matters
  // for cloud-init parsing.
  const cloudInit = `#cloud-config
# stealth-vps cloud-init bootstrap (rendered by pulumi/stealth-vps).
#
# Generated for stealth-vps ${stealthVersion}.
# Do not edit on the running VPS — re-render via \`pulumi up\`.

package_update: true
package_upgrade: true

packages:
  - ansible
  - git
  - python3-pip
  - ca-certificates

ssh_authorized_keys:
  - ${sshPublicKey}

write_files:
  - path: /etc/stealth-vps/extra-vars.yml
    permissions: "0600"
    owner: root:root
    content: |
${indentLines(extraVarsYaml, 6)}

runcmd:
  - mkdir -p ${logDir}
  - |
    ansible-pull \\
      -U ${repoUrl} \\
      -C ${stealthVersion} \\
      -i 'localhost,' \\
      -c local \\
      -e "@/etc/stealth-vps/extra-vars.yml" \\
      ansible/playbooks/site.yml \\
      2>&1 | tee ${logDir}/bootstrap.log

final_message: |
  stealth-vps ${stealthVersion} cloud-init bootstrap finished.
  Logs: ${logDir}/bootstrap.log
  Panel and connection details: /root/stealth-vps-credentials.txt
`;

  return cloudInit;
}

/**
 * Helper that also returns the merged extra-vars YAML — useful for
 * debugging or for feeding a non-cloud-init bootstrap mechanism.
 */
export function buildAll(args: StealthVpsArgs): {
  cloudInit: string;
  extraVarsYaml: string;
  stealthVersion: string;
} {
  validate(args);
  const stealthVersion = args.stealthVersion ?? DEFAULTS.stealthVersion;
  const extraVars = buildExtraVars(args);
  return {
    cloudInit: buildCloudInit(args),
    extraVarsYaml: toYaml(extraVars).trimStart(),
    stealthVersion,
  };
}
