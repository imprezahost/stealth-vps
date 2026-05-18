"""Reloader — renders Xray + Hysteria2 configs from users.index.json
then SIGHUPs the services so the new state is live.

This is the half of HeadlessBackend that B1 left as a `_noop_reloader`
hook. `HeadlessBackend.add()` and `.revoke()` mutate the index, then
call `reload_all()` from here to re-render and reload. Ansible also
calls into here once at the end of every converge (see
tasks/headless_reload.yml) so an `s-vps update` re-establishes the
multi-client config after the single-client seed that xray.yml /
hysteria.yml writes earlier in the play.

Pure stdlib — no jinja2, no PyYAML. Output is hand-rolled to keep the
package's stdlib-only invariant (so it can be copied into any host
without venv resolution). The hysteria YAML emitter handles the
flat-dict-of-dicts shape Hysteria expects and nothing more; if a
future Hysteria release needs anchors / multi-doc / tagged scalars,
the assumption breaks and we have to switch to PyYAML.

Why pure-Python rendering (not jinja2 reuse): the Ansible templates
in templates/xray-config.json.j2 + hysteria-config.yaml.j2 use
Ansible-specific filters (`to_json`, `to_nice_yaml`, etc.) and a
`{{ ansible_managed }}` placeholder. Reusing them at runtime would
mean linking a jinja2 env with custom filter shims — much more
moving parts than emitting the same structure directly.

Schema parity: the on-disk output of `render_xray_config_text()` is
JSON-loadable into the same dict as Ansible's xray-config.json.j2
rendered output. Same for hysteria after the YAML reader normalises.
The molecule headless scenario's verify.yml asserts the shape on
both first-converge (single client) and subsequent renders.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from . import state

# --- paths the role pins -------------------------------------------------
# Mirrors the Ansible defaults so HeadlessBackend can default everything
# without the caller having to thread the same constants through. Tests
# override these per-fixture; production callers (`s-vps reload`, the
# bot's reload hook) construct a Reloader with explicit paths.

XRAY_CONFIG_PATH = "/etc/xray/config.json"
HYSTERIA_CONFIG_PATH = "/etc/hysteria/config.yaml"
REALITY_STATE_PATH = "/etc/stealth-vps/reality.state.yml"
HYSTERIA_STATE_PATH = "/etc/stealth-vps/hysteria.state.yml"

# Default service-unit names. Match what the role's templates lay
# down on disk (see xray-standalone.service.j2 + the upstream
# hysteria deb's unit name).
XRAY_SERVICE = "xray.service"
HYSTERIA_SERVICE = "hysteria-server.service"


log = logging.getLogger("stealth_vps.reloader")


# --- exceptions ----------------------------------------------------------


class ReloadError(RuntimeError):
    """Raised on render / write / reload failures.

    Callers (HeadlessBackend.add/revoke) get this AFTER the index has
    already been written, so they're in the "service didn't pick up
    the change" failure mode. Operator can re-run `s-vps reload` to
    converge.
    """


# --- helpers -------------------------------------------------------------


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the narrow YAML dialect our state files use: a flat mapping
    of `key: value` lines, scalar values only (str / int / bool).

    reality.state.yml + hysteria.state.yml are both written by the role
    via `to_nice_yaml(indent=2, sort_keys=true)` on a flat dict (port +
    keys + passwords). We control the producer so the input shape is
    predictable. No nested mappings, no lists, no anchors — anything
    more complex than that raises ReloadError rather than guessing.

    Quoted strings (single or double) are unquoted. Bare `true`/`false`
    become bool. Bare digits become int. Everything else stays a str.
    """
    out: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ReloadError(f"state YAML line missing `:` — {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        elif value.lower() in ("true", "false"):
            value = value.lower() == "true"
        else:
            try:
                value = int(value)
            except ValueError:
                pass
        out[key] = value
    return out


def load_state_file(path: str) -> dict[str, Any]:
    """Read a *.state.yml file and return its parsed dict.

    Wrapper around `_parse_simple_yaml` that catches FileNotFoundError
    + IO errors and re-raises as ReloadError so callers can `except`
    on a single exception type.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _parse_simple_yaml(f.read())
    except FileNotFoundError as exc:
        raise ReloadError(f"state file missing at {path}") from exc
    except OSError as exc:
        raise ReloadError(f"could not read state file at {path}: {exc}") from exc


def _emit_yaml(data: Any, indent: int = 0) -> str:
    """Hand-rolled YAML emitter for the narrow shape Hysteria2's config
    needs: dict-of-(dict|scalar) with no lists, anchors, or multi-line
    strings. The Go YAML lib Hysteria uses (gopkg.in/yaml.v3) parses
    this dialect verbatim.

    Anything outside the supported shape (sets, tuples, bytes, custom
    objects) raises TypeError so a future template addition fails
    loudly rather than silently producing malformed output.
    """
    if not isinstance(data, Mapping):
        raise TypeError(f"_emit_yaml top-level must be a mapping, got {type(data).__name__}")
    return _emit_mapping(data, indent)


def _emit_mapping(d: Mapping[str, Any], indent: int) -> str:
    """Emit a mapping at the given indent. Keys go through `_emit_scalar`
    too — labels validated by `state.label_valid` are safe-looking but
    `yes` / `no` / `on` / `off` also match the regex and would flip
    to bool under a YAML 1.1 reader if left bare. Quoting keys is
    cheap insurance against username collisions with reserved words.
    """
    pad = "  " * indent
    parts: list[str] = []
    for k, v in d.items():
        if not isinstance(k, str):
            raise TypeError(f"YAML keys must be strings, got {type(k).__name__}")
        key_repr = _emit_scalar(k)
        if isinstance(v, Mapping):
            parts.append(f"{pad}{key_repr}:")
            parts.append(_emit_mapping(v, indent + 1))
        else:
            parts.append(f"{pad}{key_repr}: {_emit_scalar(v)}")
    return "\n".join(parts)


def _emit_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if v is None:
        return "null"
    if isinstance(v, str):
        # Always quote: catches YAML 1.1 surprises (`yes`/`no`/`on`/`off`
        # → bool, colon-containing strings → mapping, leading-digit
        # strings → maybe-int). json.dumps emits a double-quoted form
        # that's valid YAML 1.2 too.
        return json.dumps(v)
    raise TypeError(f"YAML scalar must be bool/int/None/str, got {type(v).__name__}")


def _write_atomic(path: str, content: str, mode: int = 0o640, group: str | None = None) -> None:
    """Replace `path` atomically with `content`. Writes to a sibling
    `.tmp` then `os.replace`s — POSIX rename(2) is atomic for moves in
    the same directory, so a reader either sees the old file or the
    new file, never a partial.

    Mirrors state.save_users_index's pattern but lets the caller pick
    mode + group (Xray + Hysteria expect specific group ownership so
    their service user can read).
    """
    parent = os.path.dirname(path) or "/"
    fd, tmp_path = tempfile.mkstemp(
        prefix=".reloader.", suffix=".tmp", dir=parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        os.chmod(tmp_path, mode)
        if group is not None:
            # shutil.chown lets us pass a group name string; the
            # underlying os.chown wants gid. Catch the case where the
            # group doesn't exist (test runners w/o the xray group)
            # and degrade to "don't change group".
            try:
                shutil.chown(tmp_path, group=group)
            except (LookupError, PermissionError):
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# --- xray ---------------------------------------------------------------


def render_xray_config(
    reality_state: Mapping[str, Any],
    users: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    reality_dest: str = "www.cloudflare.com:443",
    reality_servernames: Iterable[str] = ("www.cloudflare.com",),
    reality_flow: str = "xtls-rprx-vision",
) -> dict[str, Any]:
    """Build the Xray `config.json` dict from reality state + the user
    list. The shape matches templates/xray-config.json.j2 byte-for-byte
    after json.dumps — molecule's verify asserts this.

    `users` is an iterable of (label, record) tuples — same shape as
    `state.list_users(include_disabled=False)`. Disabled users are the
    caller's responsibility to filter out (HeadlessBackend always does).
    Empty `users` list raises ReloadError: Xray will fail to start with
    `clients: []`, so we'd rather get a Python exception we can log
    than a service crash-loop.
    """
    clients: list[dict[str, Any]] = []
    for label, rec in users:
        uuid = rec.get("reality_uuid")
        if not uuid:
            raise ReloadError(f"user {label!r} missing reality_uuid in index")
        clients.append(
            {
                "id": uuid,
                "email": label,
                "flow": reality_flow,
            }
        )
    if not clients:
        raise ReloadError(
            "render_xray_config got an empty user list — Xray won't start with no clients. "
            "Check that the index has at least one enabled user."
        )

    try:
        port = int(reality_state["port"])
        private_key = str(reality_state["private_key"])
        short_id = str(reality_state["short_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReloadError(f"reality state missing required fields: {exc}") from exc

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "reality-in",
                "listen": "0.0.0.0",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "clients": clients,
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "xver": 0,
                        "dest": reality_dest,
                        "serverNames": list(reality_servernames),
                        "privateKey": private_key,
                        "shortIds": [short_id],
                    },
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "metadataOnly": False,
                    "routeOnly": False,
                },
            }
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "blocked"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private"],
                    "outboundTag": "blocked",
                }
            ],
        },
    }


def render_xray_config_text(*args: Any, **kwargs: Any) -> str:
    """`render_xray_config` + a deterministic JSON dump. Sort keys so
    repeated runs with the same inputs produce byte-identical output —
    makes `git diff` of vendored configs and idempotency assertions in
    molecule trivial.
    """
    return json.dumps(render_xray_config(*args, **kwargs), indent=2, sort_keys=True)


# --- hysteria -----------------------------------------------------------


def render_hysteria_config(
    hysteria_state: Mapping[str, Any],
    users: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    per_user: bool,
    tls_cert: str,
    tls_key: str,
    masquerade_url: str,
    bandwidth_up: str,
    bandwidth_down: str,
    metrics_enabled: bool = False,
    traffic_stats_listen: str = "127.0.0.1:9090",
) -> dict[str, Any]:
    """Build the Hysteria2 server config dict.

    Auth mode toggles on `per_user`:
      - per_user=False → `auth.type=password` with the single shared
        password from hysteria.state.yml. Matches the v0.6 panel-mode
        behaviour byte-for-byte.
      - per_user=True  → `auth.type=userpass` with a label→password
        map sourced from users.index.json. Per-user revocation works
        because hysteria-server reloads the userpass list on SIGHUP.

    Hysteria's userpass map is INLINE in the config file (no external
    userpass-file support upstream), so any add/revoke writes the
    whole file. That's why HeadlessBackend.reloader fully re-renders
    on every mutation rather than doing surgical patches.
    """
    try:
        port = int(hysteria_state["port"])
        obfs_password = str(hysteria_state["obfs_password"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReloadError(f"hysteria state missing required fields: {exc}") from exc

    cfg: dict[str, Any] = {
        "listen": f":{port}",
        "tls": {"cert": tls_cert, "key": tls_key},
        "obfs": {
            "type": "salamander",
            "salamander": {"password": obfs_password},
        },
    }

    if per_user:
        userpass: dict[str, str] = {}
        for label, rec in users:
            pw = rec.get("hysteria_password", "")
            if not pw:
                # Skip users that don't have a Hysteria password yet —
                # the panel-mode migration path may leave this empty
                # for users imported before per-user was enabled.
                continue
            userpass[label] = pw
        if not userpass:
            raise ReloadError(
                "render_hysteria_config(per_user=True) got no users with passwords — "
                "hysteria-server will reject everything. Either disable per_user or "
                "back-fill hysteria_password for the existing users in the index."
            )
        cfg["auth"] = {"type": "userpass", "userpass": userpass}
    else:
        auth_password = str(hysteria_state.get("auth_password", ""))
        if not auth_password:
            raise ReloadError(
                "render_hysteria_config(per_user=False) but hysteria.state.yml "
                "has no auth_password set"
            )
        cfg["auth"] = {"type": "password", "password": auth_password}

    cfg["masquerade"] = {
        "type": "proxy",
        "proxy": {"url": masquerade_url, "rewriteHost": True},
    }
    cfg["bandwidth"] = {"up": bandwidth_up, "down": bandwidth_down}

    if metrics_enabled:
        cfg["trafficStats"] = {"listen": traffic_stats_listen}

    return cfg


def render_hysteria_config_text(*args: Any, **kwargs: Any) -> str:
    """`render_hysteria_config` + a deterministic JSON dump.

    Note: the on-disk filename is config.yaml, but the CONTENT is
    JSON-syntax. YAML 1.2 is a strict superset of JSON, so Hysteria's
    YAML parser (gopkg.in/yaml.v3) reads JSON-formatted bytes without
    complaint. We emit JSON rather than block YAML because:

      - the Ansible template at templates/hysteria-config.yaml.j2 uses
        `to_nice_json(sort_keys=true, indent=2)` to render the SEED
        config; the reloader has to produce byte-identical output for
        molecule's idempotence test to pass. Both go through the same
        json.dumps path → bytes match.
      - JSON-syntax dodges YAML 1.1 traps: `yes` / `no` / `on` / `off`
        as bool, leading-zero strings as int, etc. A user labelled
        `no` won't accidentally turn into `False: <password>`.

    sort_keys=True so a single user list always produces the same
    output regardless of dict insertion order — locks in
    file-comparison idempotency.
    """
    cfg = render_hysteria_config(*args, **kwargs)
    return json.dumps(cfg, indent=2, sort_keys=True)


# --- systemctl wrapper --------------------------------------------------


def reload_service(name: str, *, dry_run: bool = False, mode: str = "reload") -> None:
    """`systemctl <mode> <name>` — mode is "reload" (SIGHUP) or "restart".

    Neither xray-core nor Hysteria 2 currently supports hot reload, so
    the Reloader uses `mode="restart"` for both. The `mode` parameter
    stays in case a future Hysteria release implements real SIGHUP
    reload (apernet/hysteria#717); flipping back to `mode="reload"`
    would then be a one-line change.

    Why hot reload doesn't work today:
      - xray-core has no SIGHUP handler. Go's default behavior on
        SIGHUP is to terminate the process; systemd then sees the
        clean exit and leaves the service dead.
      - Hysteria 2 wires SIGHUP into the same "received signal,
        shutting down gracefully" path as SIGTERM/SIGINT — sending
        SIGHUP just stops the daemon.

    `dry_run=True` skips the subprocess call entirely — used by the
    molecule headless verify and by `s-vps reload --dry-run`.
    """
    if mode not in ("reload", "restart"):
        raise ValueError(f"reload_service mode must be 'reload' or 'restart', got {mode!r}")
    if dry_run:
        log.info("dry-run: skipping `systemctl %s %s`", mode, name)
        return
    try:
        subprocess.run(
            ["systemctl", mode, name],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        # systemctl not on PATH — happens on test runners that aren't
        # systemd hosts. Re-raise as ReloadError so HeadlessBackend can
        # surface a clear message instead of a generic OSError.
        raise ReloadError("systemctl not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise ReloadError(
            f"`systemctl {mode} {name}` failed (rc={exc.returncode}): "
            f"stderr={exc.stderr!r}"
        ) from exc


# --- orchestrator -------------------------------------------------------


class Reloader:
    """High-level reload orchestrator.

    Constructed once at process start; called as a no-arg callable
    (matching `ReloadCallback`) by HeadlessBackend on every mutation.
    Each call re-reads state files + index, re-renders both configs,
    atomically replaces them, and reloads the services.

    Components can be disabled individually:
      - `reality_enabled=False` → skips xray rendering + reload
      - `hysteria_enabled=False` → skips hysteria rendering + reload
    A reloader with both disabled is a no-op (still useful in tests).
    """

    def __init__(
        self,
        *,
        users_index_path: str = state.USERS_INDEX_PATH,
        # xray
        reality_enabled: bool = True,
        reality_state_path: str = REALITY_STATE_PATH,
        xray_config_path: str = XRAY_CONFIG_PATH,
        xray_service: str = XRAY_SERVICE,
        xray_group: str | None = "xray",
        reality_dest: str = "www.cloudflare.com:443",
        reality_servernames: Iterable[str] = ("www.cloudflare.com",),
        reality_flow: str = "xtls-rprx-vision",
        # hysteria
        hysteria_enabled: bool = False,
        hysteria_state_path: str = HYSTERIA_STATE_PATH,
        hysteria_config_path: str = HYSTERIA_CONFIG_PATH,
        hysteria_service: str = HYSTERIA_SERVICE,
        hysteria_group: str | None = "hysteria",
        hysteria_per_user: bool = False,
        hysteria_tls_cert: str = "",
        hysteria_tls_key: str = "",
        hysteria_masquerade_url: str = "",
        hysteria_bandwidth_up: str = "100 mbps",
        hysteria_bandwidth_down: str = "100 mbps",
        hysteria_metrics_enabled: bool = False,
        # control
        dry_run: bool = False,
    ) -> None:
        self.users_index_path = users_index_path
        # xray
        self.reality_enabled = reality_enabled
        self.reality_state_path = reality_state_path
        self.xray_config_path = xray_config_path
        self.xray_service = xray_service
        self.xray_group = xray_group
        self.reality_dest = reality_dest
        self.reality_servernames = tuple(reality_servernames)
        self.reality_flow = reality_flow
        # hysteria
        self.hysteria_enabled = hysteria_enabled
        self.hysteria_state_path = hysteria_state_path
        self.hysteria_config_path = hysteria_config_path
        self.hysteria_service = hysteria_service
        self.hysteria_group = hysteria_group
        self.hysteria_per_user = hysteria_per_user
        self.hysteria_tls_cert = hysteria_tls_cert
        self.hysteria_tls_key = hysteria_tls_key
        self.hysteria_masquerade_url = hysteria_masquerade_url
        self.hysteria_bandwidth_up = hysteria_bandwidth_up
        self.hysteria_bandwidth_down = hysteria_bandwidth_down
        self.hysteria_metrics_enabled = hysteria_metrics_enabled
        # control
        self.dry_run = dry_run

    def __call__(self) -> None:
        """Run the full reload cycle. Implements `ReloadCallback`."""
        self.reload_all()

    def reload_all(self) -> None:
        """Re-render every enabled component, write atomically, reload.

        Renders before any write so a failure to load (e.g. corrupt
        state file) aborts the whole cycle BEFORE we touch the
        on-disk configs. Avoids the "half-renamed config + service
        restarted with old config" failure mode.
        """
        users = state.list_users(self.users_index_path, include_disabled=False)

        # --- xray -------------------------------------------------------
        xray_text: str | None = None
        if self.reality_enabled:
            reality_state = load_state_file(self.reality_state_path)
            xray_text = render_xray_config_text(
                reality_state,
                users,
                reality_dest=self.reality_dest,
                reality_servernames=self.reality_servernames,
                reality_flow=self.reality_flow,
            )

        # --- hysteria ---------------------------------------------------
        hy_text: str | None = None
        if self.hysteria_enabled:
            hy_state = load_state_file(self.hysteria_state_path)
            hy_text = render_hysteria_config_text(
                hy_state,
                users,
                per_user=self.hysteria_per_user,
                tls_cert=self.hysteria_tls_cert,
                tls_key=self.hysteria_tls_key,
                masquerade_url=self.hysteria_masquerade_url,
                bandwidth_up=self.hysteria_bandwidth_up,
                bandwidth_down=self.hysteria_bandwidth_down,
                metrics_enabled=self.hysteria_metrics_enabled,
            )

        # --- writes -----------------------------------------------------
        # All renders succeeded; safe to commit to disk now.
        if xray_text is not None:
            _write_atomic(
                self.xray_config_path,
                xray_text,
                mode=0o640,
                group=self.xray_group,
            )
            log.info("rendered %s (%d users)", self.xray_config_path, len(users))

        if hy_text is not None:
            _write_atomic(
                self.hysteria_config_path,
                hy_text,
                mode=0o640,
                group=self.hysteria_group,
            )
            log.info(
                "rendered %s (per_user=%s, %d users)",
                self.hysteria_config_path,
                self.hysteria_per_user,
                len(users),
            )

        # --- reloads ----------------------------------------------------
        # Both services use `restart` (not `reload`) because neither
        # xray-core nor Hysteria 2 actually support hot reload:
        #   - xray-core has no SIGHUP handler — Go's default is to
        #     terminate the process. systemd then sees signal=HUP as a
        #     "clean" exit (Restart=on-failure doesn't kick in) and
        #     leaves the service dead.
        #   - Hysteria 2 wires SIGHUP into its general "received signal,
        #     shutting down gracefully" path (apernet/hysteria#717 tracks
        #     real hot-reload support; until that lands, SIGHUP just
        #     stops the server cleanly — same effective behaviour as Xray
        #     but via an explicit handler).
        # `restart` causes a sub-second cutover that drops in-flight
        # connections; clients reconnect on their own retry loop. The
        # `mode` kwarg stays on `reload_service` so a future Hysteria
        # release with real SIGHUP-reload can flip to `mode="reload"`
        # without restructuring the call.
        if xray_text is not None:
            reload_service(self.xray_service, dry_run=self.dry_run, mode="restart")
        if hy_text is not None:
            reload_service(self.hysteria_service, dry_run=self.dry_run, mode="restart")


# --- CLI ----------------------------------------------------------------
# `python3 -m stealth_vps.reloader [...]` — invoked from
# tasks/headless_reload.yml at the tail of every converge so a fresh
# multi-client config is rendered after users_index.yml seeds the
# index. Re-runnable with no args from the operator's shell to force
# an out-of-band reload (`s-vps reload` in B4 wraps this with sane
# default flags + sudo).


def _bool_flag(s: str) -> bool:
    """argparse type=callable that accepts the strings Ansible passes via
    Jinja boolean coercion ("True" / "False" / "true" / "false") as well
    as the more shell-friendly "1" / "0" forms.
    """
    return str(s).strip().lower() in ("1", "true", "yes", "on")


def _build_arg_parser() -> "argparse.ArgumentParser":
    import argparse

    p = argparse.ArgumentParser(
        prog="python3 -m stealth_vps.reloader",
        description=(
            "Re-render Xray + Hysteria2 configs from users.index.json + state "
            "files, then `systemctl reload` the affected services. Idempotent."
        ),
    )
    p.add_argument("--users-index-path", default=state.USERS_INDEX_PATH)
    # xray
    p.add_argument("--reality-enabled", type=_bool_flag, default=True)
    p.add_argument("--reality-state-path", default=REALITY_STATE_PATH)
    p.add_argument("--xray-config-path", default=XRAY_CONFIG_PATH)
    p.add_argument("--xray-service", default=XRAY_SERVICE)
    p.add_argument("--xray-group", default="xray")
    p.add_argument("--reality-dest", default="www.cloudflare.com:443")
    p.add_argument(
        "--reality-servernames",
        default="www.cloudflare.com",
        help="Comma-separated list of SNI values for the Reality inbound.",
    )
    p.add_argument("--reality-flow", default="xtls-rprx-vision")
    # hysteria
    p.add_argument("--hysteria-enabled", type=_bool_flag, default=False)
    p.add_argument("--hysteria-state-path", default=HYSTERIA_STATE_PATH)
    p.add_argument("--hysteria-config-path", default=HYSTERIA_CONFIG_PATH)
    p.add_argument("--hysteria-service", default=HYSTERIA_SERVICE)
    p.add_argument("--hysteria-group", default="hysteria")
    p.add_argument("--hysteria-per-user", type=_bool_flag, default=False)
    p.add_argument("--hysteria-tls-cert", default="")
    p.add_argument("--hysteria-tls-key", default="")
    p.add_argument("--hysteria-masquerade-url", default="")
    p.add_argument("--hysteria-bandwidth-up", default="100 mbps")
    p.add_argument("--hysteria-bandwidth-down", default="100 mbps")
    p.add_argument("--hysteria-metrics-enabled", type=_bool_flag, default=False)
    # control
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Render configs to disk but skip `systemctl reload`. Useful in "
        "containers (molecule, dev VMs) where the services aren't actually "
        "running.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code (0 success, 1 reload error)."""
    logging.basicConfig(level=logging.INFO, format="reloader: %(message)s")
    args = _build_arg_parser().parse_args(argv)

    r = Reloader(
        users_index_path=args.users_index_path,
        reality_enabled=args.reality_enabled,
        reality_state_path=args.reality_state_path,
        xray_config_path=args.xray_config_path,
        xray_service=args.xray_service,
        xray_group=args.xray_group or None,
        reality_dest=args.reality_dest,
        reality_servernames=[s.strip() for s in args.reality_servernames.split(",") if s.strip()],
        reality_flow=args.reality_flow,
        hysteria_enabled=args.hysteria_enabled,
        hysteria_state_path=args.hysteria_state_path,
        hysteria_config_path=args.hysteria_config_path,
        hysteria_service=args.hysteria_service,
        hysteria_group=args.hysteria_group or None,
        hysteria_per_user=args.hysteria_per_user,
        hysteria_tls_cert=args.hysteria_tls_cert,
        hysteria_tls_key=args.hysteria_tls_key,
        hysteria_masquerade_url=args.hysteria_masquerade_url,
        hysteria_bandwidth_up=args.hysteria_bandwidth_up,
        hysteria_bandwidth_down=args.hysteria_bandwidth_down,
        hysteria_metrics_enabled=args.hysteria_metrics_enabled,
        dry_run=args.dry_run,
    )
    try:
        r()
    except ReloadError as exc:
        log.error("reload failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
