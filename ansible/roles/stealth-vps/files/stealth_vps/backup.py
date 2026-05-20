"""Encrypted backup + restore for stealth-vps (v0.9.0+).

Bundles the operator-visible state into a tarball, encrypts it with
`age` to the operator's public key, and writes the result to a chosen
directory (default `/var/backups/stealth-vps/`). Restore reverses the
operation: decrypt with the operator's private key, untar over the
target, optionally re-apply via `s-vps reload`.

Design choices (per ADR — see docs/security.md):

  - `age` (not GPG): single-binary, no keyring, no agent. The operator
    holds a 1-line identity file; the role only needs the public
    recipient (which is fine to commit to inventory).
  - **Pubkey-only** on the box. The host never holds the private key
    — backups are encrypted-to-recipient, never decrypted on the box
    they were produced on. Operators who want to test a restore on the
    same box must temporarily provide the identity (env var or stdin).
  - What's in scope:
      /etc/stealth-vps/        (state.yml files, installer.env, version,
                                reloader-args.json — operator-set things)
      /var/lib/stealth-vps/    (users.index.json, subscriptions/)
    What's NOT:
      /usr/local/{bin,lib}/    (idempotent reinstall via ansible)
      /etc/systemd/system/*    (rendered by ansible)
      Caddy / Xray / Hysteria binaries (apt/upstream)
  - Backup format: `stealth-vps-backup-<YYYYMMDDTHHMMSSZ>-<hostname>.tar.age`.
    The age envelope is the integrity check; no separate signature.

Stdlib only (tarfile, subprocess for age). The role's bootstrap apt-
installs `age` (the only new dep this feature adds)."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Iterable

log = logging.getLogger("stealth_vps.backup")


# ---- Paths the role pins ---------------------------------------------------

# What we include. Each path is relative to /, tar'd preserving structure
# so restore is a single `tar -xf` over /. Symlinks resolve to their
# targets; we don't snapshot the link.
DEFAULT_BACKUP_INCLUDES = (
    "/etc/stealth-vps",
    "/var/lib/stealth-vps",
)

# Where to write the encrypted tarball. The role creates this with mode
# 0700 in tasks/backup.yml.
DEFAULT_BACKUP_DIR = "/var/backups/stealth-vps"

# `age` binary. Pulled from apt on Debian 12+ (it's in main since
# Bookworm); the role installs it in tasks/backup.yml.
DEFAULT_AGE_BIN = "/usr/bin/age"


class BackupError(Exception):
    """All backup/restore failures raise this so CLI / systemd can map
    them to clean exit codes + journal lines."""


# ---------------------------------------------------------------------------
# Plain tar — small wrapper around tarfile for testability
# ---------------------------------------------------------------------------


def write_tarball(
    sources: Iterable[str],
    dest: str,
    *,
    skip_missing: bool = True,
) -> list[str]:
    """Bundle every existing path in `sources` into a tarball at `dest`.
    Returns the list of paths that actually went in (operators care for
    audit logs — "did /var/lib/stealth-vps/subscriptions get included?").

    `skip_missing=True` (default): paths that don't exist on the host are
    skipped with a log line. This is the right default for `s-vps backup`
    because /var/lib/stealth-vps/subscriptions only exists when the
    subscription endpoint is enabled — we don't want backup to fail on
    a panel-only deployment.

    `skip_missing=False` raises BackupError if any source is missing.
    Used by the test suite to assert the "everything's there" path."""
    included: list[str] = []
    with tarfile.open(dest, mode="w") as tar:
        for src in sources:
            if not os.path.exists(src):
                if skip_missing:
                    log.info("backup: skipping missing path %s", src)
                    continue
                raise BackupError(f"backup source {src} does not exist")
            # arcname='etc/stealth-vps' (strip the leading /) so untar
            # over `/` restores in place. Using `arcname=src.lstrip("/")`
            # keeps the structure identical to the live filesystem.
            tar.add(src, arcname=src.lstrip("/"), recursive=True)
            included.append(src)
    return included


def extract_tarball(archive_path: str, target_root: str = "/") -> list[str]:
    """Untar `archive_path` under `target_root`. Returns the list of
    member names so operators see what was restored.

    Path-traversal defense: tarfile's `data` filter (Python 3.12+) rejects
    member names containing `..` or absolute paths. We fall back to manual
    filtering on older Pythons (the role's bootstrap pins 3.11+, so this
    path stays warm). The role's archives only ever contain paths under
    `etc/stealth-vps` and `var/lib/stealth-vps`, but a malicious or
    corrupted archive must not be able to write outside that."""
    members: list[str] = []
    with tarfile.open(archive_path, mode="r") as tar:
        for m in tar.getmembers():
            if m.name.startswith("/") or ".." in pathlib.PurePosixPath(m.name).parts:
                raise BackupError(
                    f"refusing to extract unsafe path {m.name!r} from {archive_path}"
                )
            members.append(m.name)
        # `data` filter (3.12+) re-checks safety. Older Pythons fall back
        # to the default ("fully_trusted") filter — we did the path scan
        # above, so this is defence-in-depth, not the primary check.
        try:
            tar.extractall(path=target_root, filter="data")
        except TypeError:
            tar.extractall(path=target_root)
    return members


# ---------------------------------------------------------------------------
# age — encrypt / decrypt via subprocess
# ---------------------------------------------------------------------------


def encrypt_with_age(
    plaintext_path: str,
    ciphertext_path: str,
    recipient: str,
    *,
    age_bin: str = DEFAULT_AGE_BIN,
) -> None:
    """`age -r <recipient> -o <out> <in>`. `recipient` is a single
    `age1...` string (or `ssh-ed25519 ...`, which `age` also accepts).
    Multi-recipient backups (CTO + ops shared key) aren't supported
    here — operators wanting that wrap the call manually."""
    if not os.path.exists(age_bin):
        raise BackupError(
            f"`age` binary not found at {age_bin}. Install with "
            f"`apt install age` or re-run ansible — tasks/backup.yml "
            f"handles the install when stealth_vps_backup_enabled=true."
        )
    if not recipient.strip():
        raise BackupError(
            "no age recipient configured. Set "
            "STEALTH_VPS_BACKUP_RECIPIENT (operator's `age1...` pubkey) "
            "or pass --recipient."
        )
    cmd = [age_bin, "-r", recipient.strip(), "-o", ciphertext_path, plaintext_path]
    log.info("encrypting: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise BackupError(f"could not exec age: {exc}") from exc
    if result.returncode != 0:
        raise BackupError(
            f"age encrypt failed (exit {result.returncode}): "
            f"{result.stderr.strip() or '(no stderr)'}"
        )


def decrypt_with_age(
    ciphertext_path: str,
    plaintext_path: str,
    identity_path: str,
    *,
    age_bin: str = DEFAULT_AGE_BIN,
) -> None:
    """`age -d -i <identity> -o <out> <in>`. `identity_path` is the
    operator's secret key file (a single line beginning `AGE-SECRET-KEY-`).
    The role NEVER stores this — operators pass it via --identity on
    restore, or stream it on stdin (not implemented here; trivial
    follow-up if requested)."""
    if not os.path.exists(age_bin):
        raise BackupError(f"`age` binary not found at {age_bin}.")
    if not os.path.exists(identity_path):
        raise BackupError(
            f"identity file not found at {identity_path}. The operator's "
            f"`age` private key (one line beginning `AGE-SECRET-KEY-`) "
            f"must be available locally for restore."
        )
    cmd = [age_bin, "-d", "-i", identity_path, "-o", plaintext_path, ciphertext_path]
    log.info("decrypting: %s -o %s %s", age_bin, plaintext_path, ciphertext_path)
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise BackupError(f"could not exec age: {exc}") from exc
    if result.returncode != 0:
        raise BackupError(
            f"age decrypt failed (exit {result.returncode}): "
            f"{result.stderr.strip() or '(no stderr)'}. Wrong identity?"
        )


# ---------------------------------------------------------------------------
# Top-level backup / restore — combine tar + age
# ---------------------------------------------------------------------------


def _timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


@dataclasses.dataclass
class BackupResult:
    output_path: str
    included_paths: list[str]
    bytes_written: int


def perform_backup(
    *,
    recipient: str,
    output_dir: str = DEFAULT_BACKUP_DIR,
    sources: Iterable[str] = DEFAULT_BACKUP_INCLUDES,
    age_bin: str = DEFAULT_AGE_BIN,
    hostname: str | None = None,
) -> BackupResult:
    """Tar `sources` → encrypt with `age` → drop the .tar.age in
    `output_dir`. Returns a BackupResult for the CLI to print.

    Filename pattern: `stealth-vps-backup-<ts>-<host>.tar.age`. Includes
    the hostname so a single S3 prefix can hold backups from many hosts
    without collisions."""
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    host = hostname or socket.gethostname() or "unknown"
    ts = _timestamp()
    out_name = f"stealth-vps-backup-{ts}-{host}.tar.age"
    out_path = os.path.join(output_dir, out_name)

    with tempfile.TemporaryDirectory(prefix="stealth-vps-backup-") as tmp:
        plain = os.path.join(tmp, "bundle.tar")
        included = write_tarball(list(sources), plain)
        encrypt_with_age(plain, out_path, recipient, age_bin=age_bin)
    bytes_written = os.path.getsize(out_path)
    # The encrypted file holds no plaintext-derivable secrets but operator
    # convention is "backups are 0600" — mirror that.
    os.chmod(out_path, 0o600)
    log.info("backup written: %s (%d bytes, %d sources)", out_path, bytes_written, len(included))
    return BackupResult(out_path, included, bytes_written)


def perform_restore(
    *,
    archive_path: str,
    identity_path: str,
    target_root: str = "/",
    age_bin: str = DEFAULT_AGE_BIN,
) -> list[str]:
    """Decrypt `archive_path` with `identity_path`, then untar under
    `target_root`. Returns the list of restored member names.

    `target_root` defaults to "/" (the standard "restore over a freshly
    converged host" workflow). Tests use a tmp dir."""
    if not os.path.exists(archive_path):
        raise BackupError(f"archive not found: {archive_path}")
    with tempfile.TemporaryDirectory(prefix="stealth-vps-restore-") as tmp:
        plain = os.path.join(tmp, "bundle.tar")
        decrypt_with_age(archive_path, plain, identity_path, age_bin=age_bin)
        members = extract_tarball(plain, target_root=target_root)
    log.info("restored %d members under %s", len(members), target_root)
    return members


# ---------------------------------------------------------------------------
# CLI entry points (wired by cli.py)
# ---------------------------------------------------------------------------


def cmd_backup(args: argparse.Namespace) -> int:
    recipient = (
        args.recipient
        or os.environ.get("STEALTH_VPS_BACKUP_RECIPIENT", "")
    )
    try:
        result = perform_backup(
            recipient=recipient,
            output_dir=args.output_dir,
            sources=args.sources or list(DEFAULT_BACKUP_INCLUDES),
        )
    except BackupError as exc:
        print(f"s-vps backup: {exc}", file=sys.stderr)
        return 1
    print(f"✓ backup complete: {result.output_path}")
    print(f"  size              : {result.bytes_written} bytes")
    print(f"  included paths    : {', '.join(result.included_paths) or '(none)'}")
    print()
    print("Copy the file off-host:")
    print(f"  scp root@<host>:{result.output_path} ./")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    try:
        members = perform_restore(
            archive_path=args.archive,
            identity_path=args.identity,
            target_root=args.target_root,
        )
    except BackupError as exc:
        print(f"s-vps restore: {exc}", file=sys.stderr)
        return 1
    print(f"✓ restored {len(members)} files from {args.archive}")
    if args.target_root == "/":
        print()
        print("Next step: run `s-vps reload` to re-apply the restored")
        print("state to Xray + Hysteria2 (skip for panel-mode hosts —")
        print("they pick up the index next time the panel reconciles).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stealth-vps-backup",
        description="encrypted backup/restore for stealth-vps operator state",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("backup", help="snapshot operator state to an age-encrypted tarball")
    p.add_argument(
        "--recipient",
        default="",
        help="operator's age recipient (`age1...` or `ssh-ed25519 ...`). "
             "Defaults to STEALTH_VPS_BACKUP_RECIPIENT env var.",
    )
    p.add_argument(
        "--output-dir",
        default=DEFAULT_BACKUP_DIR,
        help="directory for the .tar.age (default %(default)s).",
    )
    p.add_argument(
        "--source",
        dest="sources",
        action="append",
        default=None,
        help="extra path to include in the backup (repeat for multiple). "
             "Defaults to /etc/stealth-vps + /var/lib/stealth-vps.",
    )
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="decrypt + untar a .tar.age back over the live filesystem")
    p.add_argument("archive", help="path to the .tar.age to restore")
    p.add_argument(
        "--identity",
        required=True,
        help="path to the operator's age identity file (one-line `AGE-SECRET-KEY-...`).",
    )
    p.add_argument(
        "--target-root",
        default="/",
        help="prefix to extract under (tests pass a tmp dir; default %(default)s).",
    )
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
