# Onboarding bridge (v0.12.0+)

The onboarding bridge is a static web page that turns a subscription token into a **one-tap client import**. Instead of handing a user a raw base64 subscription URL and hoping they know which app to install + how to paste it, you hand them one link: they tap it on their phone, tap "Import to Hiddify", and they're connected.

Off by default. Design rationale (ADRs, security model): [`internal/roadmap-v0.12-onboard-bridge.md`](internal/roadmap-v0.12-onboard-bridge.md).

## What the user sees

Tapping `https://vpn.example.com/.well-known/stealth-vps-onboard/<token>` on a phone shows:

```text
┌─────────────────────────────────────┐
│  Connect to your VPN                │
│                                     │
│  Detected platform: android         │
│                                     │
│  [ Import to Hiddify ]              │  ← hiddify://install-sub?url=…
│  [ Import to NekoBox ]              │  ← sn://subscription?url=…
│  [ Import to V2Box ]                │  ← v2box://install-sub?url=…
│  [ Import to sing-box ]             │  ← sing-box://import-remote-profile?url=…
│                                     │
│      ▛▀▙  (QR — scan to import)     │
│      ▙▄▟                             │
│                                     │
│  [ Copy subscription URL ]          │
│  ▸ Show server details              │
└─────────────────────────────────────┘
```

The page detects the OS (Android / iOS / macOS / Windows / Linux) and orders the client buttons accordingly. Every button is a deep-link that opens the app and imports the subscription directly — no manual paste. The QR encodes the subscription URL for camera-scan import. "Copy subscription URL" is the universal fallback for any client.

## Enabling it

The bridge rides on the Caddy subscription endpoint, so it needs a publicly-reachable subscription:

```yaml
stealth_vps_subscription_enabled: true
stealth_vps_subscription_expose: true     # public :443 site
stealth_vps_domain: vpn.example.com        # required
stealth_vps_onboard_enabled: true
# optional override (default shown):
stealth_vps_onboard_path: /.well-known/stealth-vps-onboard
```

The role asserts at converge time that `onboard_enabled` requires `subscription_expose` + a domain — the deep-links embed a public subscription URL, so a loopback-only endpoint makes them useless.

After `s-vps update`, the static bundle lands at `/var/lib/stealth-vps/onboard/` and Caddy serves it at `<onboard_path>/*` with a strict CSP + `X-Frame-Options: DENY`.

## Handing out the link

```bash
# CLI
$ sudo s-vps user add alice
✓ added user 'alice'
  ...
  subscription URL: https://vpn.example.com/.well-known/stealth-vps-sub/<token>
  onboarding link : https://vpn.example.com/.well-known/stealth-vps-onboard/<token>

$ sudo s-vps user onboard-url alice     # just the onboarding link
https://vpn.example.com/.well-known/stealth-vps-onboard/<token>
```

Via the bot:

```text
/onboard alice
  → DMs the onboarding link + a QR image (when qrencode is installed on the host)
/sub alice
  → shows the sub URL + the onboarding link
```

Send the **onboarding link** to non-technical users; send the raw **subscription URL** to users who know their client and prefer to paste it.

## How it works (no backend, no per-user file)

The bridge is a single static bundle — the same HTML/JS/CSS for every user. The token lives only in the URL. `onboard.js` reads the token from `location.pathname`, derives the subscription URL (`<origin>/.well-known/stealth-vps-sub/<token>`), and renders the deep-links + QR entirely client-side. The only network call is the lazy "Show server details" expander (fetches the sub bundle to list per-node/per-protocol entries) — the core one-tap flow makes zero network calls beyond loading the page.

No third-party requests: no CDN, no web fonts, no analytics. The QR encoder is vendored in-tree (`vendor/qrcode.min.js`). A network observer sees one TLS request to your domain — the same as fetching the subscription directly.

## Security

The onboarding URL embeds the subscription token — **identical exposure to handing out the subscription URL directly**. The bridge is a friendlier wrapper, not a new trust boundary. Treat the onboarding link with the same care.

- **Token rotation:** `s-vps sub revoke <label>` rotates the token → the old onboarding link's deep-links point at a dead subscription. Same rotation story as the raw sub URL.
- **No token oracle:** Caddy serves the page for *any* `/onboard/*` path (SPA fallback), so a scanner can't tell a valid token from an invalid one by the page alone — only the in-app subscription fetch distinguishes them.
- **Hardening headers:** strict CSP (everything same-origin, `data:` only for the QR canvas), `X-Frame-Options: DENY` (no phishing iframe), `nosniff`, `no-referrer`.

## QR validation note

The QR encoder is a compact in-tree implementation (no JS build step, no CDN — see [`files/onboard/vendor/README.md`](../ansible/roles/stealth-vps/files/onboard/vendor/README.md)). It's structurally tested in CI but not scan-validated there (the repo has no JS runtime). **Before relying on QR import, open the onboarding page in a browser and scan it once with a real client app.** If the encoder ever fails, the page degrades gracefully to "QR unavailable — use a button or copy the URL", so import-by-button always works.

## Platform → client recommendations

| Platform | Buttons shown (in order) |
|----------|--------------------------|
| Android | Hiddify, NekoBox, V2Box, sing-box |
| iOS | Hiddify, Streisand, V2Box, sing-box |
| macOS | Hiddify, V2Box, sing-box |
| Windows | Hiddify, V2Box, sing-box |
| Linux | Hiddify, sing-box |
| (unknown) | all of the above |
