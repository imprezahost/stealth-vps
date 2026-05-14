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

## v0.6.0 — TUI install + Telegram bot + subscriptions (panel mode)

### Scope (in)

- `install.sh` interativo via whiptail (com fallback graceful para env-var quando piped).
- Index de usuários `users.index.json` — escrito por toda task que cria/revoga cliente Reality.
- Bot Telegram (`stealth-vps-bot.service`) com comandos: `/status`, `/diagnose`, `/creds`, `/user add|list|revoke`, `/sub|sub revoke`.
- Endpoint de subscription via Caddy servindo `/var/lib/stealth-vps/subscriptions/*.txt`.

### Scope (out)

- Modo headless / xray standalone (v0.7.0).
- Hysteria2 per-user (v0.7.0 — 3X-UI v2.9.4 não gerencia Hysteria2; em v0.6.0 todos os clientes Hysteria2 compartilham a mesma senha, documentado nas release notes).
- Comandos do bot com janela temporal (`/user list --since X`). Bot expõe só "totais correntes" para não criar dívida que v0.7.0 não consegue pagar sem persistência adicional.
- Web wizard. Decisão firme: conflita com pitch "auditable / IaC-native".

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

5. **`install.sh` interativo**:
   - Detecta `[ -t 0 ] && [ -t 1 ]`. Se ambos TTY → whiptail. Senão → env-var mode (path atual, intocado).
   - Sem `< /dev/tty` magic. Cloud-init / Terraform / Pulumi seguem env-var.
   - Prompts (ordem): domain → LE email (só se domain setado) → SSH port → Reality dest → habilitar bot? (token + admin chat IDs).
   - Escreve respostas em `/root/stealth-vps-install-vars.yml` chmod 0600; passa para `ansible-pull -e @<file>`.
   - Header comment documenta o contrato env-var como estável pra cloud-init.

### Files (new)

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

### Files (changed)

- `scripts/install.sh` — TUI + env-var contract documentado.
- `ansible/roles/stealth-vps/tasks/main.yml` — inclui `users_index.yml`, `bot.yml`, `subscription.yml` (com `when:` no opt-in).
- `ansible/roles/stealth-vps/defaults/main.yml` — `stealth_vps_bot_enabled`, `stealth_vps_bot_token`, `stealth_vps_bot_admin_chat_ids`, `stealth_vps_subscription_enabled`, `stealth_vps_subscription_expose`, `stealth_vps_subscription_path`.
- `ansible/roles/stealth-vps/templates/stealth-vps-metrics-update.py.j2` — importa de `threex_client.py`.
- `ansible/roles/stealth-vps/templates/stealth-vps-credentials.txt.j2` — adiciona seção "Telegram bot" + "Subscription URL" quando habilitados.
- `ansible/roles/stealth-hardening/tasks/ufw.yml` — abre 443/TCP só quando `stealth_vps_subscription_expose=true`.
- `tests/molecule/default/converge.yml` / `verify.yml`.
- `CHANGELOG.md`, `README.md`, `README.zh-CN.md`, `docs/operations.md`.

### Tests

- **Molecule**: novo `verify-bot.yml` afirma (a) `stealth-vps-bot.service` ativo, (b) `/etc/stealth-vps/bot.env` chmod 0600, (c) `/opt/stealth-vps/venv/bin/python` existe, (d) `caddy` serving 127.0.0.1:8443 retorna 404 para path inválido e 200 para token pre-seeded.
- **Real-VPS**: deploy completo em Hetzner CAX11 via TUI `install.sh`. BotFather token real, criar 2 usuários, baixar subs em v2rayNG + sing-box, confirmar conexão Reality + Hysteria2, `/metrics` mostrando per-client counters.

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

```
v0.5.9   sprints 16+17:
         - sprint 16: scripts/release.sh                       (✓ branch pushed)
         - sprint 17: refactor xray.yml                        (todo)
v0.6.0   TUI installer + bot Telegram + sub endpoint
v0.6.1   Polish: i18n EN+zh-CN nos strings do bot, /diagnose enriquecido
v0.7.0   Headless mode + s-vps CLI + migração
v0.7.1   Métricas headless via Xray gRPC stats (se não couber em v0.7.0)
v1.0.0   Probe-resistance CI full + JA4 + signed releases
```

Estimativa: v0.5.9 ~2 dias, v0.6.0 ~1-2 semanas, v0.6.1 ~3 dias, v0.7.0 ~2-3 semanas, v0.7.1 ~3 dias. Cumulativo ~5-6 semanas full-time.

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
