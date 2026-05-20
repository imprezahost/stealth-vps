"""Tests for stealth_vps.auto_update — semver parse, decision policy,
GitHub API mocking, and the main() entrypoint's exit codes.

No network: every test that would talk to GitHub patches
`urllib.request.urlopen` with a stub returning a canned JSON body. The
subprocess.run call into `s-vps update` is also patched so we don't
need an actual binary on PATH.
"""

from __future__ import annotations

import io
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import auto_update


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("v0.9.0", auto_update.Version(0, 9, 0)),
        ("0.9.0", auto_update.Version(0, 9, 0)),
        ("v10.0.42", auto_update.Version(10, 0, 42)),
        ("v0.0.1", auto_update.Version(0, 0, 1)),
        ("  v0.9.0  ", auto_update.Version(0, 9, 0)),   # whitespace stripped
    ],
)
def test_parse_version_accepts_valid(text: str, expected: auto_update.Version) -> None:
    assert auto_update.parse_version(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "local",
        "unknown",
        "v0.9",                # missing patch
        "v0.9.0-rc.1",         # pre-release not supported
        "v0.9.0+sha.abc",      # build metadata not supported
        "0.9.0.0",             # 4 components
        "main",                # branch name
    ],
)
def test_parse_version_rejects_invalid(text: str) -> None:
    with pytest.raises(auto_update.AutoUpdateError):
        auto_update.parse_version(text)


# ---------------------------------------------------------------------------
# Version ordering (the dataclass order=True default)
# ---------------------------------------------------------------------------


def test_version_ordering_is_semver() -> None:
    v = auto_update.Version
    assert v(0, 9, 0) < v(0, 9, 1)
    assert v(0, 9, 9) < v(0, 10, 0)
    assert v(0, 9, 9) < v(1, 0, 0)
    # str round-trip
    assert str(v(0, 9, 0)) == "v0.9.0"


# ---------------------------------------------------------------------------
# is_patch_update / is_minor_or_patch_update
# ---------------------------------------------------------------------------


def test_is_patch_update_within_same_minor() -> None:
    v = auto_update.Version
    assert auto_update.is_patch_update(v(0, 9, 0), v(0, 9, 1)) is True
    assert auto_update.is_patch_update(v(0, 9, 0), v(0, 9, 7)) is True
    # Same patch — not "newer"
    assert auto_update.is_patch_update(v(0, 9, 0), v(0, 9, 0)) is False
    # Older patch
    assert auto_update.is_patch_update(v(0, 9, 5), v(0, 9, 1)) is False
    # Minor bump — refused
    assert auto_update.is_patch_update(v(0, 9, 0), v(0, 10, 0)) is False
    # Major bump — refused
    assert auto_update.is_patch_update(v(0, 9, 0), v(1, 0, 0)) is False


def test_is_minor_or_patch_update() -> None:
    v = auto_update.Version
    assert auto_update.is_minor_or_patch_update(v(0, 9, 0), v(0, 9, 1)) is True
    assert auto_update.is_minor_or_patch_update(v(0, 9, 0), v(0, 10, 0)) is True
    # Major bump — refused
    assert auto_update.is_minor_or_patch_update(v(0, 9, 0), v(1, 0, 0)) is False


# ---------------------------------------------------------------------------
# decide — policy enforcement
# ---------------------------------------------------------------------------


def test_decide_patch_only_accepts_patch() -> None:
    d = auto_update.decide(
        auto_update.Version(0, 9, 0), auto_update.Version(0, 9, 1),
        policy="patch-only",
    )
    assert d.should_update is True
    assert "patch update" in d.reason


def test_decide_patch_only_refuses_minor() -> None:
    d = auto_update.decide(
        auto_update.Version(0, 9, 0), auto_update.Version(0, 10, 0),
        policy="patch-only",
    )
    assert d.should_update is False
    assert "refusing minor/major bump" in d.reason


def test_decide_minor_patch_accepts_minor() -> None:
    d = auto_update.decide(
        auto_update.Version(0, 9, 0), auto_update.Version(0, 10, 0),
        policy="minor-patch",
    )
    assert d.should_update is True


def test_decide_minor_patch_refuses_major() -> None:
    d = auto_update.decide(
        auto_update.Version(0, 9, 0), auto_update.Version(1, 0, 0),
        policy="minor-patch",
    )
    assert d.should_update is False
    assert "refusing major bump" in d.reason


