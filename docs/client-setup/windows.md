# Windows client setup

Two recommended clients, both open-source. **NekoBox for Windows** covers Reality + Hysteria2 in one place and is the easier pick if you want both protocols. **v2rayN** is the most popular Windows client overall, mature Reality support, but its Hysteria2 path is younger.

| Client | Reality (VLESS) | Hysteria2 | System-wide TUN | Notes |
|---|---|---|---|---|
| **NekoBox for Windows** (nekoray) | ✅ | ✅ | ✅ | sing-box backend |
| **v2rayN** | ✅ | ⚠️ via sing-box bundle | ✅ | Largest community |

> All snippets below assume you already have the connection URIs from
> `/root/stealth-vps-credentials.txt` on the server. `vless://…` is
> Reality; `hysteria2://…` is Hysteria2.

---

## Install

- **NekoBox**: download the latest `nekoray-*-windows64.zip` from [GitHub releases](https://github.com/MatsuriDayo/nekoray/releases). Extract anywhere (Documents, Desktop) — no installer.
- **v2rayN**: [GitHub releases](https://github.com/2dust/v2rayN/releases). Pick the `-WithSelfContained-net*.zip` build for the most independent install.

Both ship as portable apps. Windows SmartScreen will warn on first run (unsigned). Right-click → Properties → Unblock if needed.

---

## NekoBox — Reality and Hysteria2

### Import

1. Launch `nekoray.exe`.
2. Copy the URI (vless or hysteria2) to the clipboard.
3. In NekoBox: **`Program → Add profile from clipboard`** (or `Ctrl+V`).
4. The new profile appears in the list with the remark from the URI fragment.

You can import both URIs and switch between them at runtime to compare which protocol performs better on your link.

### Connect

1. Right-click the profile → **`Set as Selected`**.
2. From the top menu: **`Start`** (or `Ctrl+,`).
3. Status bar shows **Started** with the selected profile name.

By default NekoBox runs as an HTTP/SOCKS proxy on `127.0.0.1:2080`. Set your browser to use that proxy, **or** enable TUN mode for system-wide routing (see below).

### TUN mode (system-wide)

1. From the menu: **`TUN Mode`** → **`Enabled`**.
2. First time only: NekoBox prompts to install **wintun.dll** alongside the executable. Confirm; this is the Linux community's standard kernel-bypass network driver.
3. NekoBox needs admin rights to register the TUN interface. Run `nekoray.exe` as administrator (or right-click → Run as administrator).
4. Once TUN mode is on, all traffic (DNS included) goes through the tunnel — no browser proxy config needed.

### Hysteria2 + port hopping

If the server-side has port hopping enabled, the URI fragment carries the range (e.g. `host:49440,20000-50000`). NekoBox handles this automatically — Settings → Profile detail will show `Listen Range`.

If the server is using **self-signed TLS** (test setup before Let's Encrypt is provisioned), the URI carries `&insecure=1` and NekoBox skips cert validation for that profile only.

---

## v2rayN — Reality (and Hysteria2 via sing-box)

v2rayN historically targeted Xray (Reality is first-class). Hysteria2 support landed by bundling sing-box; this works but lags upstream a bit.

### Import Reality

1. Copy the `vless://…` URI.
2. v2rayN: top menu → **`Servers → Import bulk URL from clipboard`**.
3. New entry appears at the bottom of the list. Double-click to set active.

### Import Hysteria2

The sing-box backend is bundled but you may need to enable it manually:

1. Top menu → **`Settings → Core: Core Type`** → make sure **`sing-box`** is in the list.
2. Top menu → **`Servers → Import bulk URL from clipboard`** with the `hysteria2://…` URI.
3. The new entry shows `hysteria2` as its protocol; double-click to activate.

### Connect

- **System proxy mode** (default): tray icon → right-click → **`System proxy → Set system proxy`**. v2rayN listens on `127.0.0.1:10809` (HTTP) and `:10808` (SOCKS) by default.
- **TUN mode** (system-wide): top menu → **`Settings → Use TUN mode`**. Same wintun driver as NekoBox; same admin-rights requirement.

### Verify

Open `https://ifconfig.me` in a browser → must show the VPS IP.

---

## Troubleshooting

**`Program failed to start` / `Could not find wintun.dll`.**
TUN mode requires `wintun.dll` alongside the executable. NekoBox downloads it on demand on first enable; v2rayN ships it with the `-WithSelfContained` build. If you grabbed a smaller build, fetch wintun manually from [wintun.net](https://www.wintun.net/) and drop the DLL next to the `.exe`.

**`Failed to register TUN interface, access denied`.**
You're not running as administrator. Right-click the executable → Run as administrator. (Or use the proxy mode instead — no admin needed.)

**Connects, but `ifconfig.me` still shows your local IP.**
You're in proxy mode and the browser isn't using the proxy. Either enable system proxy (Settings → Use system proxy) or enable TUN mode for everything to flow through.

**Reality connects but websites time out.**
Some Windows enterprise machines have DNS-over-HTTPS pinned to a local resolver that bypasses the tunnel. Check Edge/Chrome settings → Privacy → DNS, and either disable it or set the resolver to one reachable through the tunnel (e.g. `1.1.1.1`).

**`TLS handshake failed: x509: certificate signed by unknown authority`** on Hysteria2.
The server is still using a self-signed cert and the URI in `credentials.txt` should have `&insecure=1`. Re-import the URI (the previous version may have been generated before TLS was set up).

---

## What to share with end-users

For multi-user deployments, the operator's credentials file lists the URI directly. Forward it via a private channel, or use the 3X-UI panel's subscription link feature (Inbounds → client → 3-dot menu → Subscribe → copy URL). End-users can refresh subs on their own and you can revoke per-client by disabling them in the panel.
