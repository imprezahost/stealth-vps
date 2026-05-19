# stealth_vps_cloudinit (Python)

Python port of [`pulumi/stealth-vps/src/index.ts`](../../../pulumi/stealth-vps/src/index.ts).

Same `StealthVpsArgs` shape (snake_case), same byte-for-byte cloud-init output. Drop-in for Pulumi-Python, Ansible host_vars, raw boto3 user_data, or any other Python IaC layer that creates servers.

## Use

```python
from stealth_vps_cloudinit import StealthVpsArgs, build_cloud_init

with open("/home/me/.ssh/id_ed25519.pub") as f:
    key = f.read().strip()

cloud_init = build_cloud_init(StealthVpsArgs(
    ssh_public_key=key,
    domain="vpn.example.com",
    letsencrypt_email="ops@example.com",
    stealth_version="v0.7.4",
))
```

Hand the resulting string to any cloud provider's "create instance" call's `user_data` field. The bytes are identical to what:

- `pulumi/stealth-vps/src/index.ts → buildCloudInit` produces (TypeScript)
- `terraform/modules/stealth-vps/templates/stealth-vps.cloud-init.tftpl` renders (Terraform)
- `tools/cloud-init-builder/go` produces (Go port)

… given the same inputs.

## Install

From the repo (editable):

```bash
cd tools/cloud-init-builder/python
pip install -e .
```

Or via `pip install -e git+https://github.com/imprezahost/stealth-vps.git@v0.7.4#egg=stealth-vps-cloudinit&subdirectory=tools/cloud-init-builder/python`.

PyPI publishing comes with v0.9.0.

## Tests

```bash
cd tools/cloud-init-builder/python
pip install -e .
python -m pytest tests/
```

Tests assert byte-parity against fixed-input fixtures. If the TS source changes, fixtures must be regenerated:

```bash
cd pulumi/stealth-vps
npm run build
node -e "console.log(require('./bin').buildCloudInit({sshPublicKey:'ssh-ed25519 AAAA test@example.com',stealthVersion:'v0.7.4'}))" \
  > ../../tools/cloud-init-builder/python/tests/fixtures/default.expected
```

## Why no PyYAML

The package is stdlib-only by design (zero pip deps for the production code path). The YAML serializer is hand-rolled to match the TS `toYaml` byte-for-byte — anything PyYAML emits will differ in whitespace / quoting heuristics and break the byte-parity guarantee. ~30 LOC for the subset we actually use.
