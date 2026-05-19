"""stealth_vps_cloudinit — Python port of pulumi/stealth-vps/src/index.ts.

Same inputs (`StealthVpsArgs` named-arg dataclass) → same byte-identical
cloud-init YAML output. Hand it to any Python IaC layer that creates
servers (Pulumi Python, Ansible host_vars, raw boto3 user-data, …).

Tested against fixtures in `tests/test_byte_parity.py` to confirm the
output matches the TypeScript builder byte-for-byte.

Pure stdlib — no jinja2, no PyYAML. Keeps the package dropper-friendly
into any Python environment.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Mapping

__version__ = "0.8.0"
__all__ = [
    "StealthVpsArgs",
    "build_cloud_init",
    "build_all",
]


_DEFAULTS = {
    "stealth_version": "v0.8.0",
    "ssh_port": 22550,
    "reality_dest": "www.microsoft.com:443",
    "reality_servernames": ("www.microsoft.com",),
    "log_dir": "/var/log/stealth-vps",
    "repo_url": "https://github.com/imprezahost/stealth-vps.git",
}

_SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(-[a-z0-9.]+)?$")
_SSH_KEY_PREFIX_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) "
)
_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")


@dataclasses.dataclass
class StealthVpsArgs:
    """Inputs to `build_cloud_init`. Mirrors the TS `StealthVpsArgs`
    interface 1-to-1, with snake_case names. Required fields:
    `ssh_public_key`.
    """

    ssh_public_key: str
    stealth_version: str = "v0.8.0"
    ssh_port: int = 22550
    domain: str | None = None
    letsencrypt_email: str = ""
    reality_dest: str = "www.microsoft.com:443"
    reality_servernames: tuple[str, ...] = ("www.microsoft.com",)
    extra_role_vars: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    log_dir: str = "/var/log/stealth-vps"
    repo_url: str = "https://github.com/imprezahost/stealth-vps.git"


def _validate(args: StealthVpsArgs) -> None:
    if not _SEMVER_TAG_RE.match(args.stealth_version):
        raise ValueError(
            f"stealth_version must be a SemVer tag like 'v0.8.0' or 'v0.8.0-rc.1', "
            f"got: {args.stealth_version!r}"
        )
    if not args.ssh_public_key or not _SSH_KEY_PREFIX_RE.match(args.ssh_public_key):
        raise ValueError(
            "ssh_public_key must start with a supported key type "
            "(ssh-ed25519, ssh-rsa, ecdsa-sha2-*)"
        )
    if not isinstance(args.ssh_port, int) or not (1024 < args.ssh_port < 65536):
        raise ValueError(
            f"ssh_port must be a non-privileged integer port (1024 < n < 65536), "
            f"got: {args.ssh_port!r}"
        )
    if args.letsencrypt_email and not _EMAIL_RE.match(args.letsencrypt_email):
        raise ValueError(
            f"letsencrypt_email must look like name@example.com (or be empty), "
            f"got: {args.letsencrypt_email!r}"
        )


def _to_yaml(value: Any, indent: int = 0) -> str:
    """Minimal YAML serializer. Same algorithm as the TS toYaml, so
    output is byte-identical. Supports str/int/bool/None/list/dict only.
    """
    pad = " " * indent
    if value is None:
        return "null"
    if isinstance(value, bool):
        # bool must be checked BEFORE int — Python's bool is a subclass.
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Quote strings that contain YAML-meaningful chars. Same regex
        # as the TS version: `:#&*!|>'"%@`?-{[],` plus leading/trailing
        # whitespace plus empty string.
        if re.search(r"[:#&*!|>'\"%@`?\-{\[\],]", value) or re.search(r"^\s|\s$", value) or value == "":
            # JSON-style quoting — same as JSON.stringify in TS.
            return _json_quote(value)
        return value
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return "[]"
        return "".join(f"\n{pad}- {_to_yaml(item, indent + 2)}" for item in value)
    if isinstance(value, dict):
        if not value:
            return "{}"
        out = []
        for k, v in value.items():
            rendered = _to_yaml(v, indent + 2)
            if rendered.startswith("\n"):
                out.append(f"\n{pad}{k}:{rendered}")
            else:
                out.append(f"\n{pad}{k}: {rendered}")
        return "".join(out)
    raise TypeError(f"_to_yaml: unsupported value type: {type(value).__name__}")


def _json_quote(s: str) -> str:
    """JSON.stringify equivalent — produces a JSON-string-literal
    representation of `s`. Used by `_to_yaml` to quote strings that
    need it. Strictly ASCII-escaping for non-printable / non-ASCII
    chars so the YAML output is portable.
    """
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _indent_lines(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join((pad + line) if line else line for line in text.split("\n"))


def _build_extra_vars(args: StealthVpsArgs) -> dict[str, Any]:
    """Same merge order as the TS buildExtraVars: defaults + convenience
    inputs first, operator's `extra_role_vars` last (wins on conflict).
    """
    base: dict[str, Any] = {
        "stealth_vps_reality_dest": args.reality_dest,
        "stealth_vps_reality_servernames": list(args.reality_servernames),
        "stealth_hardening_ssh_port": args.ssh_port,
    }
    if args.domain:
        base["stealth_vps_domain"] = args.domain
        base["stealth_vps_tls_email"] = args.letsencrypt_email
    return {**base, **dict(args.extra_role_vars)}


def build_cloud_init(args: StealthVpsArgs) -> str:
    """Render the cloud-init YAML for a stealth-vps host. Output is
    byte-identical to the TS `buildCloudInit` and the Terraform module's
    cloud-init.tftpl render given the same inputs.
    """
    _validate(args)
    extra_vars = _build_extra_vars(args)
    extra_vars_yaml = _to_yaml(extra_vars).lstrip()
    ssh_public_key = args.ssh_public_key.strip()

    return (
        f"#cloud-config\n"
        f"# stealth-vps cloud-init bootstrap (rendered by pulumi/stealth-vps).\n"
        f"#\n"
        f"# Generated for stealth-vps {args.stealth_version}.\n"
        f"# Do not edit on the running VPS — re-render via `pulumi up`.\n"
        f"\n"
        f"package_update: true\n"
        f"package_upgrade: true\n"
        f"\n"
        f"packages:\n"
        f"  - ansible\n"
        f"  - git\n"
        f"  - python3-pip\n"
        f"  - ca-certificates\n"
        f"\n"
        f"ssh_authorized_keys:\n"
        f"  - {ssh_public_key}\n"
        f"\n"
        f"write_files:\n"
        f"  - path: /etc/stealth-vps/extra-vars.yml\n"
        f'    permissions: "0600"\n'
        f"    owner: root:root\n"
        f"    content: |\n"
        f"{_indent_lines(extra_vars_yaml, 6)}\n"
        f"\n"
        f"runcmd:\n"
        f"  - mkdir -p {args.log_dir}\n"
        f"  - |\n"
        f"    ansible-pull \\\n"
        f"      -U {args.repo_url} \\\n"
        f"      -C {args.stealth_version} \\\n"
        f"      -i 'localhost,' \\\n"
        f"      -c local \\\n"
        f'      -e "@/etc/stealth-vps/extra-vars.yml" \\\n'
        f"      ansible/playbooks/site.yml \\\n"
        f"      2>&1 | tee {args.log_dir}/bootstrap.log\n"
        f"\n"
        f"final_message: |\n"
        f"  stealth-vps {args.stealth_version} cloud-init bootstrap finished.\n"
        f"  Logs: {args.log_dir}/bootstrap.log\n"
        f"  Panel and connection details: /root/stealth-vps-credentials.txt\n"
    )


def build_all(args: StealthVpsArgs) -> dict[str, str]:
    """Convenience helper that returns the cloud-init AND the merged
    extra-vars YAML separately — useful for debugging or for feeding a
    non-cloud-init bootstrap mechanism.
    """
    _validate(args)
    extra_vars = _build_extra_vars(args)
    return {
        "cloud_init": build_cloud_init(args),
        "extra_vars_yaml": _to_yaml(extra_vars).lstrip(),
        "stealth_version": args.stealth_version,
    }
