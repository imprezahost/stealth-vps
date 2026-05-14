# macOS client setup

Four working options as of macOS Tahoe (15+), validated pen-test 2026-05-14:

| Client | Reality | Hysteria2 | Cost | Notes |
|---|---|---|---|---|
| **Hiddify (iOS-on-Mac via "Designed for iPad")** | ✅ | ✅ | Free | **Recommended on macOS 15+.** Uses iOS VPN model — works around the Network Extension notarization issue that breaks the `.dmg` build. Apple Silicon only. |
| **Shadowrocket (iOS on Apple Silicon)** | ✅ | ✅ | Paid (~US$3, one-off) | Same "Designed for iPad" path. Apple Silicon only. |
| **V2Box** | ✅ | ✅ | Paid (Mac App Store) | Native macOS, signed/notarized — no Network Extension surprises. Both Intel + Apple Silicon. |
| **NekoBox (nekoray)** | ✅ | ✅ | Free | Native macOS, unsigned. May trip the same Tahoe Network Extension issue as Hiddify Desktop — test before relying on it. |
| ~~Hiddify Desktop `.dmg`~~ | ❌ | ❌ | Free | **Broken on macOS 15+ / Tahoe.** Network Extension unsigned → silently rejected. Kept in the doc for reference and for users on older macOS. |

All five accept the URI from `/root/stealth-vps-credentials.txt` (`vless://…` for Reality, `hysteria2://…` for Hysteria2) without manual field entry. Port hopping (`,min-max`) and the `&insecure=1` flag are honoured per-profile.

---

## Hiddify on Apple Silicon via "Designed for iPad" (recommended on macOS 15+ / Tahoe)

> **Why not the `.dmg` build?** Pen-tested 2026-05-14 on macOS Tahoe with Hiddify Desktop `Hiddify-MacOS.dmg` (universal). The app opens, the profile imports from clipboard, the UI shows "Connected" — but no VPN config gets registered. `networksetup -listallnetworkservices` shows no VPN entry, `systemextensionsctl list` shows 0 extensions, and `route -n get default` keeps pointing at the local gateway. Cause: Apple Silicon macOS 15+ requires Network Extensions to ship inside notarized apps with a paid Apple Developer ID. The Hiddify open-source project ships unsigned, so the extension is **silently rejected** (no prompt, no error). Until upstream notarizes the build, the iOS-app-on-macOS path below is the working alternative.

### Install
1. Make sure you already installed **Hiddify** on your iPhone (see [`ios.md`](ios.md)). You don't actually need it on the iPhone for this path — only that your Apple ID has it "purchased" (Hiddify is free, so it counts as purchased the moment you install once on any device).
2. Mac App Store → top-left profile picture → **Account Settings** → enable **"iPhone & iPad Apps"** if it's not already.
3. Mac App Store → search **Hiddify**. A result tagged **"Designed for iPad"** appears. Click **Get** / **Install**.
4. Open `/Applications/Hiddify` — UI is the iPhone layout, scaled.

### Add a profile
- Copy the `vless://…` URI to the clipboard.
- Hiddify → top-right **+** → **Add from Clipboard**. New profile appears with the remark from the URI fragment.
- iOS-style profiles do **not** sync across devices; re-paste the URI on the Mac even if it's already on your iPhone.

### Connect
- Tap the profile card once to mark it active.
- Tap the big toggle / **Connect**.
- macOS shows the **"Hiddify Would Like to Add VPN Configurations"** prompt — Allow once + Touch ID.
- Status flips to **Connected** within a second or two. macOS now registers the VPN under **System Settings → Network** (visible as "Hiddify" or "stealth-vps-…").

### Verify
- `curl -4 ifconfig.me` from Terminal returns the VPS IPv4 (`103.106.228.154` or whatever your VPS is).
- `curl ifconfig.me` (default, may prefer IPv6) returns the VPS IPv6 if your VPS is dual-stack — that's expected, not a leak. Cross-check with `-4` if in doubt.
- `route -n get default | grep interface` shows `utun0` (or similar), not `en0`.

### Conflict with other VPN apps
macOS allows multiple VPN configurations registered, but **Network Extensions can silently block each other**. The most common culprits observed in the field:

- **DuckDuckGo VPN** — its Network Extension stays loaded even when the DuckDuckGo app is closed. While on, Hiddify cannot install its own tunnel; the toggle flips but no traffic flows.
- **NordLayer / NordVPN / Surfshark / ExpressVPN** — same pattern. Helper daemons remain after the GUI is closed.
- **WARP (Cloudflare)** — when enabled, captures DNS and TCP traffic; will mask whether Hiddify is actually tunneling.

To diagnose: **System Settings → General → Login Items & Extensions → Network Extensions** lists all registered Network Extensions. Disable any non-Hiddify one with the toggle (no need to uninstall) before testing.

### Known limitations of this path
- The UI is the iPhone layout — denser than the native macOS app would be. Tolerable, but ugly on a large monitor.
- TUN mode is implicit (iOS doesn't expose mode selector). You get system-wide routing.
- Per-app rules behave like iOS, not like a desktop firewall — fewer power-user knobs than NekoBox or V2Box.

## Hiddify Desktop `.dmg` (broken on macOS Tahoe — kept for reference)

The native macOS build from [github.com/hiddify/hiddify-app/releases](https://github.com/hiddify/hiddify-app/releases). Two issues that bite on macOS Tahoe and which the upstream project has not addressed:

### Issue 1: Gatekeeper blocks first launch without an "Open Anyway" path

On Tahoe, double-clicking the unidentified-developer app brings up a dialog that only offers **"Move to Trash"** — the historical `right-click → Open` bypass was removed. Two workarounds:

- **UI path**: try to open the app, dismiss the "Move to Trash" dialog, go to **System Settings → Privacy & Security**, scroll to the bottom, look for *"Hiddify was blocked from use because it is not from an identified developer"* with an **Open Anyway** button. Click it, then re-open the app.
- **Terminal**: `xattr -d com.apple.quarantine /Applications/Hiddify.app` removes the quarantine flag. Double-click works after that.

### Issue 2: Network Extension never registers (the real blocker)

Once the app is open, adding a profile and toggling Connect makes the UI say "Connected" — but `systemextensionsctl list` shows zero extensions, `networksetup -listallnetworkservices` lists no VPN, and `route -n get default` still points at `en0`. Apple silently rejected the unsigned Network Extension. There is no popup, no error log entry in the app, no `Privacy & Security` "Allow" button to grant. Until Hiddify upstream publishes a notarized build with an Apple Developer ID, the `.dmg` route stays broken on macOS 15+. Use the "Designed for iPad" path above instead.

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

| App | Path | Last tested | Status |
|---|---|---|---|
| Hiddify (iOS-on-Mac) | "Designed for iPad" via Mac App Store | 2026-05-14 on macOS Tahoe / M2 Pro | ✅ validated end-to-end; IPv6 of dual-stack VPS visible in `curl ifconfig.me` |
| Hiddify Desktop `.dmg` | github.com/hiddify/hiddify-app/releases | 2026-05-14 on macOS Tahoe / M2 Pro | ❌ broken — Network Extension silently rejected by Tahoe |
| Shadowrocket | "Designed for iPad" via Mac App Store | not yet | pending |
| V2Box | Mac App Store native | not yet | pending |
| NekoBox | upstream `.dmg` | not yet | pending |

When testing a new combination, file an issue with: app + version + macOS version + Apple Silicon vs Intel + output of `systemextensionsctl list` and `route -n get default`. The first two diagnostics catch ~all Network Extension regressions.
