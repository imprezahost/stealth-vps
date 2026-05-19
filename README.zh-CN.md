# stealth-vps

> ⚠️ **翻译版本滞后于英文版多个 release。** 本中文文档由维护者从早期版本(v0.4.3)的英文 [README.md](README.md) 机器辅助翻译,自 v0.5.x / v0.6.x / v0.7 / v0.8 起新增的功能尚未同步到中文版。安装命令链接已更新到当前 release(v0.8.0),但功能描述、roadmap 表格等仍停留在 v0.5.1 时期。**功能清单与最新变更请参阅英文 [README.md](../README.md) 与 [CHANGELOG.md](CHANGELOG.md)。** zh-CN 完整重审已列入 v1.0 计划。

---

> **状态: v0.8.0(alpha)。** 当前版本继续包含 v0.5.x / v0.6.x / v0.7 所有基线功能(VLESS-Reality + Hysteria2、3X-UI 面板或无面板模式、Let's Encrypt 自动签发、SSH/UFW/fail2ban 加固、Spamhaus DROP、内核调优、amd64+arm64、Prometheus 可观测性、`s-vps` 运维 CLI、可选 Telegram 机器人、可选 Caddy 订阅端点)。**v0.8 新增运维工具集 + IaC 三件套**:`s-vps user purge LABEL`(硬删除,幂等)+ `s-vps user rotate LABEL`(重新颁发凭据,保留 label + created_at 审计锚点)。Pulumi 新增 AWS / DigitalOcean / Vultr / Proxmox VE 四个示例,与 Terraform 示例树完全对应。**独立的 Python 和 Go cloud-init builder**(`tools/cloud-init-builder/`)— 纯标准库,与 TypeScript 源代码字节级一致,可嵌入任意 IaC 工具链。**工具链共有 213 个自动化测试**(194 pytest + 9 Python builder + 10 Go builder)。详见英文 [CHANGELOG.md](CHANGELOG.md) 与 [docs/headless-mode.md](docs/headless-mode.md)。

一个可复用的工具集,用于在受限网络环境中搭建注重隐私的 VPS。在 3X-UI 面板背后部署 VLESS-Reality + Hysteria2,带合理的安全加固、真正可用的 fail2ban 配置,以及内置的可观测性方案。

面向那些希望对自己的环境进行审计、版本锁定、可重复部署、并且信任所部署内容的人 —— 而不是另一个不透明的 `bash <(curl ...)` 一键脚本。

---

## 为什么再做一个?

这个生态中已经有不少 shell 安装器(`mack-a/v2ray-agent`、`3x-ui`、`Hiddify-Manager`)。本项目**不试图替代它们**。它面向一个更窄、更具体的群体:

- 你想用**幂等的 Ansible**,以便可预测地重新部署或恢复。
- 你想要在任何 hypervisor 上都能用、无需手动交互的 **cloud-init**。
- 你想要**真正在 3X-UI 上工作的 fail2ban**(上游一个长期痛点)。
- 你想要**宽松的 MIT 协议**(而非 AGPL 病毒式协议),让服务商和运维人员能在没有法律摩擦的前提下采用。
- 你想要**遵循 semver 的版本发布**和一份你能读懂的 changelog。
- **Prometheus + Grafana** 可观测性组件已在 v0.2.0 路线图中落地。

如果你更愿意粘贴一行命令就走,那么前面那些项目对你来说更合适。

---

## 安装内容

- **Xray-core** 配合 VLESS-Reality(借用真实站点的 TLS 握手;对抗主动探测)
- **Hysteria2**(基于 QUIC,将流量伪装成访问真实站点的 HTTP/3 流量;**支持可配置的 UDP 端口跳跃**)
- **3X-UI** 面板(多用户、流量限额、过期时间、订阅链接)
- **TLS**: 当你设置了 `stealth_vps_domain` 时,通过 `acme.sh`(HTTP-01 standalone)可选地签发 **Let's Encrypt** 证书 —— Hysteria2 和 3X-UI 面板都使用真实证书;未设置时回退到自签名
- **内核调优**: BBR + fq qdisc、更大的 socket 缓冲、TCP Fast Open
- **加固**: SSH 监听非默认端口、仅密钥认证、配置真正能识别 3X-UI 的 fail2ban filter、UFW(默认拒绝入站)
- **信誉过滤**: 将 **Spamhaus DROP** 列表加载到 ipset,在 UFW 的 INPUT 链顶部丢弃,每日刷新
- **补丁**: `unattended-upgrades` 配合 security-origin 过滤和 Package-Blacklist hook
- **可观测性**: `prometheus-node-exporter` 基线(默认仅监听 loopback;按需暴露或通过 SSH 隧道访问)
- **IPv6 双栈**(默认启用)

---

## 四种使用方式

按你的工作流挑一种。四种方式应用同一套配置。

### 1. 一键安装(`install.sh`)

适合一台刚开通、只想跑起来的 VPS:

```bash
curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.8.0/scripts/install.sh | bash
```

这是一层轻量的封装脚本,它启动 Ansible 并对本仓库运行 `ansible-pull`。URL 锁定到 v0.6.4 发布标签,因此你部署的就是本 changelog 所对应的代码。若想安装其他版本,把 URL 中的 tag 换掉,**并且**传入对应的 `STEALTH_VERSION`:

```bash
curl -sSL https://raw.githubusercontent.com/imprezahost/stealth-vps/v0.8.0/scripts/install.sh \
  | STEALTH_VERSION=v0.8.0 bash
```

### 2. Ansible(推荐用于可重复部署)

```bash
git clone https://github.com/imprezahost/stealth-vps.git
cd stealth-vps
cp ansible/inventory/example.yml ansible/inventory/hosts.yml
# 编辑 ansible/inventory/hosts.yml,填入你的 VPS 信息
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbooks/site.yml
```

### 3. Cloud-init(适用于 hypervisor)

在创建 VPS 时,把 `cloud-init/stealth-vps.yaml` 作为 user-data 投入即可。可在 Proxmox、任何支持 cloud-init 的云平台、以及任何现代 hypervisor 上工作。

### 4. Terraform 模块(`v0.5.0`+)

Provider-agnostic —— 从类型化的 HCL 输入(SSH 公钥、域名、版本锁定、Reality dest、自由形式的 Ansible 变量)生成 cloud-init `user_data`。把输出传给任意云厂商的"创建实例"资源即可。

```hcl
module "stealth_vps_bootstrap" {
  source = "github.com/imprezahost/stealth-vps//terraform/modules/stealth-vps?ref=v0.8.0"

  stealth_version = "v0.8.0"
  ssh_public_key  = file("~/.ssh/id_ed25519.pub")
  domain          = "vpn.example.com"
  letsencrypt_email = "ops@example.com"
}

resource "hcloud_server" "vps" {  # 或者 aws_instance、digitalocean_droplet、vultr_instance……
  # ...
  user_data = module.stealth_vps_bootstrap.cloud_init
}
```

Hetzner Cloud 的端到端示例位于 [`terraform/examples/hetzner/`](terraform/examples/hetzner/)。完整参考见 [`docs/terraform.md`](docs/terraform.md)。

---

## 在哪里运行

任何 Debian 12 或 Ubuntu 22.04+ 的 VPS 都可以工作。如果主要面向中国大陆用户,请选择直连中国电信(理想情况是 CN2 GIA)、中国联通和 ChinaNet 的服务商以获得最佳性能。

### 赞助方基础设施

本项目由 **[Impreza Host](https://imprezahost.com)** 构建和维护。如果你需要一台跑这个模板效果良好的 VPS,我们在洛杉矶部署了一支直连下述线路的机队:

- **CN 路由**: 中国电信(CN2)、中国联通、ChinaNet
- **Tier-1 主干**: CenturyLink、Cogent、GTT、NTT

我们也在冰岛、瑞士、荷兰、俄罗斯、罗马尼亚等地提供面向隐私场景的离岸节点,服务那些需要超出常规监视联盟司法管辖的客户。

- 支持 USDT-TRC20 付款(无需信用卡)
- 标准套餐免 KYC
- 开源理念: 本仓库就是我们回馈社区的方式之一

赞助关系并不改变代码本身 —— 同一份模板可以在任何服务商的 VPS 上运行。我们只是恰好运营着适合这一场景的基础设施。

---

## 项目状态与路线图

| 版本 | 范围 | 状态 |
|---|---|---|
| v0.1.0 | Ansible role(内核 + 面板 + Reality + Hysteria2)、hardening role、cloud-init、`install.sh` | 已发布 2026-05-13 |
| v0.2.0 | Let's Encrypt 自动化、Spamhaus DROP、Hysteria2 端口跳跃、Android + Windows 配置指南、`node_exporter` 基线、Molecule 场景 | 已发布 2026-05-13 |
| v0.3.0 | 每协议 Prometheus 指标 + Grafana 仪表板 + 告警规则、多平台 Molecule 矩阵(Debian 12 + Ubuntu 22.04/24.04)、`:9100` 源 IP 过滤、iOS + macOS 完整配置指南 | 已发布 2026-05-13 |
| v0.4.0 | arm64 打包支持(Oracle Ampere / Graviton / Hetzner CAX)、反向镜像自动化(GitHub PR → GitLab CI → GitHub commit status)、probe-resistance 测试套件骨架(5 个场景、2 个可运行脚本) | 已发布 2026-05-13 |
| v0.4.1 | 填充 probe-resistance 场景 02 + 03 脚本(7 特征 TLS shape 比对;HTTP 响应 shape 比对);4/5 场景端到端可运行 | 已发布 2026-05-13 |
| v0.4.2 | 热修复: 替换失效的 `get.imprezahost.com/stealth` URL 为锁定到 release tag 的 raw GitHub URL;`STEALTH_VERSION` 默认值从 `v0.1.0` 提升至 `v0.4.2`,使一键安装真正部署当前版本 | 已发布 2026-05-13 |
| v0.4.3 | iOS + macOS 端到端 pen-test 验证、zh-CN README 重写、GitLab shell-executor runner 修复 | 进行中 |
| v0.5.0 | Provider-agnostic Terraform 模块(`terraform/modules/stealth-vps/`)+ Hetzner Cloud 端到端示例;cloud-init 版本漂移修复(`v0.1.0 → v0.5.0`);README"三种方式"→"四种方式",新增 Terraform 路径 | 已发布 2026-05-13 |
| **v0.5.1** | `tls_fingerprint_compare.py` 中通过 stdlib `ssl.MemoryBIO` 实现的字节级 **JA3 + JA3S**(无需 scapy / tlslite-ng 依赖);纯 stdlib TLS record + handshake 解析器;场景 02 文档明确 JA3 vs JA3S 语义和 TLS 1.3 `EncryptedExtensions` 的限制 | **已发布 2026-05-14** |
| v0.5.x | JA4 + JA4S(FoxIO 2023+ 规范)、HTTP/2 SETTINGS 帧比对、更多 Terraform 示例(AWS / DigitalOcean / Vultr / Proxmox)、Pulumi 参考实现 | 已发布 2026-05-14 (v0.5.8) |
| v0.5.9 | **v0.6 前置准备**:版本号一次性同步脚本 `scripts/release.sh`、`xray.yml` 拆分为面板无关 + 面板专用以为 v0.7 无面板模式铺路 | 已发布 2026-05-15 |
| v0.6.0 | **Caminho C 全 UX**:交互式 whiptail 安装器、Reality URI 终端 QR、LE 前 DNS 预检、部署后 ✓/✗/⚠ 健康检查、`s-vps` 运维 CLI、可选 Telegram 机器人、可选 Caddy 订阅端点、`users.index.json` schema | 已发布 2026-05-15 |
| v0.6.1 - v0.6.4 | **东京 VPS 烟雾测试驱动的 bug 修复**:面板 scheme 自动探测、`installer.env` ternary 修复、健康检查从 state 文件读端口、Hysteria UDP 端口监听检查的 `ss` 列号 bug | 已发布 2026-05-15 / 18 |
| v0.7.0 - v0.7.4 | **无面板模式(Headless mode)**:`panel_enabled=false` 时角色安装独立 Xray-core systemd unit + Hysteria2 每用户 `auth.userpass`。`stealth_vps.reloader` Python 模块从 `users.index.json` 重新渲染配置并重启服务。新增 `s-vps user add/revoke/list/show`、`s-vps reload`、`s-vps migrate from-3xui` CLI 动作。Telegram 机器人在 v0.7.4 接入 HeadlessBackend 派发 + sudoers 细粒度规则。v0.7.1/v0.7.2/v0.7.3 修复了东京 VPS 烟雾测试中暴露的回归(xray validate `-format=json`、installer.env 环境变量覆盖、xray + hysteria 都需要 restart 而非 reload)。 | 已发布 2026-05-18 / 19 |
| **v0.8.0** | **运维 UX + IaC 三件套**:`s-vps user purge`(硬删除,幂等)+ `s-vps user rotate`(重新颁发凭据,保留 label + created_at 审计锚点)。Pulumi 新增 AWS / DigitalOcean / Vultr / Proxmox VE 四个端到端示例。独立的 **Python 和 Go cloud-init builder** 端口(`tools/cloud-init-builder/`)— 纯标准库,与 TypeScript 源代码字节级一致。CI 新增 `go-test` 任务。工具链共有 213 个自动化测试。 | **已发布 2026-05-19** |
| v0.8.1 | CI 与生产环境对齐(Debian + ansible-core 2.19 第二个 molecule 任务)、机器人模块重构使其可测试(提取 `bot_core` 子模块)、arm64 测试机器准备(Hetzner CAX11)、zh-CN 文档同步 | 计划中 |
| v0.9.0 | `age` 加密备份/恢复、持续健康检查 Prometheus exporter、订阅 TTL、可选自动更新 | 计划中 |
| v0.10.0 | **多节点**:控制平面通过 SSH 推送 `users.index.json` 到 N 个数据节点,每节点独立的 Reality 密钥,订阅 bundle 包含所有节点的 URIs | 计划中 |
| v0.11.0 | WireGuard 回退 + Xray 协议扩展(XHTTP、VMess+WS、Trojan-Go、SS-2022) | 计划中 |
| v0.12.0 | 订阅 bridge 网页 UI(检测客户端 + 显示 QR + 深链 Hiddify Next / V2Box / NekoBox) | 计划中 |
| v0.13.0 / v0.14.0 | **原生 Android 客户端**(Kotlin),然后 **iOS**(Swift)。从零构建,不依赖第三方 fork | 远期 |
| v1.0.0 | 完整的 probe-resistance CI 套件(JA4 + JA4S + 黄金快照)、签名发布(cosign + GPG)、外部安全审计、zh-CN 母语审校 | 远期 |

实际发布进度以 [CHANGELOG](CHANGELOG.md) 为准。

---

## 文档

- [架构](docs/architecture.md) —— 安装的内容及各组件如何协同
- [运维](docs/operations.md) —— 日常操作: 凭据轮换、添加用户、升级
- [Terraform](docs/terraform.md) —— Provider-agnostic 模块 + Hetzner 示例(v0.5.0+)
- 客户端配置指南: [Android](docs/client-setup/android.md) · [iOS](docs/client-setup/ios.md) · [Windows](docs/client-setup/windows.md) · [macOS](docs/client-setup/macos.md)

English documentation: [README.md](README.md)

---

## 贡献

欢迎在 GitHub 上提交 PR 和 issue。日常开发在私有 GitLab 进行;GitHub 仓库是 release 镜像。工作流见 [CONTRIBUTING.md](CONTRIBUTING.md)。

关于安全漏洞披露,请见 [SECURITY.md](SECURITY.md)。

---

## License(协议)

MIT —— 见 [LICENSE](LICENSE)。

---

## Credits(致谢)

本项目依赖于上游优秀项目的工作:

- [XTLS/Xray-core](https://github.com/XTLS/Xray-core) —— Reality 协议
- [apernet/hysteria](https://github.com/apernet/hysteria) —— Hysteria2
- [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui) —— 面板
- `nodeseek`、`v2ex`、`linux.do`、`gfw.report` 上的隐私 / 反审查社区

---

## 术语对照(供译者审校时参考)

下列术语在本文档中保留英文原文,以便与上游项目和搜索结果对齐。母语者审校时可根据需要调整,但建议优先保持与 Chinese 反审查社区(`nodeseek` / `v2ex` / `linux.do`)的惯用表达一致。

| 英文 / 缩写 | 中文常见译法 | 在本文档中的取舍 |
|---|---|---|
| VLESS-Reality | 通常保留英文 | 保留 |
| Hysteria2 | 通常保留英文 | 保留 |
| 3X-UI | 通常保留英文 | 保留 |
| Xray-core | 通常保留英文 | 保留 |
| Ansible / cloud-init / Terraform / Pulumi | 通常保留英文 | 保留 |
| fail2ban / UFW / ipset / acme.sh | 通常保留英文 | 保留 |
| Prometheus / Grafana / node_exporter / Molecule | 通常保留英文 | 保留 |
| Spamhaus DROP | "Spamhaus 黑名单" 也可 | 保留英文 |
| BBR / fq qdisc / TCP Fast Open | 通常保留英文 | 保留 |
| QUIC / HTTP/2 / HTTP/3 / TLS / SSH | 通常保留英文 | 保留 |
| JA3 / JA3S / JA4 / JA4S | 通常保留英文 | 保留 |
| GitHub / GitLab / PR / commit / changelog | 通常保留英文 | 保留 |
| MIT / AGPL | 通常保留英文 | 保留 |
| amd64 / arm64 / x86_64 / aarch64 | 通常保留英文 | 保留 |
| Oracle Ampere / AWS Graviton / Hetzner CAX | 服务商品牌,保留英文 | 保留 |
| CN2 GIA | 通常保留英文 | 保留 |
| USDT-TRC20 / KYC | 通常保留英文 | 保留 |
| hardening | 加固 | 译为"加固" |
| port hopping | 端口跳跃 | 译为"端口跳跃" |
| kernel tuning | 内核调优 | 译为"内核调优" |
| panel | 面板 | 译为"面板" |
| reverse-mirror | 反向镜像 | 译为"反向镜像" |
| probe-resistance | (建议) 抗探测 / 抗主动探测 | 当前保留英文,留待审校决定 |
| shape comparison | (建议) 形态比对 | 当前译为"shape 比对" |
| roadmap | 路线图 | 译为"路线图" |
| shipped / planned | 已发布 / 计划中 | 译为"已发布"/"计划中" |
| pinned (a version) | (建议) 锁定 / 钉死 | 译为"锁定" |
| idempotent | (建议) 幂等的 | 译为"幂等的" |

**审校者备注**: 上面的"建议"列是机器翻译的初步选择,母语者请按需替换。中国大陆反审查社区(尤其 `nodeseek` 和 `linux.do`)对一些术语有自己的惯用法,与学术或工程文献的译法可能不同 —— 优先采纳社区惯用法。
