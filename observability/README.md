# Observability

How to see what's actually happening on a stealth-vps host — CPU, memory, network, login attempts, blocked IPs — without piecing the stack together from scratch.

## What ships in v0.2.0

The `stealth-vps` role installs **`prometheus-node-exporter`** bound to `127.0.0.1:9100` by default. The exporter is left *not* exposed externally; pull metrics from a central Prometheus via SSH tunnel, or override `stealth_vps_observability_listen` if you want it open.

```
observability/
├── grafana/
│   └── dashboards/      # JSON dashboards (planned — v0.3.0)
├── prometheus/
│   └── exporter/        # (room for stealth-vps-specific exporters — v0.3.0)
└── alerts/              # (room for alert rule templates — v0.3.0)
```

## Pulling metrics from a central Prometheus

The recommended pattern is one Prometheus + one Grafana **outside** the stealth fleet, scraping each stealth-vps over a private connection.

### Option A: SSH tunnel from the Prometheus host

On the Prometheus host:

```bash
ssh -fN -i /path/to/key -p 22550 \
  -L 9100:127.0.0.1:9100 \
  root@<stealth-vps>
```

Then in `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: stealth-vps-tokyo
    static_configs:
      - targets: ['localhost:9100']
        labels:
          host: tokyo
```

### Option B: Expose with a UFW source filter

Override in inventory:

```yaml
stealth_vps_observability_listen: "0.0.0.0:9100"
stealth_vps_observability_allow_from:
  - 198.51.100.42         # the central Prometheus host
  - 192.0.2.0/24          # a whole admin VLAN
```

The role auto-creates `ufw allow from <ip> to any port 9100 proto tcp` for each entry. No rule is added when `stealth_vps_observability_allow_from` is empty, so leaving it unset means the port is only reachable from loopback (default).

## Dashboards

Two dashboards work together:

- **Host metrics** — import Grafana.com ID **`1860`** (Node Exporter Full). Covers everything `prometheus-node-exporter` reports out of the box.
- **stealth-vps protocol metrics** — import [`grafana/dashboards/stealth-vps-overview.json`](grafana/dashboards/stealth-vps-overview.json) from this repo. Panels for Reality inbound up/down, top-N per-client traffic, Hysteria2 tx/rx per client, online clients, and scrape-error indicators.

Both share the same scrape target (`:9100`) — the stealth-vps panel reads the metrics that `stealth-vps-metrics-update.py` writes into the node_exporter textfile collector dir.

### Importing the stealth-vps overview

1. Grafana → Dashboards → New → Import → Upload JSON file → pick `stealth-vps-overview.json`.
2. Pick the Prometheus datasource that scrapes your stealth-vps fleet.
3. The `Host` variable lists all instances reporting `stealth_vps_last_scrape_timestamp` — `$__all` works for fleet-wide views.

### Metric reference

| Series | Type | Source | Labels |
|---|---|---|---|
| `stealth_vps_last_scrape_timestamp` | gauge | updater itself | `instance` |
| `stealth_vps_panel_scrape_error` | gauge | updater (3X-UI API call) | `instance` |
| `stealth_vps_hysteria_scrape_error` | gauge | updater (Hysteria2 API call) | `instance` |
| `stealth_vps_cert_scrape_error` | gauge | updater (openssl on the TLS file) | `instance` |
| `stealth_vps_fail2ban_scrape_error` | gauge | updater (`fail2ban-client status`) | `instance` |
| `stealth_vps_inbound_{up,down}_bytes` | counter | 3X-UI panel REST API | `inbound_id`, `remark`, `protocol`, `port` |
| `stealth_vps_inbound_enabled` | gauge | 3X-UI panel | same |
| `stealth_vps_client_{up,down}_bytes` | counter | 3X-UI panel `clientStats[]` | inbound labels + `client_email` |
| `stealth_vps_hysteria_online_clients` | gauge | Hysteria2 `/online` | `instance` |
| `stealth_vps_hysteria_{tx,rx}_bytes` | counter | Hysteria2 `/traffic` | `client_id` |
| `stealth_vps_cert_expiry_seconds` | gauge | `openssl x509 -enddate` (`-1` when no cert configured) | `cert="le-fullchain"` |
| `stealth_vps_fail2ban_currently_banned` | gauge | `fail2ban-client status <jail>` | `jail` |
| `stealth_vps_fail2ban_total_banned` | counter | same | `jail` |
| `stealth_vps_fail2ban_currently_failed` | gauge | same | `jail` |
| `stealth_vps_fail2ban_total_failed` | counter | same | `jail` |

## Alert rules

[`prometheus/alerts/stealth-vps.rules.yml`](prometheus/alerts/stealth-vps.rules.yml) is a drop-in for your central Prometheus's `rule_files:` glob. Includes:

- `StealthVpsCertExpiringSoon` / `…Critical` — pages 7 days / 24h out from expiry. Filters out hosts where no LE cert is configured (the metric is `-1`).
- `StealthVpsPanelScrapeError` / `StealthVpsHysteriaScrapeError` — flags when the metrics updater can't read one of the upstream APIs for >5min.
- `StealthVpsScrapeStale` — flags when the updater itself stops firing (file timestamp >180s old).
- `StealthVpsFail2banBanSpike` — >30 new bans/hour averaged over 10m, by jail.
- `StealthVpsCurrentlyBannedHigh` — >100 IPs currently in a jail's ban list.
- `StealthVpsInboundTrafficSpike` — 5m rate > 3× the 1h baseline AND > 1 MB/s, by inbound. Catches client loops and misconfigurations without paging on legitimate growth.

Tune the thresholds / `for:` durations to your fleet's traffic profile and route via Alertmanager.

## What's not in scope yet

- **Xray / Hysteria2 Prometheus endpoints**: both upstreams support exposing metrics; the role doesn't wire them up yet because the panel + standalone setups need different configuration. v0.3.0.
- **Alert rules**: cert expiry, login flood, bandwidth spike, fail2ban ban rate. v0.3.0.
- **Grafana shipped with the role**: anti-pattern in a fleet. Run Grafana once, centrally.

## Why this directory exists

Most stealth-VPS tutorials stop at "it's installed, here's your config link." After a few weeks you're flying blind — you can't tell if you're being probed, throttled, or burning bandwidth on the wrong destination. Shipping the baseline exporter as a deliberate part of the role is a small differentiator, not an afterthought.
