# Development setup

This document is for people working on `stealth-vps` itself. If you just want to install it on a VPS, follow the [README](../README.md) instead.

## Two ways to drive Ansible while iterating

The role can be applied from a separate controller (your laptop) **or** from the VPS itself. Both paths are supported and exercised; pick whichever fits your machine.

### Path A — Ansible on a controller (laptop, jump host, CI)

Use this when you want to iterate locally and apply the role to a remote VPS over SSH. Requires Linux, macOS, or WSL on Windows. **Native Windows is not supported as an Ansible controller.**

```bash
# Debian/Ubuntu controller
sudo apt-get update
sudo apt-get install -y python3-pip pipx python3-venv
pipx ensurepath
pipx install "ansible-core>=2.14,<2.18"
pipx install ansible-lint
pipx install yamllint
ansible-galaxy collection install -r ansible/requirements.yml

cp ansible/inventory/example.yml ansible/inventory/hosts.yml
# edit hosts.yml with ansible_host, ansible_user, ansible_ssh_private_key_file
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml --check
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml
```

### Path B — Ansible on the VPS itself (`ansible-pull` style)

Use this when your dev machine is Windows-only, or when you want minimal local tooling. This is exactly the path that `scripts/install.sh` and the `cloud-init` template take in production, so testing it here doubles as integration coverage of the installer.

#### One-time VPS setup

```bash
ssh root@<vps>

apt-get update
apt-get install -y --no-install-recommends \
  git ansible-core python3-pip pipx python3-venv jq ca-certificates

export PATH="/root/.local/bin:$PATH"
echo 'export PATH="/root/.local/bin:$PATH"' >> ~/.bashrc

pipx install ansible-lint
pipx install yamllint

# Clone via a GitLab deploy token (read_repository scope) so the VPS can
# `git pull` without a personal credential. Replace the token below.
git clone https://gitlab+deploy-token-<id>:<token>@git.imprezahost.com/impreza/stealth-vps.git /opt/stealth-vps

cd /opt/stealth-vps
ansible-galaxy collection install -r ansible/requirements.yml
```

#### Iteration loop

Edit on your dev machine, commit, push to GitLab. Then on the VPS:

```bash
cd /opt/stealth-vps
git fetch && git checkout <branch> && git pull

# Lint
yamllint -s .
ansible-lint ansible/

# Dry-run, then apply (use --tags to target a single area)
ansible-playbook -i 'localhost,' -c local ansible/playbooks/site.yml --tags kernel --check
ansible-playbook -i 'localhost,' -c local ansible/playbooks/site.yml --tags kernel
```

Available top-level tags: `hardening`, `stealth`, plus per-area: `kernel`, `xray`, `hysteria`, `panel`, `ssh`, `ufw`, `fail2ban`, `updates`, `spamhaus`.

## Branch model

- `main` — public-facing. Mirrored to GitHub on tagged releases (see `.gitlab-ci.yml`).
- `feat/<slug>` — your working branch. Open a merge request into `main` on GitLab when ready.
- `internal/planning` — strategy and decision docs (`docs/internal/`). Never merged into `main`, never mirrored to GitHub.

## CI

- **GitLab** (`.gitlab-ci.yml`) is the canonical pipeline: shellcheck, yamllint, ansible-lint, and (once roles have content) Molecule.
- **GitHub** (`.github/workflows/ci-validate.yml`) runs report-only lint on the public mirror to give external contributors quick feedback.

## Commit style

Conventional Commits. See [CONTRIBUTING.md](../CONTRIBUTING.md) for examples.
