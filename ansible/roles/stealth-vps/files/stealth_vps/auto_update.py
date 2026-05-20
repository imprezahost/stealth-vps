"""Opt-in auto-update for stealth-vps (v0.9.0+).

Polls GitHub Releases for the latest tag, decides whether to roll over
based on the operator's policy, and (when triggered) shells out to
`s-vps update <tag>`. Designed to be called from a systemd timer:

    [Service]
    ExecStart=/usr/local/bin/python3 -m stealth_vps.auto_update

Default policy is *patch-only*: a host pinned at `v0.9.0` will accept
`v0.9.1`, `v0.9.2`, etc., but never `v0.10.0`. Operators get bug fixes
+ security patches automatically while explicitly opting in to feature
releases (which may carry breaking schema changes). This matches what
"automatic updates" mean in practice for things like fail2ban, certbot,
or unattended-upgrades — the security model assumes the major.minor
boundary marks a deliberate-review cutoff.

Why a bespoke module instead of unattended-upgrades:
  - We're updating ansible-pull-managed state, not an apt package.
  - We pin tags, not floating versions — the existing `s-vps update`
    flow accepts an explicit tag and we want to drive it programmatically.
  - The fleet-of-VPS use case wants jittered rollout (don't hammer the
    GitHub API at the top of every hour from 100 boxes), which is
    cleaner to express in our own timer config than to bolt onto apt.

Stdlib only (urllib, json, subprocess). The role already ships a
Python interpreter; we don't want a second dep just for HTTP."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Iterable

log = logging.getLogger("stealth_vps.auto_update")

# GitHub's per-IP unauthenticated rate limit is 60 req/hr. The timer fires
# once a day by default, so a busy fleet (100+ hosts behind the same NAT
# gateway) is the only realistic way to hit it. Operators in that case
# can supply STEALTH_VPS_GITHUB_TOKEN to upgrade to 5000 req/hr.
DEFAULT_RELEASES_URL = "https://api.github.com/repos/imprezahost/stealth-vps/releases/latest"
DEFAULT_VERSION_FILE = "/etc/stealth-vps/version"
DEFAULT_S_VPS_BIN = "/usr/local/bin/s-vps"


# ---------------------------------------------------------------------------
# Semver — minimal MAJOR.MINOR.PATCH parsing, enough for "is this a patch?"
# ---------------------------------------------------------------------------

# Strict: `v0.9.0`, `v10.0.42`. We don't accept pre-release suffixes
# (`-rc.1`) here because the role's release pipeline never tags them.
# If/when that changes, extend this regex + parse the suffix.
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclasses.dataclass(frozen=True, order=True)
class Version:
    """Sortable MAJOR.MINOR.PATCH triple. Sort is by tuple order (the
    dataclass order=True default), so `Version(0,9,0) < Version(0,9,1)`
    and `Version(0,9,9) < Version(0,10,0)` — exactly the semver ordering.
    """

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"


class AutoUpdateError(Exception):
    """All non-actionable failures (network, parse, exec) raise this so
    the timer's stderr captures a one-line diagnosis. Operators read
    `journalctl -u stealth-vps-auto-update.service` to triage."""


def parse_version(text: str) -> Version:
    """Lift `"v0.9.0"` (or `"0.9.0"`) into a Version. Raises
    AutoUpdateError on anything else — including `"local"` and `"unknown"`,
    which `/etc/stealth-vps/version` carries when ansible-pull hasn't
    pinned a tag yet. The auto-updater refuses to act in those cases
    (we can't compute "next patch from local")."""
    m = _SEMVER_RE.match(text.strip())
    if not m:
        raise AutoUpdateError(
            f"version {text!r} is not vMAJOR.MINOR.PATCH — refusing to "
            f"auto-update. Set `/etc/stealth-vps/version` to a real tag "
            f"first (e.g. `echo v0.9.0 > /etc/stealth-vps/version`)."
        )
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_patch_update(current: Version, candidate: Version) -> bool:
    """True iff `candidate` is a strictly-newer patch within the same
    MAJOR.MINOR series. Used by the default `patch-only` policy."""
    if candidate.major != current.major or candidate.minor != current.minor:
        return False
    return candidate.patch > current.patch


def is_minor_or_patch_update(current: Version, candidate: Version) -> bool:
    """True iff `candidate` is a strictly-newer release within the same
    MAJOR series. Used by the `minor-patch` policy (operators who want
    minor bumps too — accept v0.9.0 → v0.10.0 but refuse v0.9.0 → v1.0.0).
    """
    if candidate.major != current.major:
        return False
    return candidate > current


# ---------------------------------------------------------------------------
# GitHub Releases API — fetch the latest tag
# ---------------------------------------------------------------------------


def fetch_latest_release_tag(
    url: str = DEFAULT_RELEASES_URL,
    *,
    timeout: float = 15.0,
    github_token: str | None = None,
) -> str:
    """Return the `tag_name` from /releases/latest. Bare urllib — no
    requests dep. Raises AutoUpdateError on any network / parse failure
    so the caller can log + skip without crashing the systemd unit.

    `github_token` (optional) is added as `Authorization: Bearer <token>`,
    bumping the per-IP rate limit from 60/hr to 5000/hr. Operator-fleet
    deployments behind a single egress IP should set
    STEALTH_VPS_GITHUB_TOKEN — the auto-update unit reads it from
    /etc/stealth-vps/auto-update.env (rendered by the role)."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "stealth-vps-auto-update",
    })
    if github_token:
        req.add_header("Authorization", f"Bearer {github_token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise AutoUpdateError(
            f"GitHub API returned HTTP {exc.code}: {exc.reason}. "
            f"(Rate-limited? Set STEALTH_VPS_GITHUB_TOKEN to authenticate.)"
        ) from exc
    except urllib.error.URLError as exc:
        raise AutoUpdateError(f"network error fetching {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AutoUpdateError(f"GitHub API response was not JSON: {exc}") from exc

    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        raise AutoUpdateError(
            f"GitHub API response missing `tag_name`: {data!r}"
        )
    return tag.strip()


# ---------------------------------------------------------------------------
# Decision: should we update?
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AutoUpdateDecision:
    """The outcome of one auto-update cycle. `should_update` drives the
    subprocess call; the rest is for logging."""

    should_update: bool
    current: Version
    candidate: Version
    reason: str

    def summary(self) -> str:
        verb = "would update" if self.should_update else "skipping"
        return f"{verb} ({self.current} → {self.candidate}): {self.reason}"


def decide(
    current: Version,
    candidate: Version,
    *,
    policy: str = "patch-only",
) -> AutoUpdateDecision:
    """Apply the operator's policy and report what to do. Pure function
    so it tests easily — the side-effectful caller does the
    `subprocess.run(["s-vps", "update", ...])`.

    Policies:
      - `patch-only` (default): only roll over within MAJOR.MINOR.
      - `minor-patch`: also accept MAJOR.X.Y rollovers (still refuses
        major-version bumps).
      - `disabled`: never update. The role lets operators set this from
        defaults so the timer can stay enabled while temporarily holding
        the host on its current tag (e.g. during a maintenance freeze).
    """
    if policy == "disabled":
        return AutoUpdateDecision(False, current, candidate, "policy=disabled")
    if candidate <= current:
        return AutoUpdateDecision(
            False, current, candidate,
            f"candidate {candidate} is not newer than current {current}",
        )
    if policy == "patch-only":
        if is_patch_update(current, candidate):
            return AutoUpdateDecision(
                True, current, candidate,
                f"patch update within {current.major}.{current.minor}.x",
            )
        return AutoUpdateDecision(
            False, current, candidate,
            f"refusing minor/major bump under patch-only policy "
            f"(set policy=minor-patch to accept {current.major}.X.Y, "
            f"or run `s-vps update {candidate}` manually).",
        )
    if policy == "minor-patch":
        if is_minor_or_patch_update(current, candidate):
            return AutoUpdateDecision(
                True, current, candidate,
                f"minor/patch update within {current.major}.x.x",
            )
        return AutoUpdateDecision(
            False, current, candidate,
            f"refusing major bump under minor-patch policy "
            f"(major releases require explicit `s-vps update {candidate}`).",
        )
    raise AutoUpdateError(
        f"unknown policy {policy!r}; expected one of: "
        f"patch-only, minor-patch, disabled"
    )


# ---------------------------------------------------------------------------
# Side-effect: shell out to `s-vps update <tag>`
# ---------------------------------------------------------------------------


def run_update(target_tag: str, *, s_vps_bin: str = DEFAULT_S_VPS_BIN) -> int:
    """Re-use the existing `s-vps update <tag>` flow. Returns the exit
    code so the systemd unit propagates failures (operator sees a red
    Failed= in `systemctl list-timers`).

    We deliberately don't reproduce the ansible-pull invocation here:
    the bash wrapper at /usr/local/bin/s-vps is the single source of
    truth for what gets passed to ansible-pull, including secret
    handling for the bot token. Calling through it keeps auto-update
    behaviourally identical to a manual `s-vps update <tag>`."""
    if not os.path.exists(s_vps_bin):
        raise AutoUpdateError(
            f"`s-vps` binary not found at {s_vps_bin}; "
            f"is the role installed? Try `ansible-pull` or "
            f"re-run the install.sh bootstrap."
        )
    log.info("invoking %s update %s", s_vps_bin, target_tag)
    try:
        result = subprocess.run(
            [s_vps_bin, "update", target_tag],
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except OSError as exc:
        raise AutoUpdateError(f"could not exec {s_vps_bin}: {exc}") from exc
    return result.returncode


# ---------------------------------------------------------------------------
# Main entrypoint — `python3 -m stealth_vps.auto_update`
# ---------------------------------------------------------------------------


def _read_pinned_version(path: str = DEFAULT_VERSION_FILE) -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise AutoUpdateError(
            f"version file {path} is missing — has the role finished its "
            f"first converge? `s-vps update <tag>` writes it."
        ) from exc


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealth-vps-auto-update",
        description="Poll GitHub Releases + roll over to a newer tag per policy.",
    )
    parser.add_argument(
        "--policy",
        default=os.environ.get("STEALTH_VPS_AUTO_UPDATE_POLICY", "patch-only"),
        choices=("patch-only", "minor-patch", "disabled"),
        help="update policy (env: STEALTH_VPS_AUTO_UPDATE_POLICY).",
    )
    parser.add_argument(
        "--releases-url",
        default=os.environ.get("STEALTH_VPS_RELEASES_URL", DEFAULT_RELEASES_URL),
        help="GitHub releases API URL (test/fork override).",
    )
    parser.add_argument(
        "--version-file",
        default=DEFAULT_VERSION_FILE,
        help="path to the pinned-version file (default /etc/stealth-vps/version).",
    )
    parser.add_argument(
        "--s-vps-bin",
        default=DEFAULT_S_VPS_BIN,
        help="path to the s-vps wrapper (default /usr/local/bin/s-vps).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the decision but skip the actual `s-vps update` call.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    github_token = os.environ.get("STEALTH_VPS_GITHUB_TOKEN") or None

    try:
        current = parse_version(_read_pinned_version(args.version_file))
        candidate_tag = fetch_latest_release_tag(
            args.releases_url, github_token=github_token,
        )
        candidate = parse_version(candidate_tag)
    except AutoUpdateError as exc:
        log.error("%s", exc)
        return 2

    decision = decide(current, candidate, policy=args.policy)
    log.info("%s", decision.summary())

    if not decision.should_update:
        return 0
    if args.dry_run:
        log.info("dry-run — would have executed `s-vps update %s`", candidate)
        return 0

    try:
        return run_update(str(candidate), s_vps_bin=args.s_vps_bin)
    except AutoUpdateError as exc:
        log.error("update failed: %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
