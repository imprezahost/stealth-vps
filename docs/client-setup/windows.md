# Windows client setup

> Placeholder — full walkthrough lands with v0.1.0.

Recommended clients:

- **NekoBox for PC** — [GitHub](https://github.com/MatsuriDayo/nekoray) — sing-box based, supports everything
- **v2rayN** — [GitHub](https://github.com/2dust/v2rayN) — most popular Windows client
- **Hiddify** — [GitHub](https://github.com/hiddify/hiddify-next) — cross-platform

## Importing your config

The `stealth-vps` install writes connection URIs to `/root/stealth-vps-credentials.txt`. Copy the URI string and use the client's `Import from clipboard` function, or paste in manually.

## TUN mode / system-wide

For system-wide routing (so all apps go through the tunnel, not just browser traffic), enable **TUN mode** in NekoBox or v2rayN. The first time you do this Windows will prompt to install a TAP/WinTUN driver.

## Troubleshooting

(To be documented.)
