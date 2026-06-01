# Vendored assets

## qrcode.min.js — QR encoder

**What it is (v0.12.0):** a compact, self-contained QR Code generator
written in-tree — a faithful reimplementation of the QR spec
(ISO/IEC 18004), structured after [`nayuki/QR-Code-generator`](https://github.com/nayuki/QR-Code-generator)
(MIT). Byte-mode only (the onboarding page always encodes a URL),
ECC levels L/M/Q/H, versions 1-40. Exposes the global `qrcodegen` with
the surface `onboard.js` calls:

```js
qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM)  // → { size, getModule(x,y) }
```

**Why in-tree instead of a vendored upstream build (ADR O1 deviation,
operator-approved):** the build/release environment has no network
fetch + no JS toolchain to pin + minify an upstream artifact. Writing a
small, auditable encoder in the repo keeps the bundle self-contained
with zero external fetch + no build step — which was the whole point of
"no CDN" (censorship-resilience, no third-party leak). The trade-off is
we own the code instead of tracking upstream; it's ~400 lines of
well-specified algorithm, no dependencies.

**Validation status:** structurally tested in CI (API-surface presence
+ brace balance — see `tests/python-pkg/test_onboard.py`). The repo has
no JS runtime, so the rendered QR is **not** scan-validated in unit
tests. **Release/operator gate:** open the onboarding page in a real
browser and scan the QR with a client app before relying on QR import.
If the encoder ever throws, `onboard.js` degrades gracefully to the
"QR unavailable — use a button or copy the URL" note, so import-by-
button always works regardless.

**If you'd rather track upstream:** drop the minified
`nayuki/QR-Code-generator` JS build here (same `qrcodegen` global) and
record its version + SHA-256 below; `onboard.js` calls the identical
API either way.

| Source | Version | SHA-256 | Date |
|--------|---------|---------|------|
| in-tree reimpl | v0.12.0 | (tracked in git) | 2026-06-01 |
