# Roadmap interno — v0.5.9 → v0.6.0 → v0.7.0

Plano de implementação para tornar a instalação mais simples (TUI + bot Telegram + endpoint de subscription) e desacoplar o stealth-vps do painel 3X-UI (modo headless). Documento interno; o público vê apenas o roadmap table no [`README.md`](../../README.md).

## Context

Hoje o stealth-vps tem dois gaps competitivos vs. `Hiddify-Manager` / `aleskxyz/reality-ezpz`:

1. **Instalação não-interativa** — operador pré-seta variáveis Ansible ou env vars antes de `curl … | bash`. Não há prompts.
2. **Dependência total do 3X-UI** — gerência de usuários, contagem de tráfego, e link de subscription só existem via painel web. Setup sem web UI não tem alternativa; o painel (GPL-3.0) é o único caminho.

**Resultado pretendido**:
- **v0.6.0**: instalação interativa (TUI) + bot Telegram para ops + endpoint de subscription. Tudo continua rodando sobre 3X-UI (default não muda).
- **v0.7.0**: modo "headless" — o painel 3X-UI vira opcional. Mesma CLI, mesmo bot Telegram, mesmo endpoint de subscription, mas a fonte da verdade vira state file Ansible-managed e o Xray-core roda como serviço systemd próprio.

**A grande aposta arquitetural**: tudo em v0.6.0 já é desenhado pensando no v0.7.0, especialmente o "index de usuários" — assim a migração entre os dois modos é trivial em vez de um rewrite.

## Strategy at a glance

Peça central: introduzir já em **v0.6.0** um arquivo `users.index.json` que vira a *visão do operador* sobre quem está autorizado. Em v0.6.0, o bot **escreve em duplicado**: chama o API do 3X-UI **e** atualiza o index. Em v0.7.0, basta flipar `stealth_vps_panel_enabled=false` — o index já é autoritativo, o painel some, e nada do que o operador via muda. Sem essa peça, v0.7.0 vira um rewrite arriscado com migração SQLite frágil.

Outras decisões consequentes:

- **Caddy + arquivos estáticos** para subscription, não daemon Python. Bot/CLI materializa `/var/lib/stealth-vps/subscriptions/<token>.txt`; Caddy só serve. Mata race condition entre "bot escreve estado" e "user busca sub". Sem novo serviço de longa duração além do bot.
- **acme.sh permanece o único cliente ACME**. Caddy é configurado com `tls /etc/stealth-vps/tls/fullchain.pem /etc/stealth-vps/tls/privkey.pem` (cert existente, sem auto-TLS). Eliminar conflito de porta 80.
- **Bot e CLI em Python**, compartilhando um pacote `/usr/local/lib/stealth-vps/` (módulos `threex_client.py`, `state.py`, `backends.py`). Venv isolado em `/opt/stealth-vps/venv` com `requirements.txt` hash-pinado.
- **Refactor do `xray.yml` já em v0.6.0**: separar a geração de state Reality (já é panel-independent) do push pro painel. v0.7.0 só adiciona a branch standalone-xray.
- **`UserBackend` interface em v0.6.0** com uma única impl (`ThreeXUIBackend`). Disciplina força command set a ser backend-agnóstico.

---

## v0.5.9 — pré-requisitos (release tooling + xray.yml refactor)

Sprint mecânico, sem feature nova visível. Habilita v0.6.0.

### Sprint 16: `scripts/release.sh`

