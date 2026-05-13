# macOS client setup

> Full step-by-step walkthrough lands with v0.3.0. The notes below get you connected today; they just haven't been pen-tested across every macOS version yet.

| Client | Reality | Hysteria2 | Cost | Notes |
|---|---|---|---|---|
| **Hiddify Desktop** | ✅ | ✅ | Free | sing-box based, open-source, cross-platform |
| **V2Box** | ✅ | ✅ | Paid (Mac App Store) | Polished UI, native macOS app |
| **NekoBox (nekoray)** | ✅ | ✅ | Free | Same code as the Windows/Linux build; runs on macOS but UI feels Linux-y |
| **Shadowrocket** | ✅ | ✅ | Paid | Yes, the iOS app runs on Apple Silicon too — same paste-and-go flow |

## Quick start

1. Install one of the clients above.
2. On the server, copy the URI you want from `/root/stealth-vps-credentials.txt`.
3. In the client, **Import from clipboard** (every option supports it).
4. Connect. macOS will prompt for VPN configuration approval once.
5. Verify in Safari: `https://ifconfig.me` should show the VPS IP.

## TUN vs system proxy

- **Hiddify** and **V2Box** default to TUN mode and capture all traffic.
- **NekoBox** defaults to HTTP/SOCKS proxy on `127.0.0.1:2080` — you need to enable TUN mode manually (Settings) or point browsers/apps at the proxy.
- macOS uses `utun*` interfaces for TUN — no kernel extension to install, no admin prompt beyond the standard VPN permission.

## Notes

- **Reality + Hysteria2 URIs** work identically to the Windows/Android clients — the schema is portable.
- **Port hopping** (Hysteria2 URI with `,min-max`): supported by all four clients.
- **Self-signed TLS**: the `&insecure=1` flag in the URI is honoured per-profile.

## Why no native macOS preference pane?

VPN preference panes (`.mobileconfig` payloads) cover only IKEv2 / IPsec / WireGuard / L2TP. VLESS-Reality and Hysteria2 require a userspace client, which is why the apps above ship a standalone GUI.
