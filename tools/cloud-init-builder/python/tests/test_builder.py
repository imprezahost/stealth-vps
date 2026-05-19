"""Tests for stealth_vps_cloudinit — input validation + render shape.

Byte-parity tests against the TS output live under tests/fixtures/.
Regenerate fixtures from the TS source whenever the template changes
(see the package README).
"""

from __future__ import annotations

import pathlib

import pytest

from stealth_vps_cloudinit import StealthVpsArgs, build_all, build_cloud_init


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_minimum_required_args() -> None:
    """ssh_public_key is the only required field — everything else has a
    sensible default."""
    out = build_cloud_init(
        StealthVpsArgs(ssh_public_key="ssh-ed25519 AAAA test@example.com")
    )
    assert out.startswith("#cloud-config")
    assert "stealth-vps v0.8.0 cloud-init bootstrap finished" in out


def test_invalid_stealth_version_rejected() -> None:
    with pytest.raises(ValueError, match="SemVer tag"):
        build_cloud_init(
            StealthVpsArgs(
                ssh_public_key="ssh-ed25519 AAAA test@example.com",
                stealth_version="0.8.0",  # missing the leading `v`
            )
        )


def test_invalid_ssh_key_rejected() -> None:
    with pytest.raises(ValueError, match="supported key type"):
        build_cloud_init(
            StealthVpsArgs(ssh_public_key="not-a-real-key")
        )


def test_privileged_ssh_port_rejected() -> None:
    with pytest.raises(ValueError, match="non-privileged"):
        build_cloud_init(
            StealthVpsArgs(
                ssh_public_key="ssh-ed25519 AAAA test@example.com",
                ssh_port=22,
            )
        )


def test_invalid_le_email_rejected() -> None:
    with pytest.raises(ValueError, match="name@example.com"):
        build_cloud_init(
            StealthVpsArgs(
                ssh_public_key="ssh-ed25519 AAAA test@example.com",
                letsencrypt_email="not-an-email",
            )
        )


def test_empty_le_email_accepted() -> None:
    # Empty is fine — operator just didn't set it.
    out = build_cloud_init(
        StealthVpsArgs(
            ssh_public_key="ssh-ed25519 AAAA test@example.com",
            letsencrypt_email="",
        )
    )
    assert "tls_email" not in out  # no domain → no email written


# ---------------------------------------------------------------------------
# extra-vars rendering
# ---------------------------------------------------------------------------


def test_domain_adds_tls_email() -> None:
    out = build_cloud_init(
        StealthVpsArgs(
            ssh_public_key="ssh-ed25519 AAAA test@example.com",
            domain="vpn.example.com",
            letsencrypt_email="ops@example.com",
        )
    )
    # `vpn.example.com` has no YAML-meaningful chars → emitted bare.
    assert "stealth_vps_domain: vpn.example.com" in out
    # `ops@example.com` has `@` → JSON-quoted per the same regex as the TS toYaml.
    assert 'stealth_vps_tls_email: "ops@example.com"' in out


def test_extra_role_vars_override_base() -> None:
    """Operator's extra_role_vars wins over the convenience defaults."""
    out = build_cloud_init(
        StealthVpsArgs(
            ssh_public_key="ssh-ed25519 AAAA test@example.com",
            reality_dest="www.cloudflare.com:443",
            extra_role_vars={"stealth_vps_reality_dest": "www.bing.com:443"},
        )
    )
    # Strings containing `:` get JSON-quoted by the YAML emitter.
    assert 'stealth_vps_reality_dest: "www.bing.com:443"' in out
    assert "www.cloudflare.com:443" not in out


def test_build_all_returns_split_outputs() -> None:
    result = build_all(
        StealthVpsArgs(ssh_public_key="ssh-ed25519 AAAA test@example.com")
    )
    assert set(result.keys()) == {"cloud_init", "extra_vars_yaml", "stealth_version"}
    assert result["stealth_version"] == "v0.8.0"
    # extra_vars_yaml is a YAML doc, no #cloud-config header.
    assert not result["extra_vars_yaml"].startswith("#cloud-config")
    assert "stealth_vps_reality_dest" in result["extra_vars_yaml"]


# ---------------------------------------------------------------------------
# Byte parity against the TS output
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (FIXTURES / "default.expected").exists(),
    reason="run `npm run build && node -e ...` in pulumi/stealth-vps to generate fixtures",
)
def test_byte_parity_default_args() -> None:
    """Default invocation produces byte-identical output to the TS source.
    Regenerate the fixture whenever the TS template changes (see README).
    """
    expected = (FIXTURES / "default.expected").read_text(encoding="utf-8")
    actual = build_cloud_init(
        StealthVpsArgs(ssh_public_key="ssh-ed25519 AAAA test@example.com")
    )
    assert actual == expected
