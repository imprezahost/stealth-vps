# Pulumi

> **v0.5.x (alpha) reference.** Pure-TypeScript port of the cloud-init builder shipped in `terraform/modules/stealth-vps/`. One worked example (Hetzner Cloud). Same mechanism as Terraform: typed inputs → cloud-init string → provider's create-server call; the language changes (TypeScript instead of HCL), the role and cloud-init template don't.

## What this is

A reference for users who prefer Pulumi to Terraform. Mechanically:

- `pulumi/stealth-vps/` — a small TypeScript package exporting `buildCloudInit({...})`. Pure function, no cloud-side resources. Same inputs + same output shape as the Terraform module.
- `pulumi/examples/hetzner/` — end-to-end stack: takes the user's config, calls `buildCloudInit()`, hands the rendered string to `hcloud.Server.userData`.

If you want to provision against AWS / DigitalOcean / Vultr / Proxmox, port the example's resource block to the respective Pulumi provider (`@pulumi/aws`, `@pulumi/digitalocean`, `@pulumi/vultr`, `@muhlba91/pulumi-proxmoxve`). The `buildCloudInit()` call is identical across all of them.

## Why Pulumi at all?

The Terraform examples already cover the IaC story. Pulumi's value is **the same workflow expressed in a real programming language** — type-checked at compile time, refactorable in an IDE, with the full TypeScript / Python / Go ecosystem for testing and CI. For users who already standardise on Pulumi internally, the Terraform module isn't an option (mixing the two state files is painful), and a parallel reference is the cleanest answer.

## Layout

```
pulumi/
├── README.md                            # this file
├── stealth-vps/
│   ├── README.md
│   ├── src/index.ts                     # buildCloudInit() — pure TS function
│   ├── package.json
│   └── tsconfig.json
└── examples/
    └── hetzner/
        ├── README.md
        ├── index.ts                     # stack entry point
        ├── Pulumi.yaml
        ├── Pulumi.dev.yaml.example      # rename to Pulumi.dev.yaml + fill in
        ├── package.json
        └── tsconfig.json
```

## Versioning

The Pulumi port tracks the role's release cycle. Pin both together by importing the right package version + setting `stealthVersion` to the matching tag:

```ts
import { buildCloudInit } from "../../stealth-vps";

const userData = buildCloudInit({
  stealthVersion: "v0.5.7",
  sshPublicKey: fs.readFileSync(`${process.env.HOME}/.ssh/id_ed25519.pub`, "utf8"),
  domain: "vpn.example.com",
  letsencryptEmail: "ops@example.com",
});
```

## Limitations at v0.6.1

- TypeScript only. Pulumi has first-class Python / Go / .NET / Java SDKs; the cloud-init builder ports trivially to any of them (it's a string template), but we've shipped one canonical TypeScript version to keep the maintenance surface small. Patches welcome.
- One example (Hetzner). AWS / DigitalOcean / Vultr / Proxmox examples land later — same as Terraform's example tree took several sprints to fill.
- No validation library equivalent to Terraform's `validation { ... }` blocks. The TypeScript function uses runtime `throw new Error(...)` for input validation; for stricter checking, wrap in `zod` or `io-ts` schemas at the call site.
- The Pulumi component is **not yet published to npm**. Examples import via relative path (`../../stealth-vps`). When the project ships a 1.0 npm release, the import becomes `@imprezahost/stealth-vps`.