def test_decide_disabled_never_updates() -> None:
    d = auto_update.decide(
        auto_update.Version(0, 9, 0), auto_update.Version(0, 9, 1),
        policy="disabled",
    )
    assert d.should_update is False
    assert d.reason == "policy=disabled"


def test_decide_refuses_when_candidate_not_newer() -> None:
    # Equal versions
    d = auto_update.decide(
        auto_update.Version(0, 9, 1), auto_update.Version(0, 9, 1),
        policy="patch-only",
    )
    assert d.should_update is False
    assert "not newer" in d.reason

    # Older candidate (someone rewrote the release pointer to an older tag)
    d = auto_update.decide(
        auto_update.Version(0, 9, 5), auto_update.Version(0, 9, 1),
        policy="patch-only",
    )
    assert d.should_update is False


def test_decide_rejects_unknown_policy() -> None:
    with pytest.raises(auto_update.AutoUpdateError, match="unknown policy"):
        auto_update.decide(
            auto_update.Version(0, 9, 0), auto_update.Version(0, 9, 1),
            policy="aggressive",
        )


# ---------------------------------------------------------------------------
# fetch_latest_release_tag — mocked urlopen
# ---------------------------------------------------------------------------


def _make_urlopen_stub(payload: dict) -> MagicMock:
    """Return a context-manager-compatible mock that yields a file-like
    body whose `json.load()` produces `payload`. Mirrors urllib's
    `urlopen()` return shape."""
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=body)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_fetch_latest_release_tag_returns_tag() -> None:
    stub = _make_urlopen_stub({"tag_name": "v0.9.1", "name": "v0.9.1"})
    with patch("urllib.request.urlopen", return_value=stub):
        assert auto_update.fetch_latest_release_tag() == "v0.9.1"


def test_fetch_latest_release_tag_strips_whitespace() -> None:
    stub = _make_urlopen_stub({"tag_name": "  v0.9.1\n"})
    with patch("urllib.request.urlopen", return_value=stub):
        assert auto_update.fetch_latest_release_tag() == "v0.9.1"


def test_fetch_latest_release_tag_raises_on_missing_field() -> None:
    stub = _make_urlopen_stub({"not_what_we_wanted": "v0.9.1"})
    with patch("urllib.request.urlopen", return_value=stub):
        with pytest.raises(auto_update.AutoUpdateError, match="missing `tag_name`"):
            auto_update.fetch_latest_release_tag()


def test_fetch_latest_release_tag_adds_auth_header_when_token_set() -> None:
    """Operators fronting a fleet behind a single egress IP set
    STEALTH_VPS_GITHUB_TOKEN to escape the 60/hr unauth rate limit.
    The token must reach the request as `Authorization: Bearer ...`."""
    captured: dict = {}
    def _spy(req, timeout):  # type: ignore[no-untyped-def]
        captured["headers"] = dict(req.header_items())
        return _make_urlopen_stub({"tag_name": "v0.9.1"})
    with patch("urllib.request.urlopen", side_effect=_spy):
        auto_update.fetch_latest_release_tag(github_token="ghp_FAKE")
    # urllib stores headers title-cased: "Authorization", "Accept", "User-agent"
    assert captured["headers"].get("Authorization") == "Bearer ghp_FAKE"


# ---------------------------------------------------------------------------
# run_update — mocked subprocess
# ---------------------------------------------------------------------------


def test_run_update_invokes_s_vps(tmp_path: pathlib.Path) -> None:
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    spy = MagicMock(return_value=MagicMock(returncode=0))
    with patch("subprocess.run", spy):
        rc = auto_update.run_update("v0.9.1", s_vps_bin=str(fake_bin))
    assert rc == 0
    args, _ = spy.call_args
    assert args[0] == [str(fake_bin), "update", "v0.9.1"]


def test_run_update_raises_when_s_vps_missing(tmp_path: pathlib.Path) -> None:
    with pytest.raises(auto_update.AutoUpdateError, match="not found"):
        auto_update.run_update("v0.9.1", s_vps_bin=str(tmp_path / "nope"))


def test_run_update_returns_subprocess_exit_code(tmp_path: pathlib.Path) -> None:
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 5\n", encoding="utf-8")
    with patch("subprocess.run", return_value=MagicMock(returncode=5)):
        assert auto_update.run_update("v0.9.1", s_vps_bin=str(fake_bin)) == 5


# ---------------------------------------------------------------------------
# main() — end-to-end with mocked GitHub + subprocess
# ---------------------------------------------------------------------------


