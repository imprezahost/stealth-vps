# `stealth-vps` — Pulumi cloud-init builder

Pure-TypeScript port of `terraform/modules/stealth-vps/`. Single exported function `buildCloudInit({...})` takes typed inputs and returns a cloud-init `user_data` string. Same shape, same inputs, same output as the Terraform module.

## Usage

```ts
import * as fs from "fs";
import { buildCloudInit } from "../../stealth-vps";

const userData = buildCloudInit({
  stealthVersion: "v0.6.0",
  sshPublicKey: fs.readFileSync(`${process.env.HOME}/.ssh/id_ed25519.pub`, "utf8"),
  sshPort: 22550,
  domain: "vpn.example.com",
  letsencryptEmail: "ops@example.com",
  realityDest: "www.microsoft.com:443",
  extraRoleVars: {
    stealth_vps_hysteria_port_hopping: true,
  },
});

// Then hand `userData` to whatever Pulumi cloud provider you use:
import * as hcloud from "@pulumi/hcloud";

new hcloud.Server("vps", {
  name: "stealth-vps-fsn1",
  serverType: "cax11",
  image: "debian-12",
  location: "fsn1",
  userData,
  // sshKeys: [...], etc.
});
```

## Inputs (`StealthVpsArgs`)

| Field | Type | Default | Required | Description |
|---|---|---|:---:|---|
| `stealthVersion` | string | `"v0.5.7"` | no | Release tag the cloud-init bootstrap pins to. Validated against `^v\d+\.\d+\.\d+(-[a-z0-9.]+)?$`. |
| `sshPublicKey` | string | — | **yes** | Full SSH public-key line. Validated to start with `ssh-ed25519` / `ssh-rsa` / `ecdsa-sha2-*`. |
| `sshPort` | number | `22550` | no | Non-default SSH port the hardening role moves to. `1024 < n < 65536`. |
| `domain` | string \| null | `null` | no | DNS name pointing at this VPS → enables Let's Encrypt. Null keeps self-signed Hysteria2 + HTTP panel. |
| `letsencryptEmail` | string | `""` | no | Required when `domain` is set. Validated email-shape when non-empty. |
| `realityDest` | string | `"www.microsoft.com:443"` | no | Reality `dest` site. |
| `realityServernames` | string[] | `["www.microsoft.com"]` | no | SNI hostnames Reality accepts. |
| `extraRoleVars` | Record<string, unknown> | `{}` | no | Free-form Ansible role variable overrides. |
| `logDir` | string | `"/var/log/stealth-vps"` | no | Where `ansible-pull` stdout/stderr is teed. |
| `repoUrl` | string | `"https://github.com/imprezahost/stealth-vps.git"` | no | Override when forking or using a mirror. |

## Outputs

`buildCloudInit(args)` returns just the cloud-init string.

For richer debugging output, use `buildAll(args)`:

```ts
import { buildAll } from "../../stealth-vps";

const { cloudInit, extraVarsYaml, stealthVersion } = buildAll({...});
console.log(extraVarsYaml);  // just the merged Ansible extra-vars
```

## Validation

Synchronous, throws `Error` on first failure. Compared to the Terraform module's `validation { ... }` blocks:

| Terraform validation | Pulumi function check |
|---|---|
| `stealth_version` SemVer regex | `SEMVER_TAG_RE.test(...)` |
| `ssh_public_key` key-type prefix | `SSH_KEY_PREFIX_RE.test(...)` |
| `ssh_port` ∈ (1024, 65536) | numeric range check |
| `letsencrypt_email` email-shape when non-empty | `EMAIL_RE.test(...)` when set |

For tighter checking (zod / io-ts), wrap the function at the call site:

```ts
import { z } from "zod";

const ArgsSchema = z.object({
  stealthVersion: z.string().regex(/^v\d+\.\d+\.\d+/),
  sshPublicKey: z.string().min(80),
  domain: z.string().nullable().optional(),
  // ...
});

const args = ArgsSchema.parse(rawInput);
const userData = buildCloudInit(args);
```

## YAML output

The function builds the cloud-init YAML inline via a small purpose-built serializer (≈40 lines) — no `js-yaml` dependency. The serializer covers the subset stealth-vps needs (strings, numbers, booleans, arrays, nested maps) and quotes any string that contains YAML-significant characters. Run `buildAll(args).extraVarsYaml` if you want to inspect the merged extra-vars without the surrounding cloud-init.

## Compatibility with the Terraform module

Output is byte-equivalent (modulo trailing whitespace) to what `terraform/modules/stealth-vps/templates/stealth-vps.cloud-init.tftpl` renders given the same inputs. The role parses both identically.

## Not yet published to npm

Examples import via relative path (`../../stealth-vps`). When the project ships a 1.0 npm release, the import becomes `@imprezahost/stealth-vps`. Until then, vendoring the directory into your Pulumi project (or using a `link:` dependency) is the recommended approach.

## Compile / typecheck locally

```bash
cd pulumi/stealth-vps
npm install
npx tsc --noEmit          # type-check only
npx tsc                    # emit declarations + JS to dist/
```
