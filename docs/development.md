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

# Clone via a GitLab deploy token (project → Settings → Repository →
# Deploy tokens → Create, scope `read_repository`). The VPS can `git pull`
# without a personal credential. Replace USER and TOKEN below — the
# username is the literal string GitLab gives you (e.g. `gitlab+deploy-token-2`).
DEPLOY_USER='gitlab+deploy-token-<id>'
DEPLOY_TOKEN='gldt-<...>'
git clone "https://${DEPLOY_USER}:${DEPLOY_TOKEN}@git.imprezahost.com/impreza/stealth-vps.git" /opt/stealth-vps

# Restrict who can read the token (it ends up in .git/config).
chmod 600 /opt/stealth-vps/.git/config

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

## External contributor flow (reverse-mirror)

External contributors open PRs on the GitHub mirror. Those PRs do not have access to the internal GitLab pipeline directly, so a **reverse-mirror workflow** bridges the two:

```text
   external dev                       GitHub                       internal GitLab
   ============                       ======                       ===============

   opens PR  ──────────────►  imprezahost/stealth-vps
                                      │
                              (pull_request_target)
                                      │
                                      ▼
                       .github/workflows/reverse-mirror.yml
                                      │
                                      │   (1) fetch pull/N/head — never checked out
                                      │   (2) git push to GitLab ext/pr-N
                                      │   (3) create or refresh MR via GitLab API
                                      │   (4) comment on PR with MR link
                                      ▼
                                                            git.imprezahost.com/.../-/merge_requests/M
                                                                       │
                                                              MR pipeline runs:
                                                                  shellcheck, yamllint,
                                                                  ansible-lint, Molecule
                                                                       │
                                                                       ▼
                                                              .gitlab-ci.yml `report-status-*`
                                                                       │
                                  ◄──────────  POST /repos/.../statuses/<sha>  ──────────
                                  (pending / success / failure)
                                      │
                                      ▼
   PR commit checks updated  ◄──  GitHub status API
```

### Required secrets

**On the GitHub mirror (`imprezahost/stealth-vps` → Settings → Secrets and variables → Actions):**

| Secret | Purpose |
|---|---|
| `GITLAB_MIRROR_URL` | Push URL with embedded token, e.g. `https://oauth2:<deploy_token>@git.imprezahost.com/impreza/stealth-vps.git`. Deploy token needs `write_repository` scope. |
| `GITLAB_API_URL` | `https://git.imprezahost.com/api/v4` |
| `GITLAB_PROJECT_ID` | Numeric project ID from GitLab → project home → vertical-ellipsis → Copy project ID |
| `GITLAB_API_TOKEN` | PAT with `api` scope, used to create / update / list MRs |

**Optional variable** (Settings → Secrets and variables → Actions → Variables):

| Variable | Default | Purpose |
|---|---|---|
| `GITLAB_WEB_URL` | `https://git.imprezahost.com/impreza/stealth-vps` | Override if the project URL changes |

**On the GitLab project (Settings → CI/CD → Variables):**

| Variable | Purpose |
|---|---|
| `GITHUB_API_TOKEN` | PAT with `repo:status` scope (classic) or `Commit statuses: read+write` (fine-grained) on `imprezahost/stealth-vps`. Mask + protect. |
| `GITHUB_DEPLOY_KEY` | SSH private key for the `mirror-to-github` job (pre-existing). Mask + protect. File type, not Variable. |

### Behaviour summary

| Event on GitHub | Action |
|---|---|
| External PR opened / reopened | Push to GitLab `ext/pr-N`, open MR, comment on PR |
| External PR head force-pushed | Force-push to GitLab `ext/pr-N`, refresh MR description (same `MR_IID`) |
| Internal PR (same-repo branch) | Skipped — internal branches already go through GitLab directly |
| MR pipeline starts on GitLab | POST `pending` status to upstream PR commit |
| MR pipeline ends — success | POST `success` status to upstream PR commit |
| MR pipeline ends — failure | POST `failure` status to upstream PR commit |

### Security notes

- The reverse-mirror workflow uses `pull_request_target` (privileged context with secrets). It intentionally **never checks out the PR code** — only `git fetch` of `pull/N/head` followed by `git push` to GitLab. The PR payload is opaque to the privileged job, so a malicious PR cannot exfiltrate secrets.
- The MR runs inside GitLab CI, where execution is on private runners. Standard `pull_request` CI rules (sandbox the PR code, no production secrets) apply there.
- Force-push protection on the GitLab `main` branch should be enabled so an `ext/pr-N` MR cannot bypass review.

## Commit style

Conventional Commits. See [CONTRIBUTING.md](../CONTRIBUTING.md) for examples.
