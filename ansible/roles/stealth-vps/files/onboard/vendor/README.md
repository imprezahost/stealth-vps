# Vendored third-party assets

## qrcode.min.js — QR encoder

**Provenance (ADR O1):** [`nayuki/QR-Code-generator`](https://github.com/nayuki/QR-Code-generator), TypeScript/JavaScript build, MIT license. Dependency-free, no DOM assumptions, exposes the global `qrcodegen` (the API `onboard.js` calls: `qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM)`).

**Why vendored, not CDN:** a censored-network user loading the onboarding page must not trigger any third-party request — it would leak that they're configuring a circumvention tool, and a CDN block would break the page. The whole bundle is self-contained + servable offline (ADR: "vendor the QR lib, no CDN").

### Vendoring procedure

The minified `qrcode.min.js` in this directory is fetched + pinned at build/release time, NOT hand-edited:

```bash
# Pin a release tag, fetch the JS build, minify, record the hash.
VER=v1.8.0
curl -fsSL -o /tmp/qrcodegen.js \
  "https://raw.githubusercontent.com/nayuki/QR-Code-generator/$VER/javascript/qrcodegen.js"
# Minify with any deterministic minifier (esbuild / terser), or ship
# the unminified file as qrcode.min.js (it's ~40KB unminified — fine).
esbuild /tmp/qrcodegen.js --minify --outfile=qrcode.min.js
sha256sum qrcode.min.js > qrcode.min.js.sha256
```

Record the version + SHA-256 here on each bump:

| Version | SHA-256 | Date |
|---------|---------|------|
| _pending vendor drop_ | _—_ | _—_ |

### Graceful degradation

`onboard.js` checks `typeof window.qrcodegen === "undefined"` before rendering the QR. If the real library hasn't been dropped in (this placeholder still present), the page renders everything else — platform detection, deep-link import buttons, copy-URL fallback, server-details expander — and shows a short "QR unavailable — use a button above or copy the URL below" note in place of the QR. The page is fully functional for import-by-button without the QR; the QR is a convenience for camera-scan import.

**Release gate:** the v0.12.0 cut MUST replace the placeholder with the real pinned `qrcode.min.js` (+ its recorded SHA) before tagging. The Tokyo smoke asserts the QR canvas renders.
