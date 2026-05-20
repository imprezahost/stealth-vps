"""Tests for stealth_vps.fleet — multi-node control plane primitives.

The module is pure-stdlib + shells out to ssh. We mock subprocess.run
for push tests (no real SSH involved), exercise the YAML parser/emitter
via round-trips, and verify validate_node_id catches the obvious
failure modes.

`sync_all` parallelism is asserted with a real ThreadPoolExecutor +
fake `push_to_node` that sleeps — confirms the wall-clock is bounded
by max(per-node) rather than sum().
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from stealth_vps import fleet


# ---------------------------------------------------------------------------
# validate_node_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_id",
    [
        "tokyo-1",
        "amsterdam-eu",
        "node5",
        "a",
        "0",
        "a" * 32,
        "x-y-z-1-2-3",
    ],
)
def test_validate_node_id_accepts(node_id: str) -> None:
    fleet.validate_node_id(node_id)   # no raise


@pytest.mark.parametrize(
    "node_id",
    [
        "",
        "a" * 33,             # too long
        "-leading-dash",
        "Tokyo-1",            # uppercase
        "tokyo_1",            # underscore
        "tokyo 1",            # space
        "tokyo.1",            # dot
        "ütf",                # non-ascii
        "  spaced  ",
    ],
)
def test_validate_node_id_rejects(node_id: str) -> None:
    with pytest.raises(fleet.FleetError, match="invalid"):
        fleet.validate_node_id(node_id)


# ---------------------------------------------------------------------------
# _parse_scalar — corner cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("hello", "hello"),
        ('"hello world"', "hello world"),
        ("'hello world'", "hello world"),
        ("42", 42),
        ("0", 0),
        ("true", True),
        ("True", True),
        ("false", False),
        ("FALSE", False),
        ("null", None),
        ("~", None),
        # Strings that look numeric-ish but aren't pure ints stay as str.
        ("42abc", "42abc"),
        ("v0.9.0", "v0.9.0"),
        # Already-quoted preserved.
        ('"42"', "42"),
    ],
)
def test_parse_scalar(value: str, expected) -> None:
    assert fleet._parse_scalar(value) == expected


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


def test_yaml_round_trip_full_node() -> None:
    """Save a complete node, parse the emitted YAML, reload — every
    field round-trips. This is the contract the on-disk format must
    honour across role upgrades."""
    node = fleet.FleetNode(
        node_id="tokyo-1",
        ssh_host="103.106.228.154",
        ssh_port=22,
        ssh_user="root",
        ssh_key_path="/etc/stealth-vps/keys/control_to_tokyo-1.ed25519",
        reality_public_key="3ajziTLzJKIN8YNUWnpl2Yli14HBIGdvnhHm6gpbM24",
        reality_short_id="04285c7f",
        reality_port=43338,
        reality_servernames=["www.microsoft.com", "www.apple.com"],
        hysteria_port=49440,
        hysteria_obfs_password="Rf9YLFwKMljt1KD20I0hBUhRIUP9juqV",
        public_host="tokyo.example.com",
        domain="tokyo.example.com",
        added_at="2026-05-21T10:00:00Z",
        last_sync_at="2026-05-21T10:05:00Z",
        last_sync_status="ok",
    )
    text = fleet._emit_node_yaml(node.to_dict())
    parsed = fleet._parse_node_yaml(text)
    rebuilt = fleet.FleetNode.from_dict(parsed)
    assert rebuilt == node


def test_yaml_round_trip_with_null_public_host() -> None:
    """When `public_host` is None the YAML must encode it as `null` so
    the parser can distinguish "operator didn't set it" (None) from
    "operator set empty string" (empty)."""
    node = fleet.FleetNode(
        node_id="node1",
        ssh_host="10.0.0.1",
        public_host=None,
    )
    text = fleet._emit_node_yaml(node.to_dict())
    assert "public_host: null" in text
    parsed = fleet._parse_node_yaml(text)
    assert parsed["public_host"] is None


def test_yaml_round_trip_empty_servernames() -> None:
    node = fleet.FleetNode(node_id="n1", ssh_host="h", reality_servernames=[])
    text = fleet._emit_node_yaml(node.to_dict())
    # Empty list still emits the key with `:` followed by nothing.
    assert "reality_servernames:" in text
    parsed = fleet._parse_node_yaml(text)
    assert parsed["reality_servernames"] == []


def test_parse_node_yaml_rejects_unparseable_line() -> None:
    bad = "node_id: tokyo-1\nthis-line-has-no-colon\n"
    with pytest.raises(fleet.FleetError, match="missing"):
        fleet._parse_node_yaml(bad)


def test_parse_node_yaml_rejects_orphan_list_item() -> None:
    bad = "  - item-without-parent-key\n"
    with pytest.raises(fleet.FleetError, match="list item"):
        fleet._parse_node_yaml(bad)


def test_parse_node_yaml_skips_comments_and_blanks() -> None:
    text = (
        "# top comment\n"
        "\n"
        "node_id: t1\n"
        "  # this isn't actually a comment under a key — strict YAML\n"
        "ssh_host: 10.0.0.1\n"
    )
    # The "  # this isn't" line starts with two spaces but starts with
    # # after lstrip, so the parser treats it as a comment.
    parsed = fleet._parse_node_yaml(text)
    assert parsed["node_id"] == "t1"
    assert parsed["ssh_host"] == "10.0.0.1"


def test_emit_scalar_quotes_strings() -> None:
    """Catches the YAML 1.1 surprises (yes/no/on/off as bool, etc).
    Operator naming a node `yes` shouldn't have it get parsed as True
    by a downstream tool."""
    assert fleet._emit_scalar("yes") == '"yes"'
    assert fleet._emit_scalar("42") == '"42"'
    assert fleet._emit_scalar("hello world") == '"hello world"'


def test_emit_node_yaml_unsupported_type_raises() -> None:
    with pytest.raises(fleet.FleetError, match="unsupported"):
        fleet._emit_node_yaml({"bad": object()})


# ---------------------------------------------------------------------------
# FleetNode.from_dict — forward/backward compat
# ---------------------------------------------------------------------------


def test_from_dict_drops_unknown_fields() -> None:
    """Forward compat: v0.10.0 reads a file written by a hypothetical
    v0.10.x that added `extra_field`. The unknown key is dropped, the
    rest loads cleanly."""
    data = {
        "node_id": "n1",
        "ssh_host": "10.0.0.1",
        "extra_field": "future-feature",
    }
    node = fleet.FleetNode.from_dict(data)
    assert node.node_id == "n1"


def test_from_dict_requires_node_id_and_ssh_host() -> None:
    with pytest.raises(fleet.FleetError, match="node_id"):
        fleet.FleetNode.from_dict({"ssh_host": "x"})
    with pytest.raises(fleet.FleetError, match="ssh_host"):
        fleet.FleetNode.from_dict({"node_id": "n1"})


def test_from_dict_coerces_null_servernames_to_empty_list() -> None:
    data = {"node_id": "n1", "ssh_host": "h", "reality_servernames": None}
    node = fleet.FleetNode.from_dict(data)
    assert node.reality_servernames == []


def test_public_endpoint_falls_back_to_ssh_host() -> None:
    n = fleet.FleetNode(node_id="n1", ssh_host="10.0.0.1", public_host=None)
    assert n.public_endpoint == "10.0.0.1"
    n.public_host = "node.example.com"
    assert n.public_endpoint == "node.example.com"


# ---------------------------------------------------------------------------
# save_node / load_node / remove_node
# ---------------------------------------------------------------------------


def test_save_node_creates_directory(tmp_path: pathlib.Path) -> None:
    fleet_dir = str(tmp_path / "fleet")   # not yet created
    node = fleet.FleetNode(node_id="n1", ssh_host="10.0.0.1", added_at="2026-05-21T10:00:00Z")
    fleet.save_node(node, fleet_dir=fleet_dir)
    assert os.path.isdir(fleet_dir)
    assert os.path.exists(os.path.join(fleet_dir, "n1.yml"))


def test_save_node_round_trip(tmp_path: pathlib.Path) -> None:
    fleet_dir = str(tmp_path)
    node = fleet.FleetNode(
        node_id="tokyo-1",
        ssh_host="103.106.228.154",
        ssh_port=22,
        ssh_user="root",
        reality_servernames=["www.microsoft.com"],
        added_at="2026-05-21T10:00:00Z",
    )
    fleet.save_node(node, fleet_dir=fleet_dir)
    reloaded = fleet.load_node("tokyo-1", fleet_dir=fleet_dir)
    assert reloaded == node


def test_save_node_uses_mode_0600(tmp_path: pathlib.Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX mode bits don't apply on Windows")
    node = fleet.FleetNode(node_id="n1", ssh_host="h")
    fleet.save_node(node, fleet_dir=str(tmp_path))
    mode = stat.S_IMODE(os.stat(tmp_path / "n1.yml").st_mode)
    assert mode == 0o600


def test_save_node_validates_id(tmp_path: pathlib.Path) -> None:
    bad = fleet.FleetNode(node_id="UPPER", ssh_host="h")
    with pytest.raises(fleet.FleetError, match="invalid"):
        fleet.save_node(bad, fleet_dir=str(tmp_path))


def test_load_node_missing_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(fleet.FleetError, match="not registered"):
        fleet.load_node("ghost", fleet_dir=str(tmp_path))


def test_remove_node_returns_true_when_deleted(tmp_path: pathlib.Path) -> None:
    node = fleet.FleetNode(node_id="n1", ssh_host="h")
    fleet.save_node(node, fleet_dir=str(tmp_path))
    assert fleet.remove_node("n1", fleet_dir=str(tmp_path)) is True
    assert not (tmp_path / "n1.yml").exists()


def test_remove_node_returns_false_when_absent(tmp_path: pathlib.Path) -> None:
    assert fleet.remove_node("ghost", fleet_dir=str(tmp_path)) is False


# ---------------------------------------------------------------------------
# load_fleet
# ---------------------------------------------------------------------------


def test_load_fleet_returns_empty_when_dir_missing(tmp_path: pathlib.Path) -> None:
    assert fleet.load_fleet(fleet_dir=str(tmp_path / "nope")) == []


def test_load_fleet_returns_empty_when_dir_present_but_empty(tmp_path: pathlib.Path) -> None:
    assert fleet.load_fleet(fleet_dir=str(tmp_path)) == []


def test_load_fleet_sorted_by_node_id(tmp_path: pathlib.Path) -> None:
    fleet_dir = str(tmp_path)
    for nid in ("tokyo-1", "amsterdam-1", "berlin-2"):
        fleet.save_node(
            fleet.FleetNode(node_id=nid, ssh_host=f"host-{nid}"),
            fleet_dir=fleet_dir,
        )
    loaded = fleet.load_fleet(fleet_dir=fleet_dir)
    assert [n.node_id for n in loaded] == ["amsterdam-1", "berlin-2", "tokyo-1"]


def test_load_fleet_skips_malformed_files(tmp_path: pathlib.Path) -> None:
    """One bad YAML doesn't fail the whole list — the operator can
    still see the good nodes and triage the bad one."""
    fleet.save_node(
        fleet.FleetNode(node_id="good", ssh_host="h"),
        fleet_dir=str(tmp_path),
    )
    (tmp_path / "bad.yml").write_text("not valid yaml: : :\n", encoding="utf-8")
    loaded = fleet.load_fleet(fleet_dir=str(tmp_path))
    assert [n.node_id for n in loaded] == ["good"]


def test_load_fleet_skips_dotfiles(tmp_path: pathlib.Path) -> None:
    """Atomic save uses a `.{node_id}.{rand}.tmp` sibling; a sync
    crashing mid-save would leave one of those behind. `load_fleet`
    must not try to parse them as node files."""
    (tmp_path / ".tokyo-1.123.tmp").write_text("garbage", encoding="utf-8")
    fleet.save_node(fleet.FleetNode(node_id="real", ssh_host="h"), fleet_dir=str(tmp_path))
    loaded = fleet.load_fleet(fleet_dir=str(tmp_path))
    assert [n.node_id for n in loaded] == ["real"]


# ---------------------------------------------------------------------------
# update_sync_status
# ---------------------------------------------------------------------------


def test_update_sync_status_writes_back(tmp_path: pathlib.Path) -> None:
    fleet.save_node(fleet.FleetNode(node_id="n1", ssh_host="h"), fleet_dir=str(tmp_path))
    fleet.update_sync_status("n1", status="ok", at="2026-05-21T11:00:00Z",
                             fleet_dir=str(tmp_path))
    reloaded = fleet.load_node("n1", fleet_dir=str(tmp_path))
    assert reloaded.last_sync_status == "ok"
    assert reloaded.last_sync_at == "2026-05-21T11:00:00Z"


def test_update_sync_status_rejects_bad_status(tmp_path: pathlib.Path) -> None:
    fleet.save_node(fleet.FleetNode(node_id="n1", ssh_host="h"), fleet_dir=str(tmp_path))
    with pytest.raises(fleet.FleetError, match="ok/failed/never"):
        fleet.update_sync_status("n1", status="hilarious", fleet_dir=str(tmp_path))


def test_update_sync_status_defaults_at_to_now(tmp_path: pathlib.Path) -> None:
    fleet.save_node(fleet.FleetNode(node_id="n1", ssh_host="h"), fleet_dir=str(tmp_path))
    fleet.update_sync_status("n1", status="ok", fleet_dir=str(tmp_path))
    reloaded = fleet.load_node("n1", fleet_dir=str(tmp_path))
    # Should be a parseable ISO timestamp.
    assert reloaded.last_sync_at is not None
    assert "T" in reloaded.last_sync_at
    assert reloaded.last_sync_at.endswith("Z")


# ---------------------------------------------------------------------------
# push_to_node
# ---------------------------------------------------------------------------


def _make_index(tmp_path: pathlib.Path, content: str | None = None) -> str:
    """Write a small valid users.index.json under tmp_path."""
    path = tmp_path / "users.index.json"
    if content is None:
        content = json.dumps({"version": 2, "users": {}})
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_push_to_node_dry_run_short_circuits(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    node = fleet.FleetNode(node_id="n1", ssh_host="10.0.0.1")
    with patch("subprocess.run") as spy:
        result = fleet.push_to_node(node, idx, dry_run=True)
    assert result.ok is True
    assert "would push" in result.stdout
    spy.assert_not_called()


def test_push_to_node_invokes_ssh_with_correct_argv(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    node = fleet.FleetNode(
        node_id="tokyo-1",
        ssh_host="103.106.228.154",
        ssh_port=2222,
        ssh_user="ops",
        ssh_key_path="/keys/control_to_tokyo-1.ed25519",
    )
    fake = MagicMock(returncode=0, stdout=b'{"ok": true}\n', stderr=b"")
    with patch("subprocess.run", return_value=fake) as spy:
        result = fleet.push_to_node(node, idx)
    assert result.ok is True
    args, kwargs = spy.call_args
    cmd = args[0]
    assert cmd[0] == "ssh"
    assert "-i" in cmd and "/keys/control_to_tokyo-1.ed25519" in cmd
    assert "-p" in cmd and "2222" in cmd
    assert "ops@103.106.228.154" in cmd
    assert any("fleet-receive" in p for p in cmd)
    # The payload was passed on stdin.
    assert kwargs["input"] is not None


def test_push_to_node_returns_failed_on_timeout(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    node = fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k")
    with patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=30)):
        result = fleet.push_to_node(node, idx, timeout=30)
    assert result.ok is False
    assert "timed out" in result.stderr


def test_push_to_node_returns_failed_on_nonzero_exit(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    node = fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k")
    fake = MagicMock(returncode=42, stdout=b"", stderr=b"permission denied")
    with patch("subprocess.run", return_value=fake):
        result = fleet.push_to_node(node, idx)
    assert result.ok is False
    assert "permission denied" in result.stderr


def test_push_to_node_handles_oserror_from_ssh(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    node = fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k")
    with patch("subprocess.run", side_effect=FileNotFoundError("ssh not found")):
        result = fleet.push_to_node(node, idx)
    assert result.ok is False
    assert "could not exec ssh" in result.stderr


def test_push_to_node_refuses_missing_index(tmp_path: pathlib.Path) -> None:
    node = fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k")
    with patch("subprocess.run") as spy:
        result = fleet.push_to_node(node, str(tmp_path / "missing.json"))
    assert result.ok is False
    assert "could not read" in result.stderr
    spy.assert_not_called()


def test_push_to_node_refuses_non_json_index(tmp_path: pathlib.Path) -> None:
    """Catches a torn-write or hand-edited index BEFORE the data node
    receives it. Cheap insurance: ssh handshake is 100x more expensive
    than json.loads on a 5KB file."""
    bad = _make_index(tmp_path, content="this is not json")
    node = fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k")
    with patch("subprocess.run") as spy:
        result = fleet.push_to_node(node, bad)
    assert result.ok is False
    assert "not valid JSON" in result.stderr
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# sync_all
# ---------------------------------------------------------------------------


def test_sync_all_empty_returns_empty() -> None:
    assert fleet.sync_all([], "/dev/null") == []


def test_sync_all_returns_sorted_results(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    nodes = [
        fleet.FleetNode(node_id="z-last", ssh_host="h", ssh_key_path="/k"),
        fleet.FleetNode(node_id="a-first", ssh_host="h", ssh_key_path="/k"),
        fleet.FleetNode(node_id="m-mid", ssh_host="h", ssh_key_path="/k"),
    ]
    with patch("stealth_vps.fleet.push_to_node",
               side_effect=lambda n, *a, **kw: fleet.PushResult(n.node_id, True, "", "", 1)):
        results = fleet.sync_all(nodes, idx)
    assert [r.node_id for r in results] == ["a-first", "m-mid", "z-last"]


def test_sync_all_runs_in_parallel(tmp_path: pathlib.Path) -> None:
    """4 nodes × 0.2s each should finish in <0.4s wall-clock with
    parallel=4 (vs ≥0.8s sequential). Pick a generous timing
    threshold so slow CI runners don't false-fail."""
    idx = _make_index(tmp_path)
    nodes = [
        fleet.FleetNode(node_id=f"n{i}", ssh_host="h", ssh_key_path="/k")
        for i in range(4)
    ]

    def slow_push(n, *a, **kw):
        time.sleep(0.2)
        return fleet.PushResult(n.node_id, True, "", "", 200)

    start = time.monotonic()
    with patch("stealth_vps.fleet.push_to_node", side_effect=slow_push):
        results = fleet.sync_all(nodes, idx, parallel=4)
    elapsed = time.monotonic() - start
    # Sequential would be ~0.8s. Parallel should be <0.5s with a
    # generous CI tolerance.
    assert elapsed < 0.5, f"sync_all took {elapsed:.2f}s — not parallel?"
    assert len(results) == 4
    assert all(r.ok for r in results)


