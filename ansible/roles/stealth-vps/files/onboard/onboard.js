/* stealth-vps onboarding bridge (v0.12.0+).
 *
 * Pure client-side. Reads the subscription token from the URL path
 * (the segment after `/.well-known/stealth-vps-onboard/`), derives the
 * subscription URL, detects the visitor's OS, and renders one-tap
 * client deep-links + a QR code. No third-party requests, no analytics,
 * no inline scripts (CSP-clean). The only optional network call is the
 * lazy "Show details" bundle fetch.
 *
 * Design + ADRs: docs/internal/roadmap-v0.12-onboard-bridge.md
 *
 * DEEPLINKS below MUST stay in lockstep with
 * stealth_vps/onboard.py:DEEPLINK_TEMPLATES — a pytest parity test
 * (test_onboard.py) asserts the scheme prefixes match. If you change a
 * scheme here, change it there too.
 *
 * Scheme strings verified Q2/2026 against:
 *   Hiddify Next 2.x   — hiddify://install-sub?url=
 *   V2Box 1.8+         — v2box://install-sub?url=
 *   NekoBox 1.3+       — sn://subscription?url=
 *   sing-box 1.8+      — sing-box://import-remote-profile?url=
 *   Streisand (iOS)    — streisand://import/<raw-url>
 */

