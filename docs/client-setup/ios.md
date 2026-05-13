# iOS client setup

> Full step-by-step walkthrough lands with v0.3.0. The notes below get you connected today; they just haven't been pen-tested across every iOS version yet.

iOS does not allow open-source proxy clients in the public App Store unless they ship through the official review process, so the practical options are paid apps or the open-source Hiddify build.

| Client | Reality | Hysteria2 | Cost | Notes |
|---|---|---|---|---|
| **Shadowrocket** | ✅ | ✅ | Paid (~US$3, one-off) | Most popular; the de-facto choice for iOS |
| **Streisand** | ✅ | ✅ | Paid (~US$3, one-off) | sing-box based, similar feature set |
| **Hiddify** | ✅ | ✅ | Free | sing-box based, open-source, on the App Store in most regions |

> In some regions (mainland China, Russia, India at times) the local App Store hides these apps. You'll need an Apple ID set to a different region — that's an App Store policy issue we can't work around from the server side.

## Quick start

1. Install one of the clients above from the App Store.
2. On the server, copy the URI you want from `/root/stealth-vps-credentials.txt` — `vless://…` for Reality, `hysteria2://…` for Hysteria2.
3. In the iOS client, paste the URI from the clipboard (every client has an `Import from clipboard` or `+` button accepting URIs).
4. Tap connect → grant the "VPN configuration" permission iOS asks for once.
5. Verify in Safari: `https://ifconfig.me` should show the VPS IP.

## Behaviour notes

- **Reality URI** is identical to the desktop one. Shadowrocket parses every parameter (flow, pbk, sid, sni, fp). No manual fields.
- **Hysteria2 URI** with `,min-max` port range works in all three clients (port hopping).
- If the server is on self-signed TLS, the URI carries `&insecure=1` — Shadowrocket and Hiddify both honour it per-profile.

## Why no auto-config profile (mobileconfig)?

iOS supports VPN provisioning profiles, but they only cover IKEv2 / IPsec / WireGuard — none of the protocols stealth-vps ships. The URI + paste flow is the only path that works for VLESS-Reality and Hysteria2 on iOS today.
