"""HeadlessBackend — v0.7.0 panel-less implementation of UserBackend.

In v0.6 (panel mode) the source of truth was the 3X-UI panel API; the
index was a portable mirror written via double-write reconciliation
(see ThreeXUIBackend). In v0.7 (headless mode) the index becomes the
authoritative source: every mutation rewrites users.index.json, then
re-renders Xray + Hysteria2 configs from it, then reloads the services.

Same `UserBackend.add/list/revoke/get` interface as ThreeXUIBackend so
the bot and CLI never need to know which backend is active — they
import `select_backend()` from this package's `__init__.py` and get
either implementation based on whether `/etc/stealth-vps/panel.state.yml`
exists at startup.

This module is the *skeleton* shipped in v0.7.0 Block 1. The actual
config rendering + `systemctl reload` happens through the `reloader`
callback the caller passes in; that callback gets wired up in Block 3
when xray standalone + hysteria per-user templates land. Block 1 keeps
the surface area stable and unit-tested.
"""

from __future__ import annotations

import datetime
import secrets
import uuid as uuid_mod
from typing import Any, Callable

from . import state
from .backends import UserBackend


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_sub_token() -> str:
    return secrets.token_urlsafe(32).rstrip("=")


def _new_hysteria_password() -> str:
    # 32 chars matches what tasks/hysteria.yml's lookup('password', ...) used
    # for the role-managed default — keep the length parity so re-issued
    # tokens look indistinguishable from seed-generated ones.
    return secrets.token_urlsafe(24).rstrip("=")


# Type alias for the reload callback. Block 3 will provide a real impl that
# renders xray-config.json.j2 + hysteria-config.yaml.j2 from the current
# users.index.json then runs `systemctl reload xray hysteria-server`.
# In tests + Block 1 the callback is a no-op or a spy.
ReloadCallback = Callable[[], None]


def _noop_reloader() -> None:
    """Default reloader — does nothing. Block 3 replaces this with a real
    impl wired into the role's templates + systemd. Keeping a no-op default
    lets Block 1 tests exercise the index-mutation logic without needing
    Jinja2 + systemctl on the test runner.
    """


class HeadlessBackend(UserBackend):
    """Index-as-source-of-truth backend.

    `add/revoke` mutate `users.index.json` atomically (via `state.add_user`
    / `state.revoke_user`, both of which use the `os.replace` rename
    pattern), then invoke `reloader()` so the operator-visible side
    effects (Xray accepting the new UUID, Hysteria2 accepting the new
    password) happen before the call returns. If `reloader()` raises,
    the index is already written — operator has to either re-run the
    backend's reload helper or run `s-vps update` to reconcile.

    The Block 1 ship is intentionally light:
      - `add/list/revoke/get` work end-to-end against the index.
      - `reloader` defaults to a no-op; tests pass a spy to verify the
        invocation sequence (index-then-reload, not the other way).
      - Block 3 replaces `_noop_reloader` with a real implementation
        that lives in this package and imports jinja2 from the
        operator-CLI venv (not the stdlib-only constraint of the
        pkg itself — Block 4's `s-vps` Python CLI vendors jinja2).
    """

    def __init__(
        self,
        *,
        users_index_path: str = state.USERS_INDEX_PATH,
        reloader: ReloadCallback | None = None,
        default_hysteria_password: str = "",
    ) -> None:
        self.users_index_path = users_index_path
        self.reloader = reloader or _noop_reloader
        # When the caller doesn't pass a Hysteria2 password to `.add()`,
        # we mint a fresh one (per-user mode, v0.7.0 default). The panel
        # path used a shared password — passing `default_hysteria_password`
        # at construction lets a migration tool preserve that shared
        # value during the panel → headless cutover.
        self.default_hysteria_password = default_hysteria_password

    # ----- mutations ------------------------------------------------------

    def add(self, label: str, *, hysteria_password: str = "") -> dict[str, Any]:
        if not state.label_valid(label):
            raise state.StateError(
                f"label {label!r} invalid (must match [a-zA-Z0-9_-]{{1,32}} "
                f"and not start with {state.RESERVED_LABEL_PREFIX!r})"
            )

        # Per-user Hysteria2 password is the v0.7 default. Caller can
        # override (e.g. migration tool reuses panel-era shared password).
        hy_pw = hysteria_password or self.default_hysteria_password or _new_hysteria_password()

        new_uuid = str(uuid_mod.uuid4())
        sub_token = _new_sub_token()

        index = state.add_user(
            label,
            reality_uuid=new_uuid,
            hysteria_password=hy_pw,
            sub_token=sub_token,
            created_at=_now_iso(),
            enabled=True,
            path=self.users_index_path,
        )
        # Index is written before reload — if reload raises, the on-disk
        # state already reflects the new user. Caller can retry the reload
        # via `s-vps reload` or re-run ansible without losing the entry.
        self.reloader()
        return index["users"][label]

    def revoke(self, label: str) -> None:
        rec = state.get_user(label, self.users_index_path)
        if rec is None:
            raise state.StateError(f"user {label!r} not found in the index")
        state.revoke_user(label, self.users_index_path)
        self.reloader()

    # ----- reads ----------------------------------------------------------

    def list(self, include_disabled: bool = False) -> list[tuple[str, dict[str, Any]]]:
        return state.list_users(self.users_index_path, include_disabled=include_disabled)

    def get(self, label: str) -> dict[str, Any] | None:
        return state.get_user(label, self.users_index_path)
