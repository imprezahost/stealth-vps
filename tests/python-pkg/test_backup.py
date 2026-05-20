"""Tests for stealth_vps.backup — tar/encrypt round-trip + CLI plumbing.

We don't install real `age` in CI. The encryption / decryption stages
are exercised with a tiny shell-script stand-in that the tests drop
into tmp_path: an `age` that does `cp -- "$@"` after parsing the
matching flags. That tests our argv layout + error handling without
needing a real cryptographic implementation.

For the path-traversal tests we go in directly through `extract_tarball`
with a hand-crafted malicious archive — no need to involve `age` at all.
"""

from __future__ import annotations

import os
import pathlib
import stat
import tarfile

import pytest

from stealth_vps import backup


# ---------------------------------------------------------------------------
# Fake `age` binary helpers
# ---------------------------------------------------------------------------


def _drop_fake_age(tmp_path: pathlib.Path, *, behavior: str = "copy") -> pathlib.Path:
    """Write a tiny shell script that mimics `age` for the two flag
    forms we use (`-r ... -o OUT IN` and `-d -i ID -o OUT IN`).
    `behavior` controls what it does after parsing:
        "copy" → cp IN OUT (the default — round-trip works)
        "fail" → exit 1 with a known stderr message
    """
    path = tmp_path / "fake-age"
    if behavior == "copy":
        path.write_text(
            "#!/bin/sh\n"
            "out=''; inp=''\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    -o) out=\"$2\"; shift 2 ;;\n"
            "    -r|-i) shift 2 ;;\n"
            "    -d) shift ;;\n"
            "    *) inp=\"$1\"; shift ;;\n"
            "  esac\n"
            "done\n"
            "cp -- \"$inp\" \"$out\"\n",
            encoding="utf-8",
        )
    elif behavior == "fail":
        path.write_text(
            "#!/bin/sh\n"
            "echo 'fake-age: nope' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
    else:
        raise ValueError(behavior)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _skip_if_no_posix_shell() -> None:
    """Some test environments (Windows without WSL or bash) can't exec
    a `#!/bin/sh` script. Skip the affected tests instead of failing."""
    if os.name != "posix":
        pytest.skip("fake-age needs a POSIX shell; skipping on this OS")


# ---------------------------------------------------------------------------
# write_tarball / extract_tarball
# ---------------------------------------------------------------------------


def test_write_tarball_skips_missing_by_default(tmp_path: pathlib.Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "hello.txt").write_text("hi", encoding="utf-8")
    out = tmp_path / "out.tar"

    included = backup.write_tarball([str(real), str(tmp_path / "missing")], str(out))
    assert included == [str(real)]
    with tarfile.open(out) as tf:
        names = tf.getnames()
        assert any(n.endswith("hello.txt") for n in names)


def test_write_tarball_strict_raises_on_missing(tmp_path: pathlib.Path) -> None:
    with pytest.raises(backup.BackupError, match="does not exist"):
        backup.write_tarball(
            [str(tmp_path / "missing")], str(tmp_path / "out.tar"),
            skip_missing=False,
        )


def test_extract_tarball_round_trip(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha", encoding="utf-8")
    archive = tmp_path / "bundle.tar"
    backup.write_tarball([str(src)], str(archive))

    target = tmp_path / "restored"
    target.mkdir()
    members = backup.extract_tarball(str(archive), target_root=str(target))
    assert any(m.endswith("a.txt") for m in members)
    assert (target / str(src).lstrip("/") / "a.txt").read_text() == "alpha" \
        or any(
            (target / pathlib.Path(m)).read_text() == "alpha"
            for m in members if m.endswith("a.txt")
        )


def test_extract_tarball_refuses_path_traversal(tmp_path: pathlib.Path) -> None:
    """A malicious archive with a `../escape.txt` member must be refused
    before any file is written."""
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as tf:
        # Build the member entirely in-memory so we don't have to write
        # the traversing file to disk first.
        info = tarfile.TarInfo(name="../escape.txt")
        data = b"pwned"
        info.size = len(data)
        import io
        tf.addfile(info, io.BytesIO(data))

    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(backup.BackupError, match="unsafe path"):
        backup.extract_tarball(str(bad), target_root=str(target))
    # The escape file must not exist anywhere under tmp_path.
    assert not (tmp_path / "escape.txt").exists()


def test_extract_tarball_refuses_absolute_paths(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "bad-abs.tar"
    with tarfile.open(bad, "w") as tf:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 0
        import io
        tf.addfile(info, io.BytesIO(b""))
    with pytest.raises(backup.BackupError, match="unsafe path"):
        backup.extract_tarball(str(bad), target_root=str(tmp_path))


# ---------------------------------------------------------------------------
# encrypt_with_age / decrypt_with_age — using the fake-age script
# ---------------------------------------------------------------------------


def test_encrypt_with_age_invokes_binary_with_recipient(tmp_path: pathlib.Path) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)
    src = tmp_path / "in.bin"
    src.write_bytes(b"hello world")
    dst = tmp_path / "out.age"
    backup.encrypt_with_age(str(src), str(dst), "age1example...", age_bin=str(age))
    assert dst.read_bytes() == b"hello world"   # fake-age just copies


def test_encrypt_with_age_rejects_empty_recipient(tmp_path: pathlib.Path) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)
    src = tmp_path / "in.bin"
    src.write_bytes(b"x")
    with pytest.raises(backup.BackupError, match="no age recipient"):
        backup.encrypt_with_age(str(src), str(tmp_path / "out.age"), "", age_bin=str(age))


def test_encrypt_with_age_missing_binary_errors(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"x")
    with pytest.raises(backup.BackupError, match="not found"):
        backup.encrypt_with_age(
            str(src), str(tmp_path / "out.age"),
            "age1example", age_bin=str(tmp_path / "nope"),
        )


def test_encrypt_with_age_propagates_nonzero_exit(tmp_path: pathlib.Path) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path, behavior="fail")
    src = tmp_path / "in.bin"
    src.write_bytes(b"x")
    with pytest.raises(backup.BackupError, match="exit 1"):
        backup.encrypt_with_age(
            str(src), str(tmp_path / "out.age"),
            "age1example", age_bin=str(age),
        )


