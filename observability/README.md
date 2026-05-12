# Observability

This directory ships ready-to-use Prometheus exporters, Grafana dashboards, and alerting templates so you can see what's actually happening on your VPS without piecing together a monitoring stack from scratch.

## What's here

```
observability/
├── grafana/
│   └── dashboards/        # JSON dashboards importable into Grafana
├── prometheus/
│   └── exporter/          # systemd-managed exporter for Xray/Hysteria2/system
└── alerts/                # alert rule templates (Discord/Telegram webhooks)
```

## Status

Skeleton only in v0.1.0-dev. Dashboards and exporter implementation land before the v0.1.0 tag.

The intent:

- One **system dashboard** (CPU, memory, network, disk, load) — sourced from `node_exporter`
- One **traffic dashboard** (per-user upload/download, connection count, top destinations) — sourced from Xray stats API + Hysteria2 metrics
- Alert templates for unusual login attempts, fail2ban bans, certificate expiry, and bandwidth spikes

## Why this is in the project

Most stealth-VPS tutorials stop at "it's installed, here's your config link." After a few weeks you're flying blind — you have no idea if you're being probed, throttled, or burning bandwidth on the wrong destination.

Shipping a working observability layer in the same release artifact is a deliberate differentiator. If you don't want it, the role lets you skip it; the default leaves it enabled.
