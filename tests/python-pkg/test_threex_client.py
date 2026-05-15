"""Tests for stealth_vps.threex_client.

We mock `ThreeXUIClient._request` so the tests don't need a live 3X-UI
panel. The HTTP transport itself is stdlib (urllib + cookiejar) and is
exercised by the metrics updater + the bot against the real panel on
the test VPS, not here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from stealth_vps.threex_client import ThreeXUIClient, ThreeXUIError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client_with_request_mock(request_side_effect):
    """Build a ThreeXUIClient with `_request` mocked from the start.

    `__init__` calls `_login` which calls `_request` — without the patch,
    constructing the client would try to hit a real URL.
    """
    with patch.object(ThreeXUIClient, "_request", side_effect=request_side_effect):
        # Login response is the first call; subsequent test calls use the
        # same side_effect so the iterator drives them in order.
        return ThreeXUIClient(
            base_url="http://127.0.0.1:32999/basepath",
            username="admin",
            password="hunter2",
            verify_tls=False,
        )


# ---------------------------------------------------------------------------
# _login
# ---------------------------------------------------------------------------


def test_constructor_calls_login_with_form_credentials() -> None:
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}) as mock:
        ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="admin",
            password="hunter2",
            verify_tls=False,
        )
        mock.assert_called_once()
        args, kwargs = mock.call_args
        assert args[0] == "POST"
        assert args[1] == "login"
        assert kwargs["body"] == {"username": "admin", "password": "hunter2"}
        assert kwargs["body_format"] == "form"


def test_constructor_raises_when_login_returns_success_false() -> None:
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": False, "msg": "bad password"}):
        with pytest.raises(ThreeXUIError, match="login failed: bad password"):
            ThreeXUIClient(
                base_url="http://127.0.0.1:1/path",
                username="admin",
                password="wrong",
                verify_tls=False,
            )


# ---------------------------------------------------------------------------
# inbounds_list / get_inbound_by_remark
# ---------------------------------------------------------------------------


def test_inbounds_list_returns_obj_array() -> None:
    inbounds = [
        {"id": 1, "remark": "stealth-vps-reality"},
        {"id": 2, "remark": "some-other"},
    ]
    responses = iter([
        {"success": True},                               # login
        {"success": True, "obj": inbounds},              # inbounds_list
    ])
    client = _build_client_with_request_mock(lambda *a, **kw: next(responses))
    with patch.object(client, "_request", side_effect=lambda *a, **kw: next(responses, None)) as _:
        pass  # noop — the next call below uses the original side_effect

    # Re-patch with the remaining response since the iterator was consumed.
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": True, "obj": inbounds}):
        got = client.inbounds_list()
    assert got == inbounds


def test_inbounds_list_raises_on_success_false() -> None:
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        client = ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": False, "msg": "rate limited"}):
        with pytest.raises(ThreeXUIError, match="inbounds/list failed: rate limited"):
            client.inbounds_list()


def test_get_inbound_by_remark_returns_match() -> None:
    inbounds = [
        {"id": 1, "remark": "stealth-vps-reality"},
        {"id": 2, "remark": "other"},
    ]
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        client = ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": True, "obj": inbounds}):
        got = client.get_inbound_by_remark("stealth-vps-reality")
    assert got == {"id": 1, "remark": "stealth-vps-reality"}


def test_get_inbound_by_remark_returns_none_on_miss() -> None:
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        client = ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": True, "obj": []}):
        assert client.get_inbound_by_remark("nope") is None


# ---------------------------------------------------------------------------
# add_client_to_inbound / del_client
# ---------------------------------------------------------------------------


def test_add_client_to_inbound_serialises_settings_json() -> None:
    """The 3X-UI addClient endpoint takes `settings` as a JSON-encoded
    STRING inside the form body (not as a nested object) — verify the
    client serialises it correctly."""
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        client = ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )

    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": True}) as mock:
        client.add_client_to_inbound(
            inbound_id=42,
            client={"id": "uuid", "email": "alice", "flow": "xtls-rprx-vision"},
        )
    args, kwargs = mock.call_args
    assert args[0] == "POST"
    assert args[1] == "panel/api/inbounds/addClient"
    assert kwargs["body"]["id"] == 42
    # settings should be a JSON string, not a dict.
    import json
    assert isinstance(kwargs["body"]["settings"], str)
    parsed = json.loads(kwargs["body"]["settings"])
    assert parsed["clients"][0]["email"] == "alice"


def test_add_client_to_inbound_raises_on_panel_error() -> None:
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        client = ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": False, "msg": "duplicate"}):
        with pytest.raises(ThreeXUIError, match="addClient failed: duplicate"):
            client.add_client_to_inbound(
                inbound_id=1,
                client={"id": "uuid", "email": "alice"},
            )


def test_del_client_constructs_path_with_inbound_id_and_uuid() -> None:
    with patch.object(ThreeXUIClient, "_request", return_value={"success": True}):
        client = ThreeXUIClient(
            base_url="http://127.0.0.1:1/path",
            username="a", password="b", verify_tls=False,
        )
    with patch.object(ThreeXUIClient, "_request",
                       return_value={"success": True}) as mock:
        client.del_client(inbound_id=7, client_uuid="abc-def")
    args, kwargs = mock.call_args
    assert args[0] == "POST"
    assert args[1] == "panel/api/inbounds/7/delClient/abc-def"
