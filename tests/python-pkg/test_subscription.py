"""Tests for stealth_vps.subscription — base64 rendering + atomic writes."""

from __future__ import annotations

import base64
import os
import pathlib

import pytest

from stealth_vps import subscription


# ---------------------------------------------------------------------------
# render_subscription_txt
# ---------------------------------------------------------------------------


def test_render_subscription_txt_base64_encodes_joined_uris() -> None:
    uris = [
        "vless://uuid1@host:443?type=tcp#alice",
        "hysteria2://pw@host:443/?sni=cn#alice",
    ]
    body = subscription.render_subscription_txt(uris)
    # Strip trailing newline before decoding.
    decoded = base64.b64decode(body.strip()).decode("utf-8")
    assert decoded.splitlines() == uris


def test_render_subscription_txt_empty_returns_empty() -> None:
    """Edge case: no URIs → empty body. The clients don't crash on empty subs."""
    assert subscription.render_subscription_txt([]) == ""


def test_render_subscription_txt_strips_whitespace_only_uris() -> None:
    """Trailing newlines inside the input get normalised by `joined.strip()`."""
    body = subscription.render_subscription_txt(["\n  \n"])
    assert body == ""


def test_render_subscription_txt_ends_with_newline_when_nonempty() -> None:
    body = subscription.render_subscription_txt(["vless://x@y:1#z"])
    assert body.endswith("\n")


# ---------------------------------------------------------------------------
# write_subscription_file — atomicity + safety
# ---------------------------------------------------------------------------


def test_write_subscription_file_creates_token_dot_txt(subscriptions_dir: str) -> None:
    path = subscription.write_subscription_file(
        "abc123",
        ["vless://uuid@host:443#alice"],
        dir=subscriptions_dir,
    )
    assert os.path.basename(path) == "abc123.txt"
    assert os.path.dirname(path) == subscriptions_dir
    assert os.path.exists(path)


def test_write_subscription_file_content_is_base64_of_uris(subscriptions_dir: str) -> None:
    uris = ["vless://u@h:443#a", "hysteria2://pw@h:443/?#a"]
    path = subscription.write_subscription_file("tok", uris, dir=subscriptions_dir)
    body = pathlib.Path(path).read_text(encoding="utf-8")
    decoded = base64.b64decode(body.strip()).decode("utf-8")
    assert decoded.splitlines() == uris


def test_write_subscription_file_overwrites_existing(subscriptions_dir: str) -> None:
    """Re-issuing a sub for the same token should replace the old content,
    not append. Atomic rename guarantees the swap."""
    subscription.write_subscription_file("tok", ["vless://old@h:1#a"], dir=subscriptions_dir)
    path = subscription.write_subscription_file("tok", ["vless://new@h:2#a"], dir=subscriptions_dir)
    body = pathlib.Path(path).read_text(encoding="utf-8")
    decoded = base64.b64decode(body.strip()).decode("utf-8")
    assert decoded == "vless://new@h:2#a"


def test_write_subscription_file_leaves_no_tmpfile(subscriptions_dir: str) -> None:
    """The atomic-rename path creates a .tmp sibling — after success it
    must not linger."""
    subscription.write_subscription_file("tok", ["vless://x@h:1#a"], dir=subscriptions_dir)
    leftovers = [p.name for p in pathlib.Path(subscriptions_dir).iterdir() if p.name != "tok.txt"]
    assert leftovers == [], f"unexpected leftovers: {leftovers}"


@pytest.mark.parametrize(
    "unsafe_token",
    [
        "",
        "../escape",                  # path-traversal upward
        "foo/bar",                    # path-traversal sideways
        ".hidden",                    # dotfile (skipped by Caddy's `hide`)
    ],
)
def test_write_subscription_file_rejects_unsafe_token(subscriptions_dir: str, unsafe_token: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        subscription.write_subscription_file(unsafe_token, ["vless://x@h:1#a"], dir=subscriptions_dir)


def test_write_subscription_file_creates_dir_if_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "fresh-subs"
    assert not missing.exists()
    subscription.write_subscription_file("tok", ["vless://x@h:1#a"], dir=str(missing))
    assert (missing / "tok.txt").exists()


# ---------------------------------------------------------------------------
# remove_subscription_file
# ---------------------------------------------------------------------------


def test_remove_subscription_file_existing_returns_true(subscriptions_dir: str) -> None:
    subscription.write_subscription_file("tok", ["vless://x@h:1#a"], dir=subscriptions_dir)
    assert subscription.remove_subscription_file("tok", dir=subscriptions_dir) is True
    assert not (pathlib.Path(subscriptions_dir) / "tok.txt").exists()


def test_remove_subscription_file_absent_returns_false(subscriptions_dir: str) -> None:
    """Idempotent — caller can revoke a token that was never written."""
    assert subscription.remove_subscription_file("never-existed", dir=subscriptions_dir) is False
