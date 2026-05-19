# stealth-vps cloud-init builder (Go)

Go port of [`pulumi/stealth-vps/src/index.ts`](../../../pulumi/stealth-vps/src/index.ts).

Same `Args` shape, same byte-for-byte cloud-init YAML output. Drop-in for Pulumi-Go, Terraform CDK-for-Go, or any Go IaC layer that creates servers.

## Use

```go
package main

import (
    "fmt"
    "os"
    "strings"

    stealthvps "github.com/imprezahost/stealth-vps/tools/cloud-init-builder/go"
)

func main() {
    keyBytes, _ := os.ReadFile(os.Getenv("HOME") + "/.ssh/id_ed25519.pub")
    cloudInit, err := stealthvps.BuildCloudInit(stealthvps.Args{
        SSHPublicKey:     strings.TrimSpace(string(keyBytes)),
        Domain:           "vpn.example.com",
        LetsEncryptEmail: "ops@example.com",
    })
    if err != nil {
        panic(err)
    }
    fmt.Println(cloudInit)
}
```

Hand `cloudInit` to whatever provider's "create instance" call. The bytes match the TypeScript, Python, and Terraform-tftpl renders given the same inputs.

## Install

```bash
go get github.com/imprezahost/stealth-vps/tools/cloud-init-builder/go@v0.7.4
```

## Tests

```bash
cd tools/cloud-init-builder/go
go test ./...
```

10 cases covering validation + render shape + toYAML quoting rules. Byte-parity fixtures against the TS source live one tier up — generate via the `pulumi/stealth-vps` build output (see the Python port's README for the recipe; same fixture format).

## Why no `gopkg.in/yaml.v3`

The hand-rolled `toYAML` matches the TS source byte-for-byte. yaml.v3's emit has different whitespace and quoting heuristics that would break the byte-parity guarantee with the TS / Python / Terraform paths. ~80 LOC for the subset we use.