def test_decrypt_with_age_requires_identity_file(tmp_path: pathlib.Path) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)
    src = tmp_path / "in.age"
    src.write_bytes(b"x")
    with pytest.raises(backup.BackupError, match="identity file not found"):
        backup.decrypt_with_age(
            str(src), str(tmp_path / "out.bin"),
            str(tmp_path / "nope"), age_bin=str(age),
        )


# ---------------------------------------------------------------------------
# perform_backup / perform_restore — full round-trip via fake-age
# ---------------------------------------------------------------------------


def test_perform_backup_then_restore_round_trip(tmp_path: pathlib.Path) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)

    # Simulate /etc/stealth-vps + /var/lib/stealth-vps under tmp_path.
    fake_etc = tmp_path / "etc-stealth-vps"
    fake_etc.mkdir()
    (fake_etc / "version").write_text("v0.9.0\n", encoding="utf-8")
    fake_var = tmp_path / "var-stealth-vps"
    fake_var.mkdir()
    (fake_var / "users.index.json").write_text('{"version":2,"users":{}}', encoding="utf-8")

    out_dir = tmp_path / "backups"
    result = backup.perform_backup(
        recipient="age1example...",
        output_dir=str(out_dir),
        sources=(str(fake_etc), str(fake_var)),
        age_bin=str(age),
        hostname="test-host",
    )
    assert os.path.exists(result.output_path)
    assert result.output_path.endswith(".tar.age")
    assert "test-host" in result.output_path
    assert oct(os.stat(result.output_path).st_mode)[-3:] == "600"

    # Now restore into a fresh target.
    identity = tmp_path / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-FAKE\n", encoding="utf-8")
    target = tmp_path / "restored"
    target.mkdir()
    members = backup.perform_restore(
        archive_path=result.output_path,
        identity_path=str(identity),
        target_root=str(target),
        age_bin=str(age),
    )
    assert any(m.endswith("version") for m in members)
    # The version file lands at target/<fake_etc_lstripped>/version
    restored_version = target / str(fake_etc).lstrip("/") / "version"
    assert restored_version.read_text() == "v0.9.0\n"


def test_perform_backup_default_includes_skip_missing(tmp_path: pathlib.Path) -> None:
    """Default sources live under /etc + /var; they don't exist on the
    test box. backup must succeed with included=[] and not crash."""
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)
    out_dir = tmp_path / "backups"
    result = backup.perform_backup(
        recipient="age1example...",
        output_dir=str(out_dir),
        sources=("/nonexistent/path/a", "/nonexistent/path/b"),
        age_bin=str(age),
        hostname="empty",
    )
    assert result.included_paths == []
    assert os.path.exists(result.output_path)


def test_perform_restore_archive_missing_errors(tmp_path: pathlib.Path) -> None:
    with pytest.raises(backup.BackupError, match="archive not found"):
        backup.perform_restore(
            archive_path=str(tmp_path / "nope.tar.age"),
            identity_path=str(tmp_path / "ident.txt"),
            target_root=str(tmp_path),
            age_bin="/usr/bin/age",
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_backup_via_env_recipient(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)
    monkeypatch.setattr(backup, "DEFAULT_AGE_BIN", str(age))
    monkeypatch.setenv("STEALTH_VPS_BACKUP_RECIPIENT", "age1example...")
    out_dir = tmp_path / "backups"
    src = tmp_path / "real-state"
    src.mkdir()
    (src / "x").write_text("hi", encoding="utf-8")
    rc = backup.main([
        "backup",
        "--output-dir", str(out_dir),
        "--source", str(src),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "backup complete" in out
    # Exactly one .tar.age dropped in the output dir.
    files = list(out_dir.glob("*.tar.age"))
    assert len(files) == 1


def test_main_backup_missing_recipient_errors(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _skip_if_no_posix_shell()
    age = _drop_fake_age(tmp_path)
    monkeypatch.setattr(backup, "DEFAULT_AGE_BIN", str(age))
    monkeypatch.delenv("STEALTH_VPS_BACKUP_RECIPIENT", raising=False)
    rc = backup.main([
        "backup",
        "--output-dir", str(tmp_path / "out"),
        "--source", str(tmp_path),
    ])
    assert rc == 1
    assert "no age recipient" in capsys.readouterr().err
