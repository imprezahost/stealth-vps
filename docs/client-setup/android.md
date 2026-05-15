# Android client setup

Two recommended clients cover everything stealth-vps installs:

| Client | Reality (VLESS) | Hysteria2 | Notes |
|---|---|---|---|
| **v2rayNG** | ✅ first-class | ⚠️ basic | most popular; great Reality support, Hysteria2 added recently |
| **NekoBox for Android** | ✅ | ✅ | sing-box based, more feature-complete; pick this if you want both protocols in one client |

You can install both side-by-side and switch as needed.

> All snippets below assume you already ran the role and have the
> connection URIs from `/root/stealth-vps-credentials.txt` on the
> server. The `vless://…` URI is for Reality; the `hysteria2://…` URI
> is for Hysteria2.

---

## Install

- **v2rayNG**: [Play Store](https://play.google.com/store/apps/details?id=com.v2ray.ang) · [GitHub releases](https://github.com/2dust/v2rayNG/releases) (recommended — the Play Store build can lag behind)
- **NekoBox**: [GitHub releases](https://github.com/MatsuriDayo/NekoBoxForAndroid/releases) (not on the Play Store)

Both are open-source and reproducible. Verify the signature/hash on the release page before sideloading the APK.

---

## v2rayNG — Reality (VLESS)

1. Open v2rayNG → tap **`+`** (top right) → **`Import config from clipboard`** (after you copied the `vless://…` URI), or **`Scan QR code`** if the server printed one.
2. The new entry appears with the remark from the URI fragment (default: `stealth-vps-reality`). Tap it to make it the active config.
3. Tap the bottom-right **V** button (or the system bottom-bar play icon) → grant the VPN permission once → connection should show **Connected** within a couple seconds.
4. **Verify**:
   - In a browser, open `https://ifconfig.me` → must show the VPS IP, not your carrier IP.
   - Open `https://dnsleaktest.com` → should not list local-ISP DNS servers.
   - The latency in v2rayNG's status row should be reasonable (anything under ~150 ms from Asia → Tokyo is fine).

### Reality settings you can verify in v2rayNG (Detail view)

- Protocol: `vless`
- Network: `tcp`
- Security: `reality`
- Flow: `xtls-rprx-vision`
- ServerName (SNI): the `dest` you configured (default `www.microsoft.com`)
- Fingerprint: `chrome`

If any of these are different, the URI got truncated during import — re-paste it carefully or scan the QR.

---

## NekoBox for Android — Reality and Hysteria2

NekoBox is sing-box-based and supports Hysteria2 natively, which v2rayNG only added recently. Use it especially if you want to test which protocol works better on your link.

### Import a profile

1. Open NekoBox → tap the gear icon → **`Profile`** or just the **`+`** menu in the main screen.
2. Use **`Import from clipboard`** after copying either URI (vless or hysteria2).
3. NekoBox parses both schemas; the profile lands in the list with the remark from the URI's `#fragment`.

### Connect

Tap the entry → tap the **lightning icon** in the bottom right → grant VPN permission once. State should flip to **Connected** within a few seconds.

### Hysteria2 specifics

If the server is using a Let's Encrypt cert (default when `stealth_vps_domain` is set on the server), the URI does **not** carry `insecure=1` and NekoBox validates the chain — same as a normal HTTPS connection.

If the server still uses a self-signed cert (test setups), the URI carries `&insecure=1`. NekoBox honours that flag: it skips cert validation **only for the matching profile**.

When port hopping is enabled on the server, the URI looks like:

```text
hysteria2://…@host:49440,20000-50000/?…
```

NekoBox automatically uses port 49440 first and randomises across the 20000-50000 range thereafter. Nothing to configure in the app.

---

## Troubleshooting

**"VPN profile created" but no traffic flows.**
The system rejected the VPN permission silently (common on some Xiaomi/MIUI ROMs). Open the client's settings → **`Service mode`** → switch to **`VPN service`** (not `Proxy only`) and try again.

**Reality connects but `ifconfig.me` still shows your local IP.**
v2rayNG's routing mode is set to `bypass mainland`. Toggle to **`Global`** (Settings → Routing → Predefined rules) and reconnect.

**Hysteria2 keeps disconnecting on mobile data.**
Some Chinese carriers throttle or block sustained UDP. Switch to v2rayNG + Reality (TCP-based) for cellular; keep Hysteria2 for Wi-Fi.

**"TLS handshake failed: bad certificate"** in NekoBox after enabling Let's Encrypt on the server.
The URI in `credentials.txt` is regenerated automatically with the right `sni=` and without `insecure=1`. Re-import the URI to pick up the change.

**Connection works but DNS leaks.**
In NekoBox: Settings → DNS → switch to **`Local DNS`** or set a remote resolver like `1.1.1.1`. v2rayNG: Settings → DNS → set a private DNS service.

---

## What to share with end-users

For multi-user deployments, the operator's credentials file
(`/root/stealth-vps-credentials.txt`) lists the URI directly. Either:

- Paste the URI into a private message
- Generate a subscription link from the 3X-UI panel (Inbounds → client → 3-dot menu → Subscribe → copy URL) — clients can refresh on their own and you can revoke per-client by disabling them in the panel
- Print a QR code from the panel and let users scan it
