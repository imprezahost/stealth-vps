"""Tests for the `s-vps` bash wrapper + the installer.env template it
re-sources on `s-vps update`.

These guard the v0.12.1 fix (#41): the wrapper's `extra_vars` array and
the cli_wrapper.yml installer.env template must both carry
`stealth_vps_onboard_enabled` and `stealth_vps_tls_email` — otherwise the
v0.12.0 onboarding bridge is unreachable via the blessed
`installer.env` + `s-vps update` path, and `s-vps update` on any
domain-configured host fails tasks/tls.yml's email assert.

We can't run bash here, so we assert on the file contents (the same
approach test_onboard.py uses for onboard.js). A drift = a re-introduced
gap shipped to operators.
"""

from __future__ import annotations

import pathlib
import re


def _role_files_root() -> pathlib.Path:
    # tests/python-pkg/ → repo root → ansible/roles/stealth-vps
    here = pathlib.Path(__file__).resolve()
    return here.parents[2] / "ansible" / "roles" / "stealth-vps"


def _s_vps_text() -> str:
    return (_role_files_root() / "files" / "s-vps").read_text(encoding="utf-8")


def _cli_wrapper_text() -> str:
    return (_role_files_root() / "tasks" / "cli_wrapper.yml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# s-vps wrapper extra_vars
# ---------------------------------------------------------------------------


def test_wrapper_extra_vars_passes_onboard_enabled() -> None:
    text = _s_vps_text()
    assert "stealth_vps_onboard_enabled=${STEALTH_ONBOARD_ENABLED" in text, (
        "s-vps update must pass stealth_vps_onboard_enabled — without it "
        "the onboarding bridge can't be enabled via installer.env"
    )


def test_wrapper_extra_vars_passes_tls_email() -> None:
    text = _s_vps_text()
    assert "stealth_vps_tls_email=${STEALTH_TLS_EMAIL" in text, (
        "s-vps update must pass stealth_vps_tls_email — tasks/tls.yml "
        "asserts it whenever a domain is set, so update would fail without it"
    )


def test_wrapper_new_vars_live_inside_the_extra_vars_array() -> None:
    """The two new lines must sit inside the `extra_vars=( ... )` array
    that feeds `ansible-pull -e`, not somewhere inert."""
    text = _s_vps_text()
    m = re.search(r"local extra_vars=\((.*?)\n\s*\)", text, re.DOTALL)
    assert m, "could not locate the extra_vars=( ... ) array in files/s-vps"
    block = m.group(1)
    assert "stealth_vps_onboard_enabled=" in block
    assert "stealth_vps_tls_email=" in block


# ---------------------------------------------------------------------------
# installer.env template (cli_wrapper.yml)
# ---------------------------------------------------------------------------


def test_installer_env_template_persists_onboard_enabled() -> None:
    text = _cli_wrapper_text()
    assert "STEALTH_ONBOARD_ENABLED" in text
    assert "stealth_vps_onboard_enabled" in text


def test_installer_env_template_persists_tls_email() -> None:
    text = _cli_wrapper_text()
    assert "STEALTH_TLS_EMAIL" in text
    assert "stealth_vps_tls_email" in text


def test_installer_env_uses_default_if_unset_form() -> None:
    """The template uses POSIX `: \"${VAR:=...}\"` so an operator env var
    wins over the persisted value. The two new keys must follow suit."""
    text = _cli_wrapper_text()
    assert ': "${STEALTH_ONBOARD_ENABLED:=' in text
    assert ': "${STEALTH_TLS_EMAIL:=' in text


def test_wrapper_and_template_agree_on_new_var_names() -> None:
    """Round-trip guard: every STEALTH_ key the template *writes* for the
    two new vars is exactly the key the wrapper *reads*. Catches a typo
    that would silently drop the value on the next `s-vps update`."""
    wrapper = _s_vps_text()
    template = _cli_wrapper_text()
    for var in ("STEALTH_ONBOARD_ENABLED", "STEALTH_TLS_EMAIL"):
        assert var in wrapper, f"{var} read by wrapper"
        assert var in template, f"{var} written by installer.env template"
