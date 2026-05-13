# macOS client setup

Four good options, picked by free-vs-paid and how you prefer to manage configs:

| Client | Reality | Hysteria2 | Cost | Notes |
|---|---|---|---|---|
| **Hiddify Desktop** | ✅ | ✅ | Free | sing-box based, open-source, cross-platform |
| **V2Box** | ✅ | ✅ | Paid (Mac App Store) | Polished native macOS UI |
| **NekoBox (nekoray)** | ✅ | ✅ | Free | Same code as Windows/Linux build; Linux-y UI |
| **Shadowrocket** | ✅ | ✅ | Paid | iOS app — runs on Apple Silicon Macs natively |

All four accept the URI from `/root/stealth-vps-credentials.txt` (`vless://…` for Reality, `hysteria2://…` for Hysteria2) without manual field entry. Port hopping (`,min-max`) and the `&insecure=1` flag are honoured per-profile.

---

## Hiddify Desktop (recommended — free, open-source)

### Install
Download `Hiddify*.dmg` from [github.com/hiddify/hiddify-app/releases](https://github.com/hiddify/hiddify-app/releases). Drag into Applications. First launch: macOS will warn about an unidentified developer — right-click → **Open** → confirm.

### Add a profile
- Copy the URI to the clipboard.
- Hiddify → top-right **+** → **Add Profile from Clipboard**. New profile appears with the remark from the URI fragment.

### Connect
- Toggle the big switch on the main screen.
- macOS shows the **"… would like to add VPN configurations"** prompt — Allow once. (`System Settings → VPN` then shows a stealth-vps profile created by Hiddify.)
- Status flips to **Connected**.

### Mode selector
- **TUN mode** (default): everything goes through. Recommended.
- **System Proxy**: macOS-wide HTTP/SOCKS only; only browsers/CLI tools that respect macOS proxy settings flow through.
- **Manual / Off**: client is idle.

### Verify
- `curl https://ifconfig.me` from the macOS Terminal returns the VPS IP.
- `https://dnsleaktest.com` shows only the proxy's DNS, not your ISP's.
- Hiddify's status bar shows ping latency.

---

## V2Box (paid, App Store — native macOS)

### Install
Mac App Store → search **V2Box** → ~US$10 one-off (varies by region).

### Add a profile
File → **Import from Clipboard** (after copying the URI), or drag-drop a `.json` config file. V2Box parses `vless` + `hysteria2` URIs natively.

### Connect
Click the on/off switch in the main window header → VPN permission once.

### Settings worth a glance
- **Routing mode**: Global / Bypass-LAN / Bypass-China. Bypass-LAN is sensible for most desktop setups; the others depend on your traffic shape.
- **DNS**: Settings → DNS → set Local DNS to `1.1.1.1` to avoid your ISP DNS leaking outside the tunnel.

---

## NekoBox / nekoray (free, cross-platform — power-user)

### Install
Download the macOS `.dmg` from [github.com/MatsuriDayo/nekoray/releases](https://github.com/MatsuriDayo/nekoray/releases). Same right-click → Open dance on first launch (unsigned).

### Add a profile
- Menu **Program → Add profile from Clipboard** (`⌘V`).
- Or **Program → Import** and pick a `.json` file.

### Connect
- Right-click the profile in the list → **Set as Selected**.
- Top menu **Start** (`⌘,`). Status bar shows **Started**.
- By default this opens an HTTP+SOCKS proxy on `127.0.0.1:2080`. Either:
  - Point your apps at the proxy (System Settings → Network → proxies), or
  - **Mode → TUN mode → Enabled** for system-wide routing (admin rights prompt on first enable).

### TUN mode caveats
- nekoray requires `wintun.dll` equivalent (uses macOS native `utun*` interfaces — no extra driver, but needs admin auth at first enable).
- Survives sleep/wake well; reconnects automatically.

---

## Shadowrocket on Apple Silicon (paid)

If you're on Apple Silicon (M1/M2/M3 etc.) and already paid for Shadowrocket on iOS, the same app runs on macOS through "Designed for iPad" mode.

### Install
Mac App Store → your purchase history → Shadowrocket → install (no second payment).

### Add a profile / Connect / Verify
Same flow as on iPad/iPhone — see `ios.md`. macOS gives the VPN permission prompt once.

### Limitations vs native macOS apps
- iOS app on macOS uses the iOS VPN extension model. Per-app rules behave like iOS, not like a desktop firewall — fewer power-user knobs than NekoBox or V2Box.

---

## Common macOS troubleshooting

**"Permission denied" creating the VPN.** System Settings → Privacy & Security → scroll for the app → click **Allow**. Re-launch the client.

**Connects but `curl ifconfig.me` shows local IP.** You're in proxy mode without macOS-wide proxy set. Either:
- Enable TUN in the client, or
- System Settings → Network → Wi-Fi → Details → Proxies → fill HTTP / SOCKS proxy with `127.0.0.1:2080` (or whatever your client uses).

**DNS leaks despite TUN.** Some apps (Slack, Zoom, certain Electron-based things) use Apple's "private DNS" API which bypasses TUN. In the client, set a "**block local DNS**" / "**force DNS through proxy**" option (Hiddify: Settings → DNS → strict; NekoBox: Settings → DNS → Local DNS off).

**TLS error on Hysteria2 after server switched to Let's Encrypt.** Re-import the URI from `/root/stealth-vps-credentials.txt` — the new version drops `&insecure=1` and has the right SNI.

**Battery drain on laptop.** Reality (TCP) is usually lighter than Hysteria2 (QUIC + active keepalive). For battery-priority sessions, switch to the Reality profile.

**"Operation not permitted" from `wintun`/`utun` setup.** Usually means you launched without admin rights or System Integrity Protection is blocking. Try Hiddify or V2Box, which use the well-trodden macOS VPN extension instead of TUN drivers.

---

## Sharing access with multiple users

Same flow as iOS — see [ios.md § Sharing access with multiple users](ios.md#sharing-access-with-multiple-users). 3X-UI panel manages additional clients and per-user subscription URLs.

---

## Validation status

Walkthroughs are written from the apps' published behaviour and our own server-side validation; **per-screen pen-tested validation lands in v0.4.0** when macOS hardware enters the QA rotation. Issues + app version + macOS version please.
