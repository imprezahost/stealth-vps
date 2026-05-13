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
stealth_hardening_ufw_extra_ports:
  - { port: 9100, proto: tcp, comment: "node_exporter from prometheus host only" }
```

…then on the firewall side, restrict source IP via `ufw allow from <prom-host-ip> to any port 9100`. (Future v0.3.0 will add a source-ip variant to the role.)

## Dashboards

For now, import the public **Node Exporter Full** dashboard (Grafana.com ID `1860`) — it covers everything `prometheus-node-exporter` reports. A stealth-vps-specific dashboard (panel inbound stats, Hysteria2 metrics, fail2ban ban rate) lands in v0.3.0.

## What's not in scope yet

- **Xray / Hysteria2 Prometheus endpoints**: both upstreams support exposing metrics; the role doesn't wire them up yet because the panel + standalone setups need different configuration. v0.3.0.
- **Alert rules**: cert expiry, login flood, bandwidth spike, fail2ban ban rate. v0.3.0.
- **Grafana shipped with the role**: anti-pattern in a fleet. Run Grafana once, centrally.

## Why this directory exists

Most stealth-VPS tutorials stop at "it's installed, here's your config link." After a few weeks you're flying blind — you can't tell if you're being probed, throttled, or burning bandwidth on the wrong destination. Shipping the baseline exporter as a deliberate part of the role is a small differentiator, not an afterthought.