def test_sync_all_clamps_parallel_to_node_count(tmp_path: pathlib.Path) -> None:
    """Asking for 100 workers on a 2-node fleet shouldn't spawn 100
    idle threads. Implementation detail but worth pinning."""
    idx = _make_index(tmp_path)
    nodes = [
        fleet.FleetNode(node_id="n1", ssh_host="h", ssh_key_path="/k"),
        fleet.FleetNode(node_id="n2", ssh_host="h", ssh_key_path="/k"),
    ]
    seen_workers: set[str] = set()
    lock = threading.Lock()

    def spy(n, *a, **kw):
        with lock:
            seen_workers.add(threading.current_thread().name)
        return fleet.PushResult(n.node_id, True, "", "", 0)

    with patch("stealth_vps.fleet.push_to_node", side_effect=spy):
        fleet.sync_all(nodes, idx, parallel=100)
    # At most 2 worker threads should have been created (one per node).
    # ThreadPoolExecutor reuses threads, so the bound is the worker
    # count, not "exactly 2."
    assert len(seen_workers) <= 2


def test_sync_all_propagates_per_node_failure(tmp_path: pathlib.Path) -> None:
    idx = _make_index(tmp_path)
    nodes = [
        fleet.FleetNode(node_id="ok-node", ssh_host="h", ssh_key_path="/k"),
        fleet.FleetNode(node_id="bad-node", ssh_host="h", ssh_key_path="/k"),
    ]

    def mixed(n, *a, **kw):
        if n.node_id == "bad-node":
            return fleet.PushResult(n.node_id, False, "", "boom", 100)
        return fleet.PushResult(n.node_id, True, "", "", 50)

    with patch("stealth_vps.fleet.push_to_node", side_effect=mixed):
        results = fleet.sync_all(nodes, idx)
    by_id = {r.node_id: r for r in results}
    assert by_id["ok-node"].ok is True
    assert by_id["bad-node"].ok is False
    assert "boom" in by_id["bad-node"].stderr
