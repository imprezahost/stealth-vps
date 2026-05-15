"""Shared pytest fixtures for the stealth_vps package tests.

We test the package against its source tree (no install step). `pyproject.toml`
sets `pythonpath = ["ansible/roles/stealth-vps/files"]` so the `stealth_vps/`
dir under that path is importable as `import stealth_vps`.

The fixtures here mostly hand out fresh temp paths so each test gets its own
users.index.json + subscriptions dir — no test cross-contamination.
"""

from __future__ import annotations

import json
import pathlib

import pytest


@pytest.fixture
def users_index_path(tmp_path: pathlib.Path) -> str:
    """Return a path to a fresh users.index.json seeded with a single user.

    Most tests want an already-populated index because the role itself
    seeds one on first install (see tasks/users_index.yml). Tests that
    need a missing file should pass `tmp_path / "missing.json"` directly.
    """
    p = tmp_path / "users.index.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "users": {
                    "alice": {
                        "reality_uuid": "00000000-0000-0000-0000-000000000001",
                        "hysteria_password": "alice-hy2-pw",
                        "sub_token": "alice-sub-token",
                        "created_at": "2026-01-01T00:00:00Z",
                        "enabled": True,
                    },
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return str(p)


@pytest.fixture
def subscriptions_dir(tmp_path: pathlib.Path) -> str:
    """A throwaway directory the subscription module can write into."""
    d = tmp_path / "subscriptions"
    d.mkdir()
    return str(d)