**Status**: shipped na branch [`feat/sprint-16-release-script`](https://git.imprezahost.com/impreza/stealth-vps/-/tree/feat/sprint-16-release-script). MR a abrir.

One-shot bumper de versão para os 21 arquivos self-pinned (install.sh, cloud-init, terraform module + 5 examples, pulumi module + Hetzner example, docs/terraform.md, terraform/README.md). `--dry-run` flag. Manual reminder pros 4 arquivos historicamente complicados (CHANGELOG, READMEs, pulumi/README).

### Sprint 17: refactor `xray.yml`

Extrair `xray.yml` linhas 74-138 (state gen Reality) para `reality_state.yml`, e linhas 140-275 (auth panel + push inbound) para `reality_push_3xui.yml`. `xray.yml` vira wrapper:

```yaml
- include_tasks: reality_state.yml
- include_tasks: reality_push_3xui.yml
  when: stealth_vps_panel_enabled | bool
```

Comportamento idêntico ao atual. Molecule deve passar sem mudança. Pré-condição pra v0.7.0 (próxima branch será `reality_xray_standalone.yml`).

---

## v0.6.0 — Full UX install + Telegram bot + subscriptions (panel mode)

> **Scope decision 2026-05-15**: shipping **Caminho C (full UX)** rather than the minimal-roadmap baseline. The thesis is "the script does the dirty work so the user is connected in under 5 minutes without touching `nano`, `dig`, or `journalctl`." That decision adds 8 UX layers on top of the original scope. See the "UX layers" subsection below for what each one is and why.

### Scope (in)

**Core (original roadmap)**:

- `install.sh` interativo via whiptail (com fallback graceful para env-var quando piped).
- Index de usuários `users.index.json` — escrito por toda task que cria/revoga cliente Reality.
- Bot Telegram (`stealth-vps-bot.service`) com comandos: `/status`, `/diagnose`, `/creds`, `/user add|list|revoke`, `/sub|sub revoke`.
- Endpoint de subscription via Caddy servindo `/var/lib/stealth-vps/subscriptions/*.txt`.

**Full UX layers (Caminho C)**:

1. **Zero-domain default**. `install.sh` doesn't require `domain`. Default = IP-only Reality + self-signed Hysteria2. The operator can be connected in 5 min without touching DNS. LE is strictly opt-in (only needed when they want a public subscription URL or a domain-validating client).
2. **Terminal QR code** for the default Reality URI at the end of `install.sh`. `qrencode -t ANSIUTF8` prints into the terminal; operator scans with their phone camera or directly from Hiddify → profile imports, connects. Cuts first-connection time to <30 s.
3. **Bot setup via QR (chat-id auto-capture)**. The TUI prompts for the BotFather token + admin handle; bot auto-captures `chat_id` from the first `/start` rather than requiring the operator to paste it. The TUI also prints a QR for `t.me/BotFather` with 3-step inline instructions for users who haven't created a bot before.
4. **DNS pre-flight**. When `domain` is set, the TUI does `dig +short` against it before calling `acme.sh` and loops "DNS not propagated yet, retrying…" up to 5 min, instead of letting LE fail and dumping an obscure error.
5. **Health-check pós-deploy**. After `ansible-pull` returns, the script sleeps 10 s and runs a 6-8-line checklist: `x-ui` active? `hysteria-server` active? panel responds HTTPS? Reality port reachable from outside (egress curl)? cert expiry > 60 d? Prints a tabela with ✓/✗/⚠ per row so the operator knows immediately if it worked.
6. **Human-friendly error messages**. `install.sh` wraps the Ansible / acme.sh / systemd errors most likely to bite and replaces the stack trace with: "✗ Reality couldn't reach dest `www.microsoft.com:443`. Common cause: VPS provider blocks outbound TCP from new instances. Quick fix: `STEALTH_REALITY_DEST=www.lovelive-anime.jp:443 install.sh`. Full log: `/var/log/stealth-vps/install-*.log`."
7. **`s-vps update` antecipado**. The CLI lands in v0.7 in the roadmap, but `s-vps update` (single command: fetch latest tag, ansible-pull with the right `--tags`) ships in v0.6.0 as a `/usr/local/bin/s-vps` symlink to a minimal shell wrapper. Full Python CLI still v0.7.
8. **Bot DM pós-install**. If the operator opted into the bot, the install script ends by sending the default-profile URI (with QR) + subscription URL + a `/diagnose` hint via DM. The operator can close their SSH session and never need to touch the terminal again — everything from then on is Telegram.

### Scope (out)

- Modo headless / xray standalone (v0.7.0).
- Hysteria2 per-user (v0.7.0 — 3X-UI v2.9.4 não gerencia Hysteria2; em v0.6.0 todos os clientes Hysteria2 compartilham a mesma senha, documentado nas release notes).
- Comandos do bot com janela temporal (`/user list --since X`). Bot expõe só "totais correntes" para não criar dívida que v0.7.0 não consegue pagar sem persistência adicional.
- Web wizard. Decisão firme: conflita com pitch "auditable / IaC-native".
- Full Python `s-vps` CLI (just `update` and `diagnose` ship as shell wrappers in v0.6.0; verb expansion is v0.7.0).

### Implementation steps (ordem importa)

1. **Index de usuários** (`/etc/stealth-vps/users.index.json`). Schema:
   ```json
   {
     "version": 1,
     "users": {
       "stealth-vps-default": {
         "reality_uuid": "...",
         "hysteria_password": "...",
         "sub_token": "<32-byte hex>",
         "created_at": "2026-05-14T12:00:00Z",
         "enabled": true
       }
     }
   }
   ```
   Seed-ado pela task `reality_state.yml` com o cliente default. Chmod 0600. Validação de label: `^[a-zA-Z0-9_-]{1,32}$`, namespace `stealth-vps-*` reservado.

2. **Pacote Python `/usr/local/lib/stealth-vps/`** (módulos compartilhados, sem deps externas):
   - `threex_client.py` — extrair de `stealth-vps-metrics-update.py.j2` (login/cookie/`inbounds/list`/`inbounds/addClient`/`inbounds/delClient`). Refactor não-destrutivo: metrics updater passa a importar deste módulo.
   - `state.py` — leitura/escrita atômica (write-temp+rename) de `users.index.json` + state files existentes.
   - `backends.py` — interface `UserBackend` (`add`, `list`, `revoke`, `get`) com `ThreeXUIBackend` como única impl. `ThreeXUIBackend.add` faz double-write: chama `inbounds/addClient` no painel **e** grava no index.
   - `subscription.py` — gera o conteúdo do arquivo `.txt` (base64 de URIs `vless://` + `hysteria2://`).
   - `urivider.py` — extrair lógica de formação de URI do template `.txt.j2` para função pura.

3. **Bot Telegram** (`/opt/stealth-vps/venv` + `tools/bot/stealth_vps_bot.py`):
   - Venv via `ansible.builtin.pip` com `virtualenv=/opt/stealth-vps/venv`, `requirements.txt` hash-pinado (apenas `python-telegram-bot==21.x`).
   - Single-file ~400 LOC. Long-poll (não webhook → sem TLS no lado do bot).
   - Auth via whitelist de chat IDs em `/etc/stealth-vps/bot.env` (chmod 0600).
   - systemd unit endurecida: `DynamicUser=yes`, `ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`, `PrivateTmp=true`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, `ReadWritePaths=/etc/stealth-vps /var/lib/stealth-vps`, `Restart=on-failure`.
   - Após `/user add` ou `/sub`, materializa `/var/lib/stealth-vps/subscriptions/<token>.txt` síncronamente *antes* de retornar a URL.
   - `/diagnose`: dump de "painel up: y/n, Reality inbound count, hysteria service state, cert expiry, last metrics scrape".

4. **Subscription endpoint** (Caddy):
   - Nova role-task `subscription.yml`. `apt install caddy` (repo oficial); systemd unit do pacote.
   - `Caddyfile` template: vhost serve `/.well-known/stealth-vps-sub/*` de `/var/lib/stealth-vps/subscriptions/`, com `tls` apontando pros certs existentes do acme.sh quando `stealth_vps_domain` está setado. Sem `tls internal`, sem `acme_dns`.
   - Default: bind em `127.0.0.1:8443`, UFW fechado, operator usa via SSH tunnel.
   - Opt-in para exposição pública (`stealth_vps_subscription_expose=true`): UFW abre 443/TCP, vhost reescreve para `0.0.0.0:443`. Caminho `/.well-known/stealth-vps-sub/<token>` (menos pingável).
   - 404 idêntico para path desconhecido e token inválido.

5. **`install.sh` interativo** (Caminho C: full UX):
   - Detecta `[ -t 0 ] && [ -t 1 ]`. Se ambos TTY → whiptail. Senão → env-var mode (path atual, intocado).
   - Sem `< /dev/tty` magic. Cloud-init / Terraform / Pulumi seguem env-var.
   - **Prompts (Caminho C)** — domain é **optional**, instalador deixa claro que o fast-path é "skip and stay on IP":
     ```text
     ┌─ stealth-vps installer ────────────────────────────────────┐
     │  Fast path: leave fields empty for IP-only deploy          │
     │  (you can be connected in 5 minutes — domain is optional). │
     └────────────────────────────────────────────────────────────┘
     [1/5] Domain (optional, leave empty for IP-only): _
     [2/5] LE email (only if domain set): _
     [3/5] SSH port [22550]: _
     [4/5] Reality dest [www.microsoft.com:443]: _
     [5/5] Enable Telegram bot? (Y/n): _
       └─ if yes: paste BotFather token: _
       └─ admin Telegram handle (we'll capture chat_id on /start): _
     ```
   - **DNS pre-flight** (Caminho C item #4) when domain is set: loops `dig +short` against the domain vs the VPS IP up to 5 min with progress dots before calling `acme.sh`. Aborts with a human message if it times out.
   - **Health check post-deploy** (item #5): after `ansible-pull` returns, sleeps 10 s and runs a checklist (services active? panel HTTPS reachable? Reality port reachable from outside via curl to ifconfig.me? cert expiry > 60 d?). Prints a ✓/✗/⚠ table — operator sees deploy status without parsing logs.
   - **Error wrapping** (item #6): around the `ansible-pull` call + `acme.sh` invocations, intercept known failure patterns (regex on stderr/journal) and replace the stack trace with a one-paragraph "what failed + likely cause + quick fix" message. Full Ansible log stays at `/var/log/stealth-vps/install-<timestamp>.log` for advanced cases.
   - **QR code at the end** (item #2): if `qrencode` is installed (added to apt-get deps in `install.sh` itself), print `qrencode -t ANSIUTF8 -o - "<vless URI>"` directly into the terminal. If `qrencode` install fails, fall back to printing the URI and a `qrencode -t ANSIUTF8 -r /root/stealth-vps-credentials.txt` instruction.
   - **Bot DM at the end** (item #8): if bot is enabled AND chat_id was captured (operator clicked /start), the script sends the default profile URI + sub URL + a `/diagnose` hint via Telegram before exiting. From that point the operator's phone is the control surface.
   - **`s-vps update` wrapper** (item #7): script drops `/usr/local/bin/s-vps` as a thin shell wrapper exposing `update` (fetch latest tag + ansible-pull) and `diagnose` (the same checklist as the health-check). Both call existing role logic; full Python CLI lands in v0.7.0.
   - Escreve respostas em `/root/stealth-vps-install-vars.yml` chmod 0600; passa para `ansible-pull -e @<file>`.
   - Header comment documenta o contrato env-var como estável pra cloud-init.

6. **Bot chat_id auto-capture** (item #3): when the bot starts for the first time without a chat_id in `bot.env`, it enters a "pairing mode" — polling for `/start` messages from anyone, accepting the first one as the admin, writing the chat_id to `bot.env`, and dropping into normal mode. Pairing mode has a 30-min timeout (bot exits and falls back to env-var configuration if no `/start` arrives) so a forgotten-pair bot doesn't sit open indefinitely. Documented in `docs/telegram-bot.md`.

7. **QR code library** (item #2): add `qrencode` to the role's package list (`stealth-vps/tasks/main.yml` or similar). Also installed by `install.sh` directly before the QR-print step (in case the operator runs `install.sh` before `apt-get` finishes setting up the role's deps).

### Files (new)

Core (original roadmap):
- `ansible/roles/stealth-vps/tasks/users_index.yml`
- `ansible/roles/stealth-vps/tasks/bot.yml`
- `ansible/roles/stealth-vps/tasks/subscription.yml`
- `ansible/roles/stealth-vps/templates/stealth-vps-bot.service.j2`
- `ansible/roles/stealth-vps/templates/stealth-vps-bot.env.j2`
- `ansible/roles/stealth-vps/templates/Caddyfile.j2`
- `ansible/roles/stealth-vps/files/python-pkg/threex_client.py`
- `ansible/roles/stealth-vps/files/python-pkg/state.py`
- `ansible/roles/stealth-vps/files/python-pkg/backends.py`
- `ansible/roles/stealth-vps/files/python-pkg/subscription.py`
- `ansible/roles/stealth-vps/files/python-pkg/urivider.py`
- `ansible/roles/stealth-vps/files/bot/stealth_vps_bot.py`
- `ansible/roles/stealth-vps/files/bot/requirements.txt` (hash-pinado)
- `docs/telegram-bot.md`
- `docs/subscription-endpoint.md`
- `tests/molecule/default/verify-bot.yml`

Full UX layer (Caminho C):
- `scripts/lib/health-check.sh` — the post-deploy ✓/✗/⚠ checklist, sourced by `install.sh` and reused by `s-vps diagnose`. Pure bash so it works on minimal VPSes before the Python venv is set up.
- `scripts/lib/dns-preflight.sh` — the `dig`-loop waiting for DNS to propagate to the VPS IP. Sourced by `install.sh`.
- `scripts/lib/error-wrap.sh` — known-failure-pattern catalogue (associative array of `pattern → "what failed + likely cause + quick fix"`); piped through `ansible-pull` + `acme.sh` stderr.
- `scripts/s-vps` — thin shell wrapper exposing `update` (fetch latest tag + ansible-pull with smart `--tags`) and `diagnose` (calls `scripts/lib/health-check.sh`). Installed at `/usr/local/bin/s-vps` by the role's `main.yml`.
- `ansible/roles/stealth-vps/tasks/cli_wrapper.yml` — drops `/usr/local/bin/s-vps` + makes it executable.
- `docs/installer-ux.md` — documents the install.sh contract: prompts, env-var fallbacks, the 8 UX layers, what each error message means.

### Files (changed)

- `scripts/install.sh` — TUI + env-var contract documentado + sourcing the three `scripts/lib/*.sh` helpers + the 8 UX-layer integrations (DNS pre-flight, error wrap, health check, QR print, bot DM, s-vps drop, zero-domain branch). Header comment expanded to enumerate every env-var the contract supports.
- `ansible/roles/stealth-vps/tasks/main.yml` — inclui `users_index.yml`, `bot.yml`, `subscription.yml`, `cli_wrapper.yml` (com `when:` no opt-in onde apropriado).
- `ansible/roles/stealth-vps/defaults/main.yml` — `stealth_vps_bot_enabled`, `stealth_vps_bot_token`, `stealth_vps_bot_admin_chat_ids` (now optional; bot pairs via /start if empty), `stealth_vps_subscription_enabled`, `stealth_vps_subscription_expose`, `stealth_vps_subscription_path`, `stealth_vps_cli_install` (default true).
- `ansible/roles/stealth-vps/templates/stealth-vps-metrics-update.py.j2` — importa de `threex_client.py`.
- `ansible/roles/stealth-vps/templates/stealth-vps-credentials.txt.j2` — adiciona seção "Telegram bot" + "Subscription URL" quando habilitados + ANSI QR (literal bytes) for the default URI so `cat /root/stealth-vps-credentials.txt` from a fresh SSH session shows the QR too.
- `ansible/roles/stealth-hardening/tasks/ufw.yml` — abre 443/TCP só quando `stealth_vps_subscription_expose=true`.
- `tests/molecule/default/converge.yml` / `verify.yml`.
- `CHANGELOG.md`, `README.md`, `README.zh-CN.md`, `docs/operations.md`.

### Tests

- **Molecule** (panel mode):
  - novo `verify-bot.yml` afirma (a) `stealth-vps-bot.service` ativo, (b) `/etc/stealth-vps/bot.env` chmod 0600, (c) `/opt/stealth-vps/venv/bin/python` existe, (d) `caddy` serving 127.0.0.1:8443 retorna 404 para path inválido e 200 para token pre-seeded.
  - `verify-installer-ux.yml` (Caminho C): asserts `qrencode` installed, `/usr/local/bin/s-vps` exists + executable, `scripts/lib/health-check.sh` returns 0 on a converged host, `dns-preflight.sh` returns 2 (inconclusive) on an unset domain — fast unit-style checks.
- **Real-VPS** validation matrix (Caminho C):
  - **Fast-path**: deploy via TUI on a fresh Hetzner CAX11, leave domain empty, scan the printed QR in Hiddify, connect. Target time-to-connect: under 5 min from `curl install.sh | bash` to traffic flowing.
  - **Full-path**: same VPS, with domain + LE + bot. Verify DNS pre-flight waits + retries, bot pairs via /start, install ends with a Telegram DM containing the QR + sub URL.
  - **Failure path**: simulate dest unreachable (point `STEALTH_REALITY_DEST` at `127.0.0.1:1`); verify the error-wrap intercepts and shows the human message instead of the stack trace.

---

## v0.7.0 — Headless mode (panel optional)

### Scope (in)

- `stealth_vps_panel_enabled=false` vira modo de operação totalmente suportado.
- Xray-core como systemd service próprio (não embedded no 3X-UI).
- Hysteria2 per-user via `auth.type: userpass`.
- CLI `s-vps` instalada em `/usr/local/bin/`.
- `HeadlessBackend` implementando a mesma interface `UserBackend`.
- Métricas Reality em modo headless via Xray stats API (gRPC).
- Ferramenta de migração `s-vps migrate from-3xui`.

### Scope (out)

- Web UI própria (decisão firme).
- Suporte a transports adicionais (gRPC/ws/h2 inbound shapes) — `xtls-rprx-vision` + Reality TCP só.

### Implementation steps

1. **Standalone Xray service**:
   - Nova task `reality_xray_standalone.yml`. Download direto de XTLS/Xray-core releases (`Xray-linux-{arch}.zip`), install para `/usr/local/bin/xray`.
   - User dedicado `xray`. systemd unit endurecida.
   - Config: `/etc/xray/config.json` rendered de `users.index.json` + `reality.state.yml`.
   - Mutex em `tasks/main.yml`: se `panel_enabled=true` *e* xray standalone, fail loud.

2. **Hysteria2 per-user**:
   - Migração de schema: `hysteria.state.yml` atual = `{auth_password, obfs_password}` → `{users: {label: password}, obfs_password}`. Task `hysteria_migrate_state.yml` converte preservando senha existente (mapeada para `stealth-vps-default`).
   - Template `hysteria-config.yaml.j2`: branch `userpass` ativa quando index tem >1 user OU `panel_enabled=false`.

3. **`HeadlessBackend`**:
   - Implementa `UserBackend.add/list/revoke` lendo/escrevendo `users.index.json` direto. Após mutação: renderiza `xray config.json`, renderiza `hysteria-config.yaml`, `systemctl reload xray && systemctl reload hysteria-server`.
   - Bot/CLI escolhem backend no startup: `if /etc/stealth-vps/panel.state.yml exists → ThreeXUIBackend, else HeadlessBackend`.

4. **CLI `s-vps`**:
   - Single-file Python ~300 LOC. Verbs: `user add|list|revoke <label>`, `creds`, `status`, `diagnose`, `sub gen|revoke <label>`, `reload`, `migrate from-3xui`.
   - Instalado em `/usr/local/bin/s-vps`.

5. **Métricas em modo headless**:
   - Xray expõe `StatsService` via gRPC. Adicionar `services.statsService` no config.json + `stats {}` inbound.
   - `stealth-vps-metrics-update.py` ganha branch: `HeadlessBackend` ativo → gRPC stats em vez de panel API.
   - Mesmas labels Prometheus. Dashboard Grafana não muda.

6. **Migração `s-vps migrate from-3xui`**:
   - Lê `/etc/x-ui/x-ui.db` (SQLite). Schema do 3X-UI v2.9.4 está pinado.
   - Para cada client: cria entrada em `users.index.json` com `reality_uuid=clients[i].id`, `hysteria_password=<reuso ou gera nova>`, `sub_token=<gera>`.
   - Output: instrução para flipar `stealth_vps_panel_enabled=false` + rodar `ansible-playbook`.
   - Não desinstala o painel — rollback flipando back.

### Files (new)

- `ansible/roles/stealth-vps/tasks/reality_xray_standalone.yml`
- `ansible/roles/stealth-vps/tasks/hysteria_migrate_state.yml`
- `ansible/roles/stealth-vps/templates/xray-standalone.service.j2`
- `ansible/roles/stealth-vps/templates/xray-config.json.j2`
- `ansible/roles/stealth-vps/files/python-pkg/backends_headless.py`
- `ansible/roles/stealth-vps/files/cli/s-vps.py`
- `docs/headless-mode.md`
- `docs/migration-3xui-to-headless.md`
- `tests/molecule/headless/`

### Tests

- **Molecule novo scenario `headless`**: mesma matrix, inventory com `panel_enabled=false`. Asserts: `x-ui.service` não existe, `xray.service` ativo, `/etc/xray/config.json` 0600 root, `s-vps user list` retorna o cliente default, `/var/lib/stealth-vps/subscriptions/<default-token>.txt` existe.
- **Migração**: scenario derivado — converge em panel mode, cria 3 users via bot, roda `s-vps migrate from-3xui`, flipa flag, re-converge headless, verifica que os 3 users persistem e URIs continuam válidos.

---

## Cross-cutting concerns

### Security model

- **Bot token + admin chat IDs**: `/etc/stealth-vps/bot.env`, chmod 0600, `EnvironmentFile=` no systemd. Recovery: SSH + edit + restart.
- **Subscription tokens**: 32-byte random URL-safe base64. Não logados. `sub revoke` invalida + rotaciona.
- **Label validation**: `^[a-zA-Z0-9_-]{1,32}$`, namespace `stealth-vps-*` reservado.
- **systemd hardening**: bot + sub (v0.7.0 também xray standalone): `DynamicUser=yes` quando possível, `ProtectSystem=strict`, `ReadWritePaths=` mínimo.
- **404 uniforme** no Caddy.
- **Bot fail-closed**: se `users.index.json` for unreadable, bot retorna erro em todos os comandos.
- **Reconciliação panel ↔ index**: após `addClient`, bot faz `inbounds/list` imediatamente e confirma antes de gravar no index.

### Backward compatibility & state migrations

- `users.index.json` é criado seed-ado pela task `users_index.yml` em v0.6.0 a partir de `reality.state.yml` + `hysteria.state.yml` existentes. Idempotente.
- `hysteria.state.yml` shape change (v0.7.0) detectado por `hysteria_migrate_state.yml`. One-shot, rerun é no-op.
- Cloud-init / Terraform / Pulumi paths NÃO mudam de forma quebrando.
- Operators v0.5.x → v0.7.0 direto: documentar que precisam de pelo menos um apply v0.6.x antes de flipar a flag.

### Out of scope (explícito)

- Web wizard / web installer.
- UI web própria.
- Suporte a transports não-Reality-TCP / Hysteria2-padrão.
- Comandos do bot com janela temporal antes de v0.7.0 ter `StatsService` exposto.
- Replace acme.sh por Caddy ACME.

---

## Release sequencing

```text
v0.5.9   sprints 16+17+18:
         - sprint 16: scripts/release.sh                       (✓ MR !17 merged 1f4762b)
         - sprint 17: roadmap doc                              (this file, branch up, MR pending)
         - sprint 18: refactor xray.yml                        (todo, prereq for v0.6)
v0.6.0   Caminho C full-UX install + bot + sub. Split into 11 sub-sprints (6.0.1 .. 6.0.11):
         - 6.0.1  zero-domain default + DNS pre-flight
         - 6.0.2  users.index.json schema + seed task
         - 6.0.3  Python pkg extract (threex_client, state, backends, subscription, urivider)
         - 6.0.4  TUI install.sh (whiptail + env-var fallback)
         - 6.0.5  qrencode + terminal QR
         - 6.0.6  bot service skeleton + chat_id auto-capture pairing mode
         - 6.0.7  Caddy + subscription endpoint
         - 6.0.8  bot DM pós-install + sub URL print
         - 6.0.9  health-check post-deploy lib + integration
         - 6.0.10 error-wrap lib + integration
         - 6.0.11 s-vps shell wrapper (update + diagnose)
v0.6.1   Polish: i18n EN+zh-CN nos strings do bot, /diagnose enriquecido com mais signal
v0.7.0   Headless mode + full Python s-vps CLI + 3X-UI migração
v0.7.1   Métricas headless via Xray gRPC stats (se não couber em v0.7.0)
v1.0.0   Probe-resistance CI full + JA4 + signed releases
```

Estimativas revisadas (Caminho C):
- **v0.5.9** ~2 dias (sprint 18 é o único item de código real; 16+17 já estão prontos).
- **v0.6.0** ~3-4 semanas full-time (era 1-2 semanas no escopo mínimo; 8 UX layers + 11 sub-sprints adicionam ~2 semanas).
- **v0.6.1** ~3 dias.
- **v0.7.0** ~2-3 semanas.
- **v0.7.1** ~3 dias.

Cumulativo ~7-8 semanas full-time.

A linha gorda é v0.6.0. O ponto-de-corte deliberado: a aposta é que aumentar v0.6.0 em ~2 semanas pra entregar full UX vale mais do que cortar v0.6.0 cedo e empurrar UX pra v0.6.1, v0.6.2, etc. — porque a UX é o que diferencia stealth-vps dos `bash <(curl ...)` scripts existentes; entregar metade não move a agulha.

---

## Critical files (referência rápida)

Onde mais código vai mudar:

- [`ansible/roles/stealth-vps/tasks/xray.yml`](../../ansible/roles/stealth-vps/tasks/xray.yml) — refactor em v0.5.9.
- [`ansible/roles/stealth-vps/tasks/main.yml`](../../ansible/roles/stealth-vps/tasks/main.yml) — orquestração panel-vs-headless.
- [`ansible/roles/stealth-vps/templates/stealth-vps-metrics-update.py.j2`](../../ansible/roles/stealth-vps/templates/stealth-vps-metrics-update.py.j2) — extrai `threex_client.py`.
- [`ansible/roles/stealth-vps/templates/stealth-vps-credentials.txt.j2`](../../ansible/roles/stealth-vps/templates/stealth-vps-credentials.txt.j2) — adiciona seções bot/sub.
- [`scripts/install.sh`](../../scripts/install.sh) — TUI + env-var fallback.
- [`ansible/roles/stealth-vps/defaults/main.yml`](../../ansible/roles/stealth-vps/defaults/main.yml) — toda var nova.

Padrões exemplares a seguir:

- [`ansible/roles/stealth-vps/tasks/panel.yml`](../../ansible/roles/stealth-vps/tasks/panel.yml) — padrão de state.yml + systemd unit + smoke test.
- [`ansible/roles/stealth-vps/tasks/observability.yml`](../../ansible/roles/stealth-vps/tasks/observability.yml) — padrão de Python script + systemd timer.
- `tests/molecule/default/molecule.yml` + `verify.yml` — padrão de novo scenario.
