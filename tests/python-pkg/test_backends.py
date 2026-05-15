"""Tests for stealth_vps.backends — UserBackend ABC + ThreeXUIBackend impl.

The headline behaviour: ThreeXUIBackend.add does a double-write — panel API
call first, then re-list reconcile, then users.index.json write. Tests
verify the ordering + that an API failure aborts BEFORE the index changes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import state
from stealth_vps.backends import ThreeXUIBackend, UserBackend
from stealth_vps.threex_client import ThreeXUIError


# ---------------------------------------------------------------------------
# UserBackend ABC
# ---------------------------------------------------------------------------


def test_userbackend_is_abstract() -> None:
    """UserBackend is an ABC; instantiating directly should fail."""
    with pytest.raises(TypeError, match="abstract"):
        UserBackend()  # type: ignore[abstract]


def test_userbackend_subclass_must_implement_all_abstract_methods() -> None:
    class Partial(UserBackend):
        def add(self, label, *, hysteria_password=""):
            return {}
        # missing list/revoke/get

    with pytest.raises(TypeError, match="abstract"):
        Partial()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# ThreeXUIBackend — happy-path add()
# ---------------------------------------------------------------------------


def _make_backend(users_index_path: str) -> tuple[ThreeXUIBackend, MagicMock]:
    """Build a backend with a mocked 3X-UI client.

    The client's calls are tracked via the returned MagicMock so tests
    can assert ordering + the actual values that hit the panel API.
    """
    mock_client = MagicMock()
    mock_client.get_inbound_by_remark.return_value = {
        "id": 1,
        "remark": "stealth-vps-reality",
        "settings": json.dumps({"clients": [{"email": "stealth-vps-default"}]}),
    }
    mock_client.add_client_to_inbound.return_value = {"success": True}
    mock_client.del_client.return_value = {"success": True}

    backend = ThreeXUIBackend(
        mock_client,
        reality_remark="stealth-vps-reality",
        reality_flow="xtls-rprx-vision",
        users_index_path=users_index_path,
    )
    return backend, mock_client


def test_add_user_calls_panel_then_writes_index(users_index_path: str) -> None:
    backend, client = _make_backend(users_index_path)

    # Reconcile re-list — make get_inbound_by_remark return a different
    # value on the second call so we can verify both calls happened.
    client.get_inbound_by_remark.side_effect = [
        {  # first call (initial fetch)
            "id": 1, "remark": "stealth-vps-reality",
            "settings": json.dumps({"clients": []}),
        },
        {  # second call (reconcile)
            "id": 1, "remark": "stealth-vps-reality",
            "settings": json.dumps({"clients": [{"email": "bob"}]}),
        },
    ]

    rec = backend.add("bob", hysteria_password="bob-hy2")

    # Panel was called.
    client.add_client_to_inbound.assert_called_once()
    args, kwargs = client.add_client_to_inbound.call_args
    assert kwargs["inbound_id"] == 1
    assert kwargs["client"]["email"] == "bob"
    assert kwargs["client"]["flow"] == "xtls-rprx-vision"
    assert kwargs["client"]["enable"] is True

    # Index was written.
    data = state.load_users_index(users_index_path)
    assert "bob" in data["users"]
    assert data["users"]["bob"]["hysteria_password"] == "bob-hy2"
    assert data["users"]["bob"]["reality_uuid"] == kwargs["client"]["id"]

    # Returned record matches the index.
    assert rec == data["users"]["bob"]


def test_add_user_panel_failure_does_not_touch_index(users_index_path: str) -> None:
    """If the panel API throws, the index must NOT have been written."""
    backend, client = _make_backend(users_index_path)
    client.add_client_to_inbound.side_effect = ThreeXUIError("panel rejected")

    before = state.load_users_index(users_index_path)
    with pytest.raises(ThreeXUIError):
        backend.add("bob", hysteria_password="bob-hy2")
    after = state.load_users_index(users_index_path)

    assert before == after, "index changed despite panel failure"


def test_add_user_reconcile_failure_does_not_touch_index(users_index_path: str) -> None:
    """Panel returned success but reconcile re-list didn't show the new
    label. Backend must raise before writing the index."""
    backend, client = _make_backend(users_index_path)
    # First call (initial fetch) → has the default seed client.
    # Second call (reconcile) → bob is missing — simulates a panel that
    # accepted the request but didn't persist (rare 3X-UI bug).
    client.get_inbound_by_remark.side_effect = [
        {"id": 1, "remark": "stealth-vps-reality",
         "settings": json.dumps({"clients": [{"email": "stealth-vps-default"}]})},
        {"id": 1, "remark": "stealth-vps-reality",
         "settings": json.dumps({"clients": [{"email": "stealth-vps-default"}]})},
    ]

    before = state.load_users_index(users_index_path)
    with pytest.raises(ThreeXUIError, match="not visible in re-listed"):
        backend.add("bob", hysteria_password="bob-hy2")
    after = state.load_users_index(users_index_path)
    assert before == after


def test_add_user_invalid_label_raises_before_panel_call(users_index_path: str) -> None:
    """Label validation happens up front — we should never call the panel
    with an invalid label."""
    backend, client = _make_backend(users_index_path)
    with pytest.raises(state.StateError, match="invalid"):
        backend.add("bad/label", hysteria_password="x")
    client.add_client_to_inbound.assert_not_called()


def test_add_user_missing_inbound_raises_before_panel_call(users_index_path: str) -> None:
    """Reality inbound doesn't exist in the panel — abort, don't try to
    create a client in a non-existent inbound."""
    backend, client = _make_backend(users_index_path)
    client.get_inbound_by_remark.return_value = None

    with pytest.raises(ThreeXUIError, match="not found in panel"):
        backend.add("bob", hysteria_password="x")
    client.add_client_to_inbound.assert_not_called()


# ---------------------------------------------------------------------------
# ThreeXUIBackend.revoke
# ---------------------------------------------------------------------------


def test_revoke_user_calls_panel_then_marks_index(users_index_path: str) -> None:
    backend, client = _make_backend(users_index_path)
    backend.revoke("alice")

    client.del_client.assert_called_once()
    args, kwargs = client.del_client.call_args
    assert kwargs["inbound_id"] == 1
    assert kwargs["client_uuid"] == "00000000-0000-0000-0000-000000000001"

    data = state.load_users_index(users_index_path)
    assert data["users"]["alice"]["enabled"] is False


def test_revoke_user_idempotent_when_panel_already_removed(users_index_path: str) -> None:
    """The panel returns an error if the client was already deleted —
    backend should swallow that and still flip the index row."""
    backend, client = _make_backend(users_index_path)
    client.del_client.side_effect = ThreeXUIError("client not found")

    backend.revoke("alice")   # must not raise

    data = state.load_users_index(users_index_path)
    assert data["users"]["alice"]["enabled"] is False


def test_revoke_user_missing_label_raises(users_index_path: str) -> None:
    backend, client = _make_backend(users_index_path)
    with pytest.raises(state.StateError, match="not found in the index"):
        backend.revoke("nobody")
    client.del_client.assert_not_called()


# ---------------------------------------------------------------------------
# ThreeXUIBackend.list / get — thin wrappers over state
# ---------------------------------------------------------------------------


def test_list_returns_only_enabled_by_default(users_index_path: str) -> None:
    backend, _ = _make_backend(users_index_path)
    rows = backend.list()
    labels = [label for label, _ in rows]
    assert labels == ["alice"]


def test_get_returns_record_or_none(users_index_path: str) -> None:
    backend, _ = _make_backend(users_index_path)
    rec = backend.get("alice")
    assert rec is not None
    assert rec["reality_uuid"] == "00000000-0000-0000-0000-000000000001"
    assert backend.get("nobody") is None