def test_main_happy_path_patch_only(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    """Pinned at v0.9.0, GitHub returns v0.9.1, policy default → updates."""
    version_file = tmp_path / "version"
    version_file.write_text("v0.9.0\n", encoding="utf-8")
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    monkeypatch.delenv("STEALTH_VPS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("STEALTH_VPS_AUTO_UPDATE_POLICY", raising=False)

    stub = _make_urlopen_stub({"tag_name": "v0.9.1"})
    with patch("urllib.request.urlopen", return_value=stub), \
         patch("subprocess.run", return_value=MagicMock(returncode=0)) as spy:
        rc = auto_update.main([
            "--version-file", str(version_file),
            "--s-vps-bin", str(fake_bin),
        ])
    assert rc == 0
    args, _ = spy.call_args
    assert args[0] == [str(fake_bin), "update", "v0.9.1"]


def test_main_skips_when_already_latest(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """No update when the pinned tag matches latest. Exit 0, no
    subprocess.run call."""
    version_file = tmp_path / "version"
    version_file.write_text("v0.9.1\n", encoding="utf-8")
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    stub = _make_urlopen_stub({"tag_name": "v0.9.1"})
    spy = MagicMock(return_value=MagicMock(returncode=0))
    with patch("urllib.request.urlopen", return_value=stub), \
         patch("subprocess.run", spy):
        rc = auto_update.main([
            "--version-file", str(version_file),
            "--s-vps-bin", str(fake_bin),
        ])
    assert rc == 0
    spy.assert_not_called()


def test_main_dry_run_does_not_invoke_s_vps(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    version_file = tmp_path / "version"
    version_file.write_text("v0.9.0\n", encoding="utf-8")
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    stub = _make_urlopen_stub({"tag_name": "v0.9.1"})
    spy = MagicMock(return_value=MagicMock(returncode=0))
    with patch("urllib.request.urlopen", return_value=stub), \
         patch("subprocess.run", spy):
        rc = auto_update.main([
            "--version-file", str(version_file),
            "--s-vps-bin", str(fake_bin),
            "--dry-run",
        ])
    assert rc == 0
    spy.assert_not_called()


def test_main_refuses_minor_bump_under_default_policy(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """v0.9.0 pinned, GitHub at v0.10.0, default policy=patch-only:
    refuse. Operator must run `s-vps update v0.10.0` manually."""
    version_file = tmp_path / "version"
    version_file.write_text("v0.9.0\n", encoding="utf-8")
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    stub = _make_urlopen_stub({"tag_name": "v0.10.0"})
    spy = MagicMock(return_value=MagicMock(returncode=0))
    with patch("urllib.request.urlopen", return_value=stub), \
         patch("subprocess.run", spy):
        rc = auto_update.main([
            "--version-file", str(version_file),
            "--s-vps-bin", str(fake_bin),
        ])
    assert rc == 0  # skipping is not a failure
    spy.assert_not_called()


def test_main_exit_two_when_version_file_missing(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    rc = auto_update.main([
        "--version-file", str(tmp_path / "nope"),
        "--s-vps-bin", "/usr/local/bin/s-vps",
    ])
    assert rc == 2


def test_main_exit_two_on_garbled_pin(
    tmp_path: pathlib.Path,
) -> None:
    version_file = tmp_path / "version"
    version_file.write_text("local\n", encoding="utf-8")
    rc = auto_update.main([
        "--version-file", str(version_file),
        "--s-vps-bin", "/usr/local/bin/s-vps",
    ])
    assert rc == 2


def test_main_policy_disabled_short_circuits(
    tmp_path: pathlib.Path,
) -> None:
    """Even with a newer release on GitHub, policy=disabled is a no-op.
    Useful when an operator wants the timer enabled (for the fleet
    convergence story) but is temporarily holding the host on its tag."""
    version_file = tmp_path / "version"
    version_file.write_text("v0.9.0\n", encoding="utf-8")
    fake_bin = tmp_path / "s-vps"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    stub = _make_urlopen_stub({"tag_name": "v0.9.5"})
    spy = MagicMock(return_value=MagicMock(returncode=0))
    with patch("urllib.request.urlopen", return_value=stub), \
         patch("subprocess.run", spy):
        rc = auto_update.main([
            "--version-file", str(version_file),
            "--s-vps-bin", str(fake_bin),
            "--policy", "disabled",
        ])
    assert rc == 0
    spy.assert_not_called()
