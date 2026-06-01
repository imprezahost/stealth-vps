# Roadmap interno — v0.12.0 Subscription bridge web UI

Design doc for the onboarding bridge: a static web page that turns a subscription token into a one-tap client import. Internal doc; the public sees the `README.md` roadmap row + the `CHANGELOG.md` entry at release. ADRs locked with the operator are marked **[ADR]**; unresolved items in [Open questions](#open-questions).

## Context

Today, getting a user connected is a multi-step manual dance:

1. Operator runs `s-vps user add alice`, copies the subscription URL.
2. Operator sends the URL to alice (Telegram, email, paper).
3. Alice has to *already know* which client app to install (Hiddify Next? V2Box? NekoBox?) for her platform.
4. Alice installs the app, finds "add subscription / import from URL", pastes the URL.

Steps 3-4 are where non-technical users get stuck. The competition solved this: Hiddify-Manager's "user portal" + Marzban's subscription page both render a per-user HTML page that detects the platform and shows a one-tap "Import to Hiddify" button. stealth-vps hands out a raw `.txt` base64 blob — correct, auditable, but hostile to a non-technical end user.

v0.12 closes that gap with a **subscription bridge**: a friendly web page at `/.well-known/stealth-vps-onboard/<token>` that:

- detects the visitor's OS (Android / iOS / macOS / Windows / Linux),
- shows the right client recommendations for that platform,
- renders one-tap **deep-link buttons** (`hiddify://`, `v2box://`, `sn://`, `sing-box://`) that import the subscription directly into the app,
- shows a **QR code** of the subscription URL for camera-scan import,
- falls back to a "copy subscription URL" button for any client.

The operator hands the user ONE link; the page does the rest.

## Strategy at a glance

**[ADR] Static page, no backend.** The bridge is a single static HTML/CSS/JS bundle served by Caddy — same "Caddy serves files, no daemon" principle as the subscription endpoint itself (v0.6). No server-side rendering, no per-user HTML file, no new long-running process. The page reads the token from its own URL and does everything client-side.

**[ADR] Derive, don't fetch (by default).** The page can build the subscription URL + all deep-links + the QR purely from the token in the URL + the known sub base — it does NOT need to fetch the sub bundle to function. An optional "show details" fetch lists the per-node/per-protocol entries, but the core one-tap flow works with zero network calls beyond loading the page. This dodges CORS entirely and keeps the page working even if the sub endpoint is loopback-only behind a tunnel.

**[ADR] Opt-in, off by default.** `stealth_vps_onboard_enabled`. Like every feature since v0.9, adding the bridge is explicit. It rides on the existing Caddy install (requires `stealth_vps_subscription_enabled` + `expose` + a domain — the page links are only useful when the sub URL is publicly reachable).

**[ADR] Vendor the QR lib, no CDN.** QR generation is client-side via a vendored, MIT-licensed, dependency-free JS QR encoder (no `<script src="cdn…">`). A censored-network user loading the page must not trigger a third-party request that leaks "this person is setting up a circumvention tool" or that a CDN-block would break. The whole bundle is self-contained + servable offline.

Consequences:

- **No new per-user state.** The onboard page is the same static asset for every user; the token is purely in the URL. `s-vps user add` doesn't write an HTML file.
- **The onboard token IS the sub token.** No new secret — the onboard URL just wraps the existing `sub_token` in a friendlier path. Exposure is identical to handing out the sub URL (see [Security model](#security-model)).
- **Caddy gets one more route.** `handle_path /.well-known/stealth-vps-onboard/* { … }` serving the static bundle, with the token captured client-side from `location.pathname`.
- **Bot + CLI surface a new URL.** `s-vps user show` + the bot's `/sub` print the onboard URL alongside the raw sub URL. New `s-vps user onboard-url LABEL` convenience verb.

---

## UX flow

```text
Operator                          End user (alice)
────────                          ────────────────
s-vps user add alice
  → onboard URL:
    https://vpn.example.com/.well-known/stealth-vps-onboard/<token>
  ── sends link via Telegram ──▶  taps link on her phone
                                  ┌─────────────────────────────────┐
                                  │  stealth-vps — Connect           │
                                  │                                  │
                                  │  Detected: Android               │
                                  │                                  │
                                  │  ┌────────────────────────────┐  │
                                  │  │   [ Import to Hiddify ]     │  │ ← hiddify://install-sub?url=…
                                  │  │   [ Import to V2Box ]       │  │ ← v2box://install-sub?url=…
                                  │  │   [ Import to NekoBox ]     │  │ ← sn://subscription?url=…
                                  │  └────────────────────────────┘  │
                                  │                                  │
                                  │      ▛▀▙ QR (scan to import)     │
                                  │      ▙▄▟                          │
                                  │                                  │
                                  │  [ Copy subscription URL ]       │
                                  │  ▸ Show details (4 servers)      │
                                  └─────────────────────────────────┘
                                  taps "Import to Hiddify"
                                  → Hiddify opens, subscription added,
                                    connects. Done.
```

Platform → recommended clients (the page shows the top 2-3 per platform, all clients behind a "more options" expander):

| Platform | Primary | Secondary |
|----------|---------|-----------|
| Android | Hiddify Next, NekoBox | V2rayNG, sing-box |
| iOS | Hiddify, Streisand | V2Box, sing-box |
| macOS | Hiddify, V2Box | sing-box |
| Windows | Hiddify, NekoRay | v2rayN |
| Linux | Hiddify, sing-box | NekoRay |

---

## Architecture

```text
/var/lib/stealth-vps/onboard/           ← static bundle (role-installed)
├── index.html                          # the page (one file)
├── onboard.js                          # UA detect + deep-link + QR render
├── onboard.css                         # styling (system fonts, no web fonts)
└── vendor/qrcode.min.js                # vendored MIT QR encoder, no CDN

Caddy (public TLS site):
  handle_path /.well-known/stealth-vps-onboard/* {
      root * /var/lib/stealth-vps/onboard
      try_files /index.html              # SPA-style: any token path → index.html
      file_server
  }
```

The page is served for ANY path under `/onboard/` — Caddy's `try_files /index.html` makes it an SPA. `onboard.js` reads the token from `location.pathname` (the segment after `/onboard/`), constructs:

```js
const subUrl = `${location.origin}/.well-known/stealth-vps-sub/${token}`;
```

then renders the deep-links + QR from `subUrl`. Deep-link templates:

```js
const DEEPLINKS = {
  hiddify:  u => `hiddify://install-sub?url=${encodeURIComponent(u)}`,
  v2box:    u => `v2box://install-sub?url=${encodeURIComponent(u)}`,
  nekobox:  u => `sn://subscription?url=${encodeURIComponent(u)}`,
  singbox:  u => `sing-box://import-remote-profile?url=${encodeURIComponent(u)}`,
  streisand:u => `streisand://import/${u}`,
};
```

(Exact scheme strings pinned in [Open question O2](#open-questions) — they drift between client releases and need a verification pass against current app versions.)

QR: `vendor/qrcode.min.js` renders the `subUrl` into a `<canvas>` — pure client-side, no network.

Optional "Show details" expander: fetches `subUrl`, base64-decodes the body, parses the URIs, lists them (one row per node × protocol with a per-row copy button). This is the ONLY network call and it's lazy (only on expander tap), so the core flow never needs it. Same-origin fetch (sub + onboard are both on the domain) → no CORS.

---

## Scope (in)

- **Static onboard bundle** under `files/onboard/` (index.html + onboard.js + onboard.css + vendor/qrcode.min.js).
- **`tasks/onboard.yml`** — installs the bundle to `/var/lib/stealth-vps/onboard/`, gated on `stealth_vps_onboard_enabled`.
- **Caddyfile route** — `handle_path /.well-known/stealth-vps-onboard/*` in the public-TLS site (rendered when onboard enabled).
- **UA detection + per-platform client recommendations** in `onboard.js`.
- **Deep-link buttons** for Hiddify Next / V2Box / NekoBox / sing-box / Streisand.
- **Client-side QR** of the subscription URL (vendored lib).
- **"Copy subscription URL"** fallback + **"Show details"** lazy bundle fetch.
- **`s-vps user onboard-url LABEL`** — print the onboard URL for a user.
- **Bot + `s-vps user show`** surface the onboard URL alongside the raw sub URL.
- **A pure-Python URL builder** `stealth_vps.onboard.onboard_url_for(token, base)` (the testable core — the CLI/bot reuse it; the HTML stays untested beyond a smoke that asserts the token-extraction JS regex matches the role's path).
- **defaults** — `stealth_vps_onboard_enabled` + `stealth_vps_onboard_path` (default `/.well-known/stealth-vps-onboard`).
- **docs/onboarding.md** — operator guide + the end-user flow.

## Scope (out — deferred)

| Want | Why deferred |
|------|--------------|
| Per-user HTML page (server-rendered) | Static-page + client-side token read is simpler + stateless. No per-user file to write/clean. |
| Account self-service (user changes own creds) | That's a portal, not a bridge. Big surface (auth, sessions, CSRF). Out of the project's "operator-curated" model. |
| Traffic/quota display on the page | Needs a stats API the page can hit per-token — server-side surface we don't have. v0.13+ if asked. |
| Web fonts / external CSS | Self-contained bundle only (censorship-resilience + no third-party leak). System font stack. |
| Analytics / telemetry on the page | Never. A circumvention onboarding page must phone home to nobody. |
| i18n beyond English + zh-CN | English + a zh-CN string table at most for v0.12. More locales = community PRs. |
| Multi-node "pick your region" UI | The subscription bundle already carries all nodes; Hiddify auto-selects. A manual region picker is a v0.13+ nicety. |

---

## Security model

| Concern | Analysis |
|---------|----------|
| Onboard URL leaks the sub token | The onboard URL embeds `sub_token`. Anyone with the URL can fetch the subscription — **identical exposure to handing out the sub URL directly**. The bridge is a friendlier wrapper, not a new trust boundary. Operators treat the onboard URL with the same care as the sub URL. |
| Token rotation | `s-vps sub revoke LABEL` (v0.6) rotates the sub token → the old onboard URL 404s on the sub fetch + the deep-links point at a dead sub. Same rotation story as today. |
| Third-party requests | **Zero.** No CDN, no web fonts, no analytics. The page + QR lib are vendored + same-origin. A network observer sees one TLS request to the operator's domain — same as fetching the sub. |
| Page enumeration | Caddy serves `index.html` for ANY `/onboard/*` path (SPA fallback), so a scanner can't distinguish a valid token from an invalid one by the onboard page alone — the page always renders. The validity check only happens when the client app fetches the sub URL (which 404s for a bad token). No token oracle. |
| Clickjacking / embedding | The page sets `X-Frame-Options: DENY` (via a Caddy header directive) so it can't be iframed into a phishing wrapper. |
| Content-Security-Policy | Strict CSP header (`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:`) — `data:` for the QR canvas, everything else same-origin. No inline scripts (onboard.js is external). Blocks any injected third-party load. |

The bridge adds **no new secret + no new network exposure** beyond what the subscription endpoint already has. It's a presentation layer over the existing sub token.

---

## Phasing

**Phase 1 — static bundle + Caddy route + Python URL builder (~2 dev-days)**
- `stealth_vps/onboard.py`: `onboard_url_for(token, base)` + token-validation helper. Pure stdlib. Tests.
- `files/onboard/` bundle: index.html + onboard.js (UA detect, deep-links, QR) + onboard.css + vendored qrcode.min.js.
- `tasks/onboard.yml` + defaults + Caddyfile route + CSP/X-Frame headers.
- A node-free JS sanity check in CI (the existing `go-test`-style lane, or a tiny `python -c` that lints the deep-link template table against the Python builder's expectations).

**Phase 2 — CLI + bot surface (~1 dev-day)**
- `s-vps user onboard-url LABEL`.
- `s-vps user show` + bot `/sub` print the onboard URL.
- Bot `/onboard LABEL` → DM the onboard link (+ optionally a QR image generated server-side via `qrencode` if installed, else just the link).

**Phase 3 — docs + cut (~1 dev-day)**
- `docs/onboarding.md` (operator + end-user flow, screenshots-as-ASCII).
- CHANGELOG + READMEs + release.sh bump + Tokyo smoke (onboard page returns 200 + the JS extracts the token) + GitHub mirror.

**Total: ~4 dev-days. Target cut: 2026-07-10.**

---

## Decisions (locked 2026-06-01)

All 7 questions resolved with the operator; defaults accepted. Recorded for the implementing dev.

| # | Decision |
|---|----------|
| O1 | **`nayuki/QR-Code-generator`** (MIT, dependency-free, actively maintained) — vendored minified into `files/onboard/vendor/qrcode.min.js`. |
| O2 | Deep-link scheme strings VERIFIED against current client releases during Phase 1; each recorded with a `// verified <app> <version> <date>` comment in `onboard.js`. No guessed schemes ship. |
| O3 | "Show details" fetches the bundle **lazily** (expander tap only). Core flow is fetch-free. |
| O4 | Onboard path: **`/.well-known/stealth-vps-onboard`**. |
| O5 | Bot `/onboard`: **link + QR image** when `qrencode` present; link-only fallback. |
| O6 | **English-only** for v0.12; `STRINGS` object structured for a later zh-CN table. |
| O7 | **Require `subscription_expose=true`** — converge-time assert when `onboard_enabled` but not exposed. |

---

## What signals success

- **Operator runs `s-vps user add alice`**, copies the onboard URL, sends it. Alice taps it on her phone, taps "Import to Hiddify", and is connected — no manual app-store hunt, no URL paste.
- **The page loads with zero third-party requests** (verify in browser devtools: only same-origin requests).
- **A scanner hitting random `/onboard/<garbage>` paths** always gets the same 200 page (no token oracle); only the in-app sub fetch distinguishes valid from invalid.
- **Single-node v0.11 → v0.12 is a no-op** — onboard off by default, no new listener, no behaviour change.
- **The page works offline** once loaded (QR + deep-links are derive-only; "show details" is the only thing that needs the network).
- **~545 automated tests** (536 from v0.11 + ~10 for the onboard URL builder + UA/deep-link table). HTML/JS is smoke-tested, not unit-tested — the testable logic lives in `onboard.py`.
- **Two beta users** (one Android, one iOS) onboard via the bridge before announcing.
