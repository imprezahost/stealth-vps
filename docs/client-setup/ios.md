# iOS client setup

iOS doesn't allow open-source proxy clients on the public App Store unless they go through the official review process, so practical options are paid apps (Shadowrocket, Streisand) or the open-source **Hiddify** build (free; available on the App Store in most regions).

| Client | Reality | Hysteria2 | Cost | App Store regions |
|---|---|---|---|---|
| **Shadowrocket** | ✅ | ✅ | Paid (~US$3, one-off) | most regions; pulled from cn-China store |
| **Hiddify** | ✅ | ✅ | Free | most regions; pulled from cn-China store |
| **Streisand** | ✅ | ✅ | Paid (~US$3, one-off) | most regions |

> In mainland China, Russia, and some others, App Store hides these apps. Switch your Apple ID to a different region (the app stays installed if you bought it elsewhere) — it's an App Store policy issue, not something we can work around server-side.

All three accept the URI from `/root/stealth-vps-credentials.txt` as-is. `vless://…` for Reality, `hysteria2://…` for Hysteria2. Port hopping (`,min-max` suffix) and `&insecure=1` are honoured per-profile.

---

## Hiddify (recommended — free, open-source)

### Install
App Store → search **Hiddify** → install (free). Confirm publisher is "Hiddify Inc".

### Add a profile

**From the share URL:** copy the `vless://…` or `hysteria2://…` URI from the credentials file → in Hiddify, tap the **+** icon (top-right) → **Add from clipboard**. The profile appears with the remark from the URI fragment.

**From a QR code:** the 3X-UI panel can render a QR for any inbound/client (Inbounds → row → 3-dot menu → QR code). In Hiddify, tap **+** → **Scan QR**.

### Connect
1. Tap the new profile → **Set as active**.
2. Tap the big toggle (or the Quick Connect button) → grant the **"Add VPN Configurations"** permission iOS asks for once.
3. Status flips to **Connected** within a couple seconds. Latency shows in the row.

### Verify
- Safari → `https://ifconfig.me` should show the VPS IP, not your carrier IP.
- `https://dnsleaktest.com` → only the proxy's resolver (Cloudflare 1.1.1.1 or what Hiddify is set to), not your ISP DNS.

### Reality-specific checks (Hiddify → profile detail)
- Type: `vless`
- Network: `tcp`
- Security: `reality`
- Flow: `xtls-rprx-vision`
- SNI: the server-side `dest` host (`www.microsoft.com` by default)
- Fingerprint: `chrome`
- Public Key (pbk) + ShortID (sid) populated

If any are missing, the URI was truncated during copy. Re-paste / re-scan.

### Hysteria2 specifics
- With Let's Encrypt on the server (default when `stealth_vps_domain` is set), the URI does **not** carry `insecure=1`. Hiddify validates against the public chain.
- Self-signed (test setups): the URI ends with `&insecure=1`. Hiddify honours the flag per-profile — does **not** weaken TLS for unrelated profiles.
- Port hopping URIs (`host:49440,20000-50000/…`) work transparently.

---

## Shadowrocket (paid — the de-facto iOS choice)

### Install
App Store → search **Shadowrocket** → ~US$3, one-off purchase. (Publisher: Shadow Launch Technology Limited.)

### Add a profile
- Open Shadowrocket → top right **+**.
- **Paste from clipboard** if you copied the URI, or **Scan QR** with the camera.
- Both URI schemes (`vless`, `hysteria2`) are parsed natively — Shadowrocket fills every field, no manual entry.

### Connect
- Toggle the master switch on the **Home** tab → grant VPN permission once.
- Mode selector at the top: **Proxy** (system VPN — recommended for stealth-vps) vs **Off / Configuration**. Pick Proxy.
- Rule-set: leave on **Default** until you have specific bypass needs.

### Verify
Same checks as Hiddify (`ifconfig.me`, DNS leak test).

### Shadowrocket-specific knobs worth knowing
- **Connection check**: Settings → Connectivity Test → set the URL to something fast-loading + your destination, otherwise the "Last latency" reading is meaningless on a fresh profile.
- **TUN mode**: on by default on iOS (no choice anyway — iOS only supports the per-app-aware system VPN, which Shadowrocket exposes as TUN).

### Hysteria2 specifics
Same as Hiddify (port hopping + insecure handling).

---

## Streisand (paid — sing-box backend)

### Install
App Store → search **Streisand** → ~US$3 one-off.

### Add a profile
Tap **+** → **Add from clipboard** (URI must already be in the clipboard). Streisand uses sing-box internally, so `vless` + `hysteria2` URIs both parse.

### Connect
Master toggle on the main screen → VPN permission once → Connected.

### Notes
- Streisand defaults to TUN mode for system-wide routing.
- Routing rules are richer than Shadowrocket but more opaque; if you want a "tunnel everything" setup, no extra config needed.

---

## Common iOS troubleshooting

**"VPN configuration is not installed."** iOS forgot the grant. Settings → General → VPN & Device Management → ensure the profile exists and is enabled.

**Connects but websites won't load.** The OS-level DNS is bypassing the tunnel. In the client, set DNS to a public resolver (`1.1.1.1`, `8.8.8.8`) or, for Reality, enable "Use server DNS" in the client's advanced settings.

**Hysteria2 disconnects when leaving Wi-Fi for cellular.** Some Chinese carriers throttle sustained UDP. Switch the active profile to your Reality (TCP) URI for cellular; Hysteria2 stays parked for Wi-Fi.

**"TLS handshake failed: certificate not trusted"** on Hysteria2 after switching the server to Let's Encrypt. The URI in `credentials.txt` is regenerated with the right SNI and no longer carries `insecure=1`. Re-import the URI.

**Battery drain.** Reality (TCP) is typically lighter on battery than Hysteria2 (QUIC), even when idle. If battery is the priority, default to Reality.

---

## Sharing access with multiple users

For multi-user deployments, the operator (you) creates additional clients in the panel:

1. 3X-UI panel → **Inbounds** → row for `stealth-vps-reality` (or whichever) → **+** under Clients.
2. Fill: email (just a label, e.g. `alice@team`), traffic cap, expiry, etc.
3. Save → row's **3-dot menu** → **QR code** or **Subscription URL** → share that with the end user.

Subscription URLs let clients refresh on their own and you can revoke per-user by disabling the client in the panel. Shadowrocket, Hiddify, and Streisand all support subscription import.

---

## Validation status

These walkthroughs are written from the apps' published behaviour and our own server-side validation; **per-screen pen-tested validation lands in v0.4.0** once an iOS device is in the QA rotation. If you hit something that doesn't match what's here, please open an issue with the app + iOS version + exact step.
