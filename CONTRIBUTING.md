# Contributing to stealth-vps

Thanks for taking the time to contribute. This document explains how the project is developed and how external contributions flow into releases.

## Development model

This repository follows a **"develop private, release public"** model — the same pattern used by HashiCorp, MongoDB, and several other infrastructure-focused open-source projects.

- **Source of truth**: a private GitLab repository maintained by Impreza Host.
- **Public mirror**: this GitHub repository, updated automatically on each tagged release.

What this means in practice:

- The public GitHub repository only shows **tagged release commits**, the `LICENSE`, `README`, and the source files at each release. Day-to-day work-in-progress branches, draft merge requests, and internal discussions stay private.
- Releases follow [Semantic Versioning](https://semver.org/). Read the [CHANGELOG](CHANGELOG.md) to see what each version contains.

## How to contribute

### Reporting bugs / requesting features

Open an issue on the [GitHub issue tracker](https://github.com/imprezahost/stealth-vps/issues).

Please include:

- Your OS / VPS provider / region (helps reproduce environment-specific issues)
- The exact command you ran (one-shot install, ansible-playbook, or cloud-init)
- Relevant log output (`journalctl -u xray`, `journalctl -u hysteria-server`, `/var/log/syslog`)
- The release version (`stealth-vps --version` or the tag you checked out)

For security issues, **do not open a public issue** — see [SECURITY.md](SECURITY.md).

### Submitting pull requests

Pull requests are welcome on GitHub. The workflow is automated:

1. Open a PR against `main` on GitHub.
2. A bot mirrors the PR head to the internal GitLab as `ext/pr-<N>` and opens a tracking Merge Request there. You'll see a comment on your PR with the MR link.
3. The internal CI pipeline (Molecule on Debian 12 + Ubuntu 22.04/24.04, full lint matrix) runs against your changes. The pipeline status is reported back on your PR as a commit-status check named `stealth-vps/gitlab-ci`.
4. We review publicly on GitHub. Discussion happens in the PR.
5. If accepted, a maintainer merges the internal MR. Your original commits + authorship are preserved.
6. The change ships in the next tagged release; the release tag triggers the GitLab → GitHub release mirror and shows up on `main` here.

The "release-only" mirror means your PR may stay open for a few days after acceptance, until the next tag goes out. We'll close the PR with a reference to the inclusion tag once it ships.

If you force-push to your PR branch, the workflow refreshes the same internal MR — no need to coordinate manually.

See [docs/development.md § External contributor flow](docs/development.md#external-contributor-flow-reverse-mirror) for the implementation details if you're curious.

### Code style

- **Shell**: `shellcheck` clean. Bash, not POSIX sh — we target modern Debian/Ubuntu.
- **Ansible**: `ansible-lint` clean. Idempotent tasks. No `command:` / `shell:` unless there is no module alternative.
- **YAML**: `yamllint` clean. 2-space indent.
- **Markdown**: leave trailing whitespace untouched (it's significant for line breaks).
- **Python** (where applicable): `ruff` + `black`. Type hints required for public functions.

CI on GitLab runs all linters; you can run them locally with:

```bash
shellcheck scripts/*.sh
ansible-lint ansible/
yamllint .
```

### Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(ansible): add support for shadowsocks-2022 inbound
fix(install): handle missing dpkg-reconfigure on minimal images
docs(client): add Hiddify setup for Android
chore(ci): bump ansible-lint to 24.0
```

### Sign-offs

Contributions are accepted under the project's [MIT License](LICENSE). By submitting a PR you confirm you have the right to do so. We don't require a CLA — if you can write `Signed-off-by:` on your commits (DCO-style), that's appreciated.

## Questions

For development questions that aren't bugs or features, use [GitHub Discussions](https://github.com/imprezahost/stealth-vps/discussions) (when enabled) or the project's Telegram channel (link in the README once announced).