(function () {
  "use strict";

  // The path segment Caddy serves the bundle under. Keep in sync with
  // the role's stealth_vps_onboard_path default + the Caddyfile route.
  var ONBOARD_SEGMENT = "stealth-vps-onboard";
  var SUB_SEGMENT = "stealth-vps-sub";

  // Deep-link builders — { client: fn(subUrl) -> deeplink }.
  var DEEPLINKS = {
    hiddify: function (u) { return "hiddify://install-sub?url=" + encodeURIComponent(u); },
    v2box: function (u) { return "v2box://install-sub?url=" + encodeURIComponent(u); },
    nekobox: function (u) { return "sn://subscription?url=" + encodeURIComponent(u); },
    singbox: function (u) { return "sing-box://import-remote-profile?url=" + encodeURIComponent(u); },
    streisand: function (u) { return "streisand://import/" + encodeURIComponent(u); }
  };

  // Friendly labels for the buttons.
  var CLIENT_LABELS = {
    hiddify: "Hiddify",
    v2box: "V2Box",
    nekobox: "NekoBox",
    singbox: "sing-box",
    streisand: "Streisand"
  };

  // Platform → ordered client recommendations (primary first).
  var PLATFORM_CLIENTS = {
    android: ["hiddify", "nekobox", "v2box", "singbox"],
    ios: ["hiddify", "streisand", "v2box", "singbox"],
    macos: ["hiddify", "v2box", "singbox"],
    windows: ["hiddify", "v2box", "singbox"],
    linux: ["hiddify", "singbox"],
    unknown: ["hiddify", "v2box", "nekobox", "singbox", "streisand"]
  };

  // English string table. Structured so a zh-CN table can slot in later
  // (ADR O6 — English-only for v0.12).
  var STRINGS = {
    title: "Connect to your VPN",
    detected: "Detected platform:",
    tapToImport: "Tap to import into your client app:",
    scanQr: "Or scan it with your VPN app's own QR scanner (Add subscription → Scan QR):",
    copyUrl: "Copy subscription URL",
    copied: "Copied!",
    showDetails: "Show server details",
    noToken: "No subscription token in the URL. Ask your operator for a fresh onboarding link.",
    qrUnavailable: "QR unavailable — use a button above or copy the URL below.",
    qrHint: "Tip: pointing your phone camera at this just opens the raw subscription text — that's normal. Use your VPN app's own QR scanner (or a button above) instead.",
    moreOptions: "More client options"
  };

  function detectPlatform() {
    var ua = (navigator.userAgent || "").toLowerCase();
    // iPadOS 13+ reports as Mac; disambiguate via touch points.
    var isIpadOs = ua.indexOf("macintosh") > -1 && navigator.maxTouchPoints > 1;
    if (/android/.test(ua)) return "android";
    if (/iphone|ipad|ipod/.test(ua) || isIpadOs) return "ios";
    if (/macintosh|mac os x/.test(ua)) return "macos";
    if (/windows/.test(ua)) return "windows";
    if (/linux/.test(ua)) return "linux";
    return "unknown";
  }

  function tokenFromPath() {
    // /.well-known/stealth-vps-onboard/<token>  →  <token>
    var parts = location.pathname.split("/").filter(Boolean);
    var idx = parts.indexOf(ONBOARD_SEGMENT);
    if (idx === -1 || idx + 1 >= parts.length) return null;
    return decodeURIComponent(parts[idx + 1]);
  }

  function subUrlFor(token) {
    // Same origin; the sub endpoint sits at the parallel path. We keep
    // the `.well-known` prefix the role uses.
    return location.origin + "/.well-known/" + SUB_SEGMENT + "/" + encodeURIComponent(token);
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") node.className = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { node.appendChild(c); });
    return node;
  }

  function renderQr(container, text) {
    // Uses the vendored nayuki QR generator (window.qrcodegen). Degrades
    // to a text note if the lib didn't load (offline-first: we never
    // pull a CDN, so the only failure is a missing vendored file).
    if (typeof window.qrcodegen === "undefined") {
      container.appendChild(el("p", { class: "muted", text: STRINGS.qrUnavailable }));
      return;
    }
    try {
      var QRC = window.qrcodegen.QrCode;
      var qr = QRC.encodeText(text, QRC.Ecc.MEDIUM);
      var size = qr.size;
      var scale = 6, border = 4;
      var dim = (size + border * 2) * scale;
      var canvas = el("canvas", { width: dim, height: dim, "aria-label": "subscription QR code" });
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, dim, dim);
      ctx.fillStyle = "#000";
      for (var y = 0; y < size; y++) {
        for (var x = 0; x < size; x++) {
          if (qr.getModule(x, y)) {
            ctx.fillRect((x + border) * scale, (y + border) * scale, scale, scale);
          }
        }
      }
      container.appendChild(canvas);
    } catch (e) {
      container.appendChild(el("p", { class: "muted", text: STRINGS.qrUnavailable }));
    }
  }

  function main() {
    var root = document.getElementById("app");
    root.appendChild(el("h1", { text: STRINGS.title }));

    var token = tokenFromPath();
    if (!token) {
      root.appendChild(el("p", { class: "error", text: STRINGS.noToken }));
      return;
    }
    var subUrl = subUrlFor(token);
    var platform = detectPlatform();

    root.appendChild(el("p", { class: "muted" }, [
      document.createTextNode(STRINGS.detected + " "),
      el("strong", { text: platform })
    ]));

    // Deep-link buttons, platform-ordered.
    root.appendChild(el("p", { text: STRINGS.tapToImport }));
    var btnWrap = el("div", { class: "buttons" });
    var clients = PLATFORM_CLIENTS[platform] || PLATFORM_CLIENTS.unknown;
    clients.forEach(function (c) {
      var a = el("a", {
        class: "btn",
        href: DEEPLINKS[c](subUrl),
        rel: "noopener noreferrer"
      }, [document.createTextNode("Import to " + CLIENT_LABELS[c])]);
      btnWrap.appendChild(a);
    });
    root.appendChild(btnWrap);

    // QR.
    root.appendChild(el("p", { text: STRINGS.scanQr }));
    var qrWrap = el("div", { class: "qr" });
    renderQr(qrWrap, subUrl);
    root.appendChild(qrWrap);
    root.appendChild(el("p", { class: "muted", text: STRINGS.qrHint }));

    // Copy-URL fallback.
    var copyBtn = el("button", { class: "btn secondary", text: STRINGS.copyUrl });
    copyBtn.addEventListener("click", function () {
      navigator.clipboard && navigator.clipboard.writeText(subUrl).then(function () {
        copyBtn.textContent = STRINGS.copied;
        setTimeout(function () { copyBtn.textContent = STRINGS.copyUrl; }, 1500);
      });
    });
    root.appendChild(copyBtn);

    // Lazy "show details" — the ONLY network call, on tap (ADR O3).
    var details = el("details");
    details.appendChild(el("summary", { text: STRINGS.showDetails }));
    var detailBody = el("div", { class: "details-body" });
    details.appendChild(detailBody);
    var loaded = false;
    details.addEventListener("toggle", function () {
      if (!details.open || loaded) return;
      loaded = true;
      fetch(subUrl).then(function (r) { return r.text(); }).then(function (b64) {
        var decoded = "";
        try { decoded = atob(b64.trim()); } catch (e) { decoded = ""; }
        var uris = decoded.split("\n").filter(function (l) { return l.trim(); });
        if (!uris.length) {
          detailBody.appendChild(el("p", { class: "muted", text: "(no entries)" }));
          return;
        }
        uris.forEach(function (uri) {
          var scheme = uri.split("://", 1)[0];
          detailBody.appendChild(el("div", { class: "uri-row" }, [
            el("span", { class: "scheme", text: scheme }),
            el("code", { text: uri })
          ]));
        });
      }).catch(function () {
        detailBody.appendChild(el("p", { class: "muted", text: "(could not load details)" }));
      });
    });
    root.appendChild(details);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
