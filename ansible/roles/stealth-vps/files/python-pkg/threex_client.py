"""Minimal 3X-UI REST client — extracted from
stealth-vps-metrics-update.py.j2 so the bot, the CLI, and the
metrics updater all share one implementation.

Pure stdlib; no `requests`. The bot venv stays small and the
metrics updater (which runs every 30 s via systemd timer) doesn't
need to import a heavyweight HTTP library.
"""

from __future__ import annotations

import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ThreeXUIError(RuntimeError):
    """Raised on transport / auth / API-shape failures."""


class ThreeXUIClient:
    """3X-UI panel REST client.

    Login on construction (so subsequent calls use the session cookie).
    Reuses a single OpenerDirector with a CookieJar — same shape as
    Python's `requests.Session` but stdlib-only.

    Args:
      base_url: full URL up to (and excluding) the trailing slash of
        the panel — e.g. "https://127.0.0.1:32999/gre7hkrta4i9u8yk".
      username, password: from panel.state.yml.
      verify_tls: True in production with a real cert; False for the
        loopback (127.0.0.1 vs cert CN) call path. Mirrors the
        validate_certs=false used in the Ansible REST tasks.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        timeout: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self._cookiejar = http.cookiejar.CookieJar()
        ssl_ctx = ssl.create_default_context()
        if not verify_tls:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookiejar),
            urllib.request.HTTPSHandler(context=ssl_ctx),
        )

        self._login(username, password)

    # ---- HTTP plumbing ------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        body_format: str = "form",
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if body is not None:
            if body_format == "json":
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode("utf-8")
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                data = urllib.parse.urlencode(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ThreeXUIError(f"{method} {path} → HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ThreeXUIError(f"{method} {path} → transport: {exc.reason}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ThreeXUIError(f"{method} {path} → non-JSON response: {raw[:200]}") from exc

    # ---- API surface --------------------------------------------------

    def _login(self, username: str, password: str) -> None:
        result = self._request(
            "POST",
            "login",
            body={"username": username, "password": password},
            body_format="form",
        )
        if not result.get("success"):
            raise ThreeXUIError(f"login failed: {result.get('msg', 'no msg')}")

    def inbounds_list(self) -> list[dict[str, Any]]:
        result = self._request("GET", "panel/api/inbounds/list")
        if not result.get("success"):
            raise ThreeXUIError(f"inbounds/list failed: {result.get('msg', 'no msg')}")
        return result.get("obj", []) or []

    def get_inbound_by_remark(self, remark: str) -> dict[str, Any] | None:
        for inbound in self.inbounds_list():
            if inbound.get("remark") == remark:
                return inbound
        return None

    def add_client_to_inbound(self, inbound_id: int, client: dict[str, Any]) -> dict[str, Any]:
        """Add a client to an existing Reality inbound. Caller provides
        the client dict (UUID, email, flow, limitIp, totalGB, expiryTime,
        enable, tgId, subId, reset). Panel expects clients nested under
        settings -> clients in the inbound, but the addClient API takes
        a different shape — see 3X-UI source.
        """
        body = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [client]}),
        }
        result = self._request(
            "POST",
            f"panel/api/inbounds/addClient",
            body=body,
            body_format="json",
        )
        if not result.get("success"):
            raise ThreeXUIError(f"addClient failed: {result.get('msg', 'no msg')}")
        return result

    def del_client(self, inbound_id: int, client_uuid: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"panel/api/inbounds/{inbound_id}/delClient/{client_uuid}",
        )
        if not result.get("success"):
            raise ThreeXUIError(f"delClient failed: {result.get('msg', 'no msg')}")
        return result

    def get_client_traffic(self, email: str) -> dict[str, Any] | None:
        """clientStats[] per-client up/down totals from the panel's
        /inbounds/getClientTraffics/:email endpoint. Used by the
        metrics updater and by `/diagnose` for "user X used N GB".
        """
        try:
            result = self._request("GET", f"panel/api/inbounds/getClientTraffics/{email}")
        except ThreeXUIError:
            return None
        if not result.get("success"):
            return None
        return result.get("obj")
