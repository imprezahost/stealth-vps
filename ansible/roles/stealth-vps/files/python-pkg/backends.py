"""UserBackend interface + the v0.6.0 implementation (ThreeXUIBackend).

The interface is intentionally small so v0.7.0's HeadlessBackend slots
in without reshaping callers. Bot + CLI consume `UserBackend.add()` /
`.list()` / `.revoke()` and never touch the panel API directly.

In v0.6.0 (panel mode) `ThreeXUIBackend.add` does a double-write: the
panel API call is the runtime fact-of-record for traffic accounting,
the index is the operator's portable source-of-truth. v0.7.0 will
flip — index becomes authoritative, panel goes away.
"""

from __future__ import annotations

import datetime
import secrets
import uuid as uuid_mod
from abc import ABC, abstractmethod
from typing import Any

from . import state
from .threex_client import ThreeXUIClient, ThreeXUIError


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_sub_token() -> str:
    # 32 bytes = 256 bits of entropy. token_urlsafe gives ~43 chars
    # base64 — same shape as what users_index.yml uses for the
    # default client's seed token.
    return secrets.token_urlsafe(32).rstrip("=")


class UserBackend(ABC):
    """Backend-agnostic interface for user CRUD.

    Implementations:
      ThreeXUIBackend     — v0.6.0 panel mode (double-write panel + index)
      HeadlessBackend     — v0.7.0 (index only, re-renders xray config)
    """

    @abstractmethod
    def add(self, label: str, *, hysteria_password: str = "") -> dict[str, Any]:
        """Create a user. Returns the persisted record. Raises
        StateError / ThreeXUIError on failure.
        """

    @abstractmethod
    def list(self, include_disabled: bool = False) -> list[tuple[str, dict[str, Any]]]:
        """List users. Returns (label, record) pairs."""

    @abstractmethod
    def revoke(self, label: str) -> None:
        """Mark a user as disabled. Idempotent."""

    @abstractmethod
    def get(self, label: str) -> dict[str, Any] | None:
        """Return the record or None."""


class ThreeXUIBackend(UserBackend):
    """Panel-mode backend: every mutation hits the 3X-UI API first,
    then writes the index on success. On API failure nothing in the
    index changes — operator can retry safely.
    """

    def __init__(
        self,
        client: ThreeXUIClient,
        *,
        reality_remark: str,
        reality_flow: str,
        users_index_path: str = state.USERS_INDEX_PATH,
    ) -> None:
        self.client = client
        self.reality_remark = reality_remark
        self.reality_flow = reality_flow
        self.users_index_path = users_index_path

    def _get_reality_inbound(self) -> dict[str, Any]:
        inbound = self.client.get_inbound_by_remark(self.reality_remark)
        if inbound is None:
            raise ThreeXUIError(
                f"Reality inbound {self.reality_remark!r} not found in panel — was the role applied?"
            )
        return inbound

    def add(self, label: str, *, hysteria_password: str = "") -> dict[str, Any]:
        if not state.label_valid(label):
            raise state.StateError(
                f"label {label!r} invalid (must match [a-zA-Z0-9_-]{{1,32}} "
                f"and not start with {state.RESERVED_LABEL_PREFIX!r})"
            )
        inbound = self._get_reality_inbound()
        new_uuid = str(uuid_mod.uuid4())
        sub_token = _new_sub_token()

        # Panel write first — if this fails, we abort before touching
        # the index. The panel raises ThreeXUIError on failure.
        self.client.add_client_to_inbound(
            inbound_id=inbound["id"],
            client={
                "id": new_uuid,
                "flow": self.reality_flow,
                "email": label,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": "",
                "subId": "",
                "reset": 0,
            },
        )

        # Reconcile: re-fetch the inbound and confirm the client landed
        # before writing the index. Catches the rare case where the
        # panel returns success=true but the inbound write didn't fan
        # out to the underlying xray config.
        confirmed = self.client.get_inbound_by_remark(self.reality_remark)
        # Cheap check: re-list contains our label.
        if confirmed is None or label not in str(confirmed.get("settings", "")):
            raise ThreeXUIError(
                f"panel reported success but {label!r} not visible in re-listed inbound"
            )

        index = state.add_user(
            label,
            reality_uuid=new_uuid,
            hysteria_password=hysteria_password,
            sub_token=sub_token,
            created_at=_now_iso(),
            enabled=True,
            path=self.users_index_path,
        )
        return index["users"][label]

    def list(self, include_disabled: bool = False) -> list[tuple[str, dict[str, Any]]]:
        return state.list_users(self.users_index_path, include_disabled=include_disabled)

    def revoke(self, label: str) -> None:
        rec = state.get_user(label, self.users_index_path)
        if rec is None:
            raise state.StateError(f"user {label!r} not found in the index")
        inbound = self._get_reality_inbound()
        try:
            self.client.del_client(inbound_id=inbound["id"], client_uuid=rec["reality_uuid"])
        except ThreeXUIError:
            # 3X-UI returns an error if the client was already removed
            # from the panel — that's fine, we still want to mark the
            # index entry as disabled below.
            pass
        state.revoke_user(label, self.users_index_path)

    def get(self, label: str) -> dict[str, Any] | None:
        return state.get_user(label, self.users_index_path)
