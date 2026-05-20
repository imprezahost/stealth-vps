"""stealth_vps.fleet — multi-node control-plane primitives (v0.10.0+).

Pure-stdlib module backing `s-vps fleet add/remove/sync/list`. The
control box runs this; data nodes never import it (they're "dumb"
receivers of pushed users.index.json + a SIGHUP via Reloader).

Design constraints (per `docs/internal/roadmap-v0.10-multi-node.md`):

  - Stdlib only. No paramiko, no fabric, no PyYAML. SSH/scp go through
    subprocess to openssh-client (already a dep of ansible). YAML I/O
    is hand-rolled, matching the dialect `state.py` + `reloader.py` use.
  - Control plane pushes; data nodes never pull. The `push_to_node`
    function streams `users.index.json` over SSH stdin to the
    `s-vps fleet-receive` HIDDEN subcommand on the remote. That
    subverb is what the restricted-key `command="..."` on the data
    node's authorized_keys forces.
  - Per-node SSH key. Each FleetNode points at its own
    `/etc/stealth-vps/keys/control_to_<node_id>.ed25519`. Compromising
    one key gives an attacker write access to one node's index —
    equivalent to compromising the control. No lateral movement.
  - Eventually consistent. A failed push doesn't queue. Next mutation
    (or manual `fleet sync`) retries. Operators see per-node ✓/✗ at
    each sync; they decide whether to investigate or wait.

Public surface (importable from `stealth_vps`):

    FleetNode      — dataclass for one data node's metadata
    PushResult     — outcome of one push attempt
    FleetError     — single exception type for callers to catch
    load_node, save_node, remove_node, load_fleet
    push_to_node, sync_all, update_sync_status
    validate_node_id

The Telegram bot + the `s-vps fleet *` CLI verbs are thin presenters
on top of this module — same separation as `state.py` vs `cli.py`.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
import time
from typing import Any, Iterable

log = logging.getLogger("stealth_vps.fleet")


# ---------------------------------------------------------------------------
# Constants + defaults
# ---------------------------------------------------------------------------

DEFAULT_FLEET_DIR = "/etc/stealth-vps/fleet"
DEFAULT_KEYS_DIR = "/etc/stealth-vps/keys"

# `node_id` is a filesystem path component + a remark suffix + a label
# operators type on the CLI. Strict lowercase + dashes keeps it safe
# across all three. 32-char ceiling matches the `users.index.json`
# label rule.
NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

# Default sync budget. Empirically a coast-to-coast `s-vps fleet-receive`
# (SSH handshake + 1KB index + reload) finishes in ~3-5 seconds; 30
# gives 6× headroom for transient slowness. Operators on satellite
# links bump it with `--timeout`.
DEFAULT_SYNC_TIMEOUT = 30.0

# 4 workers in `sync_all` (Open Question #3, locked).
DEFAULT_PARALLEL = 4

# Hidden CLI subverb that the data node's restricted authorized_keys
# forces. Spelled-out string here so the auth template + the push call
# stay in sync (search for either to find both).
HIDDEN_RECEIVE_SUBCMD = "fleet-receive"


class FleetError(Exception):
    """All non-recoverable failures (parse, IO, validation) raise this.
    Network failures during push DON'T raise — they return a
    `PushResult(ok=False)` so `sync_all` can keep going across the
    rest of the fleet."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class FleetNode:
    """One data node's metadata, as it lives in `/etc/stealth-vps/fleet/<id>.yml`.

    Fields default to safe-empty so partially-bootstrapped nodes can
    round-trip through load → mutate → save without losing fields we
    haven't discovered yet (e.g. `last_sync_at` is `None` until the
    first sync, then becomes an ISO timestamp).
    """

    node_id: str
    ssh_host: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key_path: str = ""

    # Discovered on `fleet add` by reading the data node's reality.state.yml
    # over SSH. Refreshed on every `fleet sync` so a remote key rotation
    # propagates back into the subscription bundle on the next push.
    reality_public_key: str = ""
    reality_short_id: str = ""
    reality_port: int = 0
    reality_servernames: list[str] = dataclasses.field(default_factory=list)

    hysteria_port: int = 0
    hysteria_obfs_password: str = ""

    # Public host for client URIs. When unset, falls back to `ssh_host`
    # (typical when SSH and client traffic share an IP). Operators with
    # bastion + DNS split set both — SSH hits the bastion, clients hit
    # the DNS name.
    public_host: str | None = None

    # LE cert CN on this node. Empty string when the data node runs
    # without a domain (IP-only Reality + self-signed Hysteria2).
    domain: str = ""

    added_at: str = ""
    last_sync_at: str | None = None
    last_sync_status: str = "never"   # ok | failed | never

    # ---- Computed ---------------------------------------------------

    @property
    def public_endpoint(self) -> str:
        """The host clients connect to. Used by the multi-node URI
        builder; `public_host` overrides `ssh_host` when set."""
        return self.public_host or self.ssh_host

    # ---- Round-trip dict <-> dataclass ------------------------------

    def to_dict(self) -> dict[str, Any]:
        """For YAML serialisation. Skips fields with None where the
        YAML schema treats `null` and absent identically."""
        return {
            "node_id": self.node_id,
            "ssh_host": self.ssh_host,
            "ssh_port": self.ssh_port,
            "ssh_user": self.ssh_user,
            "ssh_key_path": self.ssh_key_path,
            "reality_public_key": self.reality_public_key,
            "reality_short_id": self.reality_short_id,
            "reality_port": self.reality_port,
            "reality_servernames": list(self.reality_servernames),
            "hysteria_port": self.hysteria_port,
            "hysteria_obfs_password": self.hysteria_obfs_password,
            "public_host": self.public_host,
            "domain": self.domain,
            "added_at": self.added_at,
            "last_sync_at": self.last_sync_at,
            "last_sync_status": self.last_sync_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FleetNode":
        """Inverse of to_dict. Tolerates missing keys (default applies)
        so v0.10.x can add fields without breaking v0.10.0-written
        node files. Unknown extra keys are dropped silently (forward
        compatibility — v0.11+ may add fields older Python doesn't
        know about)."""
        # Filter to fields we know — drops the noise from forward-compat keys.
        known = {f.name for f in dataclasses.fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        # Required fields. node_id + ssh_host have no useful default.
        if "node_id" not in clean:
            raise FleetError("node YAML missing required field `node_id`")
        if "ssh_host" not in clean:
            raise FleetError(f"node YAML for {clean.get('node_id')!r} missing `ssh_host`")
        # reality_servernames may come back as None if the YAML had
        # `reality_servernames:` with no list items (badly-edited file).
        if clean.get("reality_servernames") is None:
            clean["reality_servernames"] = []
        return cls(**clean)


@dataclasses.dataclass
class PushResult:
    """Outcome of one `push_to_node` call. The `ok` flag drives the
    per-node ✓/✗ column in `s-vps fleet sync`'s output table."""

    node_id: str
    ok: bool
    stdout: str
    stderr: str
    duration_ms: int


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_node_id(node_id: str) -> None:
    """Raise FleetError if `node_id` doesn't match the canonical regex.
    Called by every entry point that accepts a node_id from the CLI;
    centralised so the error message is consistent."""
    if not NODE_ID_RE.match(node_id):
        raise FleetError(
            f"node_id {node_id!r} invalid — must match [a-z0-9-]{{1,32}}, "
            f"start with [a-z0-9], lowercase only. Examples: `tokyo-1`, "
            f"`amsterdam-eu`, `node5`."
        )


# ---------------------------------------------------------------------------
# YAML I/O — hand-rolled, narrow dialect
# ---------------------------------------------------------------------------
# The schema is:
#   - flat top-level keys (`key: value`)
#   - one known list field (`reality_servernames` → `- item` indented by 2)
#   - scalars: int, str (quoted or bare), bool, null
#   - no nested mappings, no anchors, no multi-doc
#
# Parser is permissive on input (accepts both `null` and absent for
# nullable fields), emitter is strict on output (quotes all strings,
# emits `null` for None) so files written by the role always round-trip
# byte-identical through save → load → save.


def _parse_node_yaml(text: str) -> dict[str, Any]:
    """Parse a fleet/<node>.yml file. Returns a dict suitable for
    `FleetNode.from_dict`. Raises FleetError on syntactic problems."""
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - "):
            if current_list_key is None:
                raise FleetError(
                    f"line {lineno}: list item without parent key — {line!r}"
                )
            item = line[4:].strip()
            if item.startswith(("'", '"')) and item.endswith(item[0]) and len(item) >= 2:
                item = item[1:-1]
            out[current_list_key].append(item)
            continue

        # Any non-list-item line resets the list context.
        current_list_key = None

        if ":" not in line:
            raise FleetError(f"line {lineno}: missing `:` — {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if value == "":
            # `key:` on its own line opens a list section.
            out[key] = []
            current_list_key = key
            continue

        out[key] = _parse_scalar(value)
    return out


def _parse_scalar(value: str) -> Any:
    """Parse a scalar value from a YAML line. Handles quoted strings,
    null, bool, int, and falls back to bare string."""
    if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
        return value[1:-1]
    if value.lower() == "null" or value == "~":
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def _emit_node_yaml(data: dict[str, Any]) -> str:
    """Render the dict as the role's narrow YAML dialect. Always quotes
    string scalars (catches YAML 1.1 surprises like `yes`/`no` → bool)
    and emits `null` for None so the parser can distinguish unset from
    empty string."""
    lines: list[str] = []
    for k, v in data.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_emit_scalar(item)}")
        else:
            lines.append(f"{k}: {_emit_scalar(v)}")
    return "\n".join(lines) + "\n"


def _emit_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        # json.dumps emits a double-quoted form that's valid YAML 1.2.
        return json.dumps(v)
    raise FleetError(f"unsupported scalar type: {type(v).__name__}")


# ---------------------------------------------------------------------------
# Load / save / remove / list
# ---------------------------------------------------------------------------


def _node_yaml_path(node_id: str, fleet_dir: str) -> str:
    return os.path.join(fleet_dir, f"{node_id}.yml")


def load_node(node_id: str, fleet_dir: str = DEFAULT_FLEET_DIR) -> FleetNode:
    """Load one node from disk. Raises FleetError when the file is
    missing or malformed."""
    validate_node_id(node_id)
    path = _node_yaml_path(node_id, fleet_dir)
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FleetError(f"node {node_id!r} not registered (no file at {path})") from exc
    except OSError as exc:
        raise FleetError(f"could not read {path}: {exc}") from exc
    data = _parse_node_yaml(text)
    return FleetNode.from_dict(data)


def save_node(node: FleetNode, fleet_dir: str = DEFAULT_FLEET_DIR) -> None:
    """Atomic-replace `{fleet_dir}/{node_id}.yml` with the node's
    current state. Creates parent dir if missing. Mode 0600.

    Atomic via tempfile + os.replace — concurrent readers (e.g. the
    bot at /user add time) never see a half-written file."""
    validate_node_id(node.node_id)
    if node.node_id != node.node_id.strip():
        raise FleetError("node_id must not have leading/trailing whitespace")
    os.makedirs(fleet_dir, exist_ok=True)
    path = _node_yaml_path(node.node_id, fleet_dir)
    payload = _emit_node_yaml(node.to_dict())

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{node.node_id}.", suffix=".tmp", dir=fleet_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the tmp file on failure so we don't leave .tmp
        # crumbs littering /etc/stealth-vps/fleet/.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def remove_node(node_id: str, fleet_dir: str = DEFAULT_FLEET_DIR) -> bool:
    """Delete the node's YAML file. Returns True if a file was deleted,
    False if the node wasn't registered (idempotent — same pattern as
    `subscription.remove_subscription_file`).

    Does NOT touch the data node itself (no remote SSH, no service
    actions). The operator decommissions the box separately. Does
    NOT delete the SSH key under `keys/` either — `s-vps fleet remove`
    handles that at the CLI layer so the key removal can be opt-in
    via flag."""
    validate_node_id(node_id)
    path = _node_yaml_path(node_id, fleet_dir)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def load_fleet(fleet_dir: str = DEFAULT_FLEET_DIR) -> list[FleetNode]:
    """Enumerate every node in the fleet, sorted by node_id. Empty
    when the directory doesn't exist (e.g. on a single-node host
    that's not configured as a control)."""
    if not os.path.isdir(fleet_dir):
        return []
    nodes: list[FleetNode] = []
    for entry in sorted(os.listdir(fleet_dir)):
        if not entry.endswith(".yml"):
            continue
        if entry.startswith("."):
            continue   # skip .tmp files from in-flight saves
        node_id = entry[:-len(".yml")]
        try:
            nodes.append(load_node(node_id, fleet_dir))
        except FleetError as exc:
            # Don't fail the whole list on one bad file — the operator
            # can still see good nodes + we log the bad one.
            log.warning("skipping malformed node file %s: %s", entry, exc)
            continue
    return nodes


def update_sync_status(
    node_id: str,
    *,
    status: str,
    at: str | None = None,
    fleet_dir: str = DEFAULT_FLEET_DIR,
) -> None:
    """Update just the `last_sync_*` fields. Used after `push_to_node`
    completes so the next `fleet list` shows when each node was last
    in sync. Load → mutate → save, atomic via save_node's pattern."""
    if status not in ("ok", "failed", "never"):
        raise FleetError(f"sync status must be ok/failed/never, got {status!r}")
    node = load_node(node_id, fleet_dir)
    node.last_sync_status = status
    if at is None:
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    node.last_sync_at = at
    save_node(node, fleet_dir)


# ---------------------------------------------------------------------------
# Push / sync
# ---------------------------------------------------------------------------


def push_to_node(
    node: FleetNode,
    users_index_path: str,
    *,
    timeout: float = DEFAULT_SYNC_TIMEOUT,
    dry_run: bool = False,
) -> PushResult:
    """Stream `users_index_path` over SSH stdin to the data node's
    `s-vps fleet-receive` hidden subverb. The data node's restricted
    authorized_keys forces that command regardless of what's sent,
    so passing it explicitly here is belt-and-braces (also lets dev
    bootstraps with a non-restricted key work the same way).

    Returns a `PushResult` rather than raising on network errors —
    the caller (`sync_all`) needs to keep going across the rest of
    the fleet."""
    start = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start) * 1000)

    if dry_run:
        return PushResult(
            node.node_id, True,
            f"would push to {node.ssh_user}@{node.ssh_host}:{node.ssh_port}",
            "", _elapsed_ms(),
        )

    # Read the index ONCE on the control. Pushing N nodes shouldn't
    # re-read the file N times — saves IO + keeps every node's payload
    # identical even if the file changes mid-sync (rare but possible).
    try:
        with open(users_index_path, "rb") as f:
            payload = f.read()
    except OSError as exc:
        return PushResult(
            node.node_id, False, "",
            f"could not read {users_index_path}: {exc}",
            _elapsed_ms(),
        )

    # Sanity: refuse to push a non-JSON file. A torn index would render
    # the data node's xray.service unable to start.
    try:
        json.loads(payload)
    except json.JSONDecodeError as exc:
        return PushResult(
            node.node_id, False, "",
            f"{users_index_path} is not valid JSON ({exc}); refusing to push",
            _elapsed_ms(),
        )

    cmd = [
        "ssh",
        "-i", node.ssh_key_path,
        "-p", str(node.ssh_port),
        "-o", "BatchMode=yes",           # no password prompt
        "-o", "ConnectTimeout=10",        # fail fast on dead nodes
        "-o", "StrictHostKeyChecking=accept-new",  # TOFU for first connect
        f"{node.ssh_user}@{node.ssh_host}",
        # The restricted-key command= prefix on the data node IGNORES
        # this argument and runs `s-vps fleet-receive` instead. We
        # pass it anyway for dev bootstraps that use the operator's
        # personal SSH key (no command= restriction).
        f"/usr/local/bin/s-vps {HIDDEN_RECEIVE_SUBCMD}",
    ]

    try:
        result = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return PushResult(
            node.node_id, False, "",
            f"ssh push timed out after {timeout}s",
            _elapsed_ms(),
        )
    except OSError as exc:
        return PushResult(
            node.node_id, False, "",
            f"could not exec ssh: {exc}",
            _elapsed_ms(),
        )

    ok = (result.returncode == 0)
    return PushResult(
        node.node_id, ok,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
        _elapsed_ms(),
    )


def sync_all(
    nodes: Iterable[FleetNode],
    users_index_path: str,
    *,
    parallel: int = DEFAULT_PARALLEL,
    dry_run: bool = False,
    timeout: float = DEFAULT_SYNC_TIMEOUT,
) -> list[PushResult]:
    """Push the index to every node in parallel. Returns results sorted
    by node_id so output tables are stable across runs (Python's dict
    insertion order is stable, but the operator's mental model of
    "tokyo-1 comes before amsterdam-1 because I added it first" isn't
    reliable — alphabetic is the contract)."""
    nodes_list = list(nodes)
    if not nodes_list:
        return []

    # Bound parallelism to reasonable values — 1 means sequential, very
    # large values just spawn idle threads waiting for ssh handshakes.
    parallel = max(1, min(parallel, len(nodes_list)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = {
            ex.submit(
                push_to_node, node, users_index_path,
                timeout=timeout, dry_run=dry_run,
            ): node
            for node in nodes_list
        }
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    results.sort(key=lambda r: r.node_id)
    return results
