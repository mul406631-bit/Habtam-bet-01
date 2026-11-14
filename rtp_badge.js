/* rtp_badge.js -- shows players the RTP the games pay RIGHT NOW and tells them when it changes (never changed silently). */
(function () {
  "use strict";
  var last = null, badge = null;
  function ensure() {
    if (badge) return badge;
    badge = document.createElement("div"); badge.id = "rtp-badge";
    badge.style.cssText = "position:fixed;top:8px;right:8px;z-index:9999;padding:4px 10px;border-radius:12px;font:700 12px system-ui;background:rgba(15,23,42,.88);color:#fff;border:1px solid rgba(255,255,255,.25);pointer-events:none";
    document.body.appendChild(badge); return badge;
  }
  function toast(t) {
    var d = document.createElement("div"); d.textContent = t;
    d.style.cssText = "position:fixed;left:50%;top:44px;transform:translateX(-50%);z-index:10000;max-width:88%;padding:10px 14px;border-radius:12px;font:700 13px system-ui;background:#1d4ed8;color:#fff;text-align:center";
    document.body.appendChild(d); setTimeout(function () { d.remove(); }, 5000);
  }
  async function poll() {
    try {
      var r = await apiFetch("/house/rtp"); if (r.rtp == null) return;
      var pct = (r.rtp * 100).toFixed(1).replace(/\.0$/, "");
      ensure().textContent = "RTP " + pct + "%" + (r.adjusting ? " ⚙" : "");
      if (last !== null && Math.abs(last - r.rtp) >= 0.005) {
        toast((r.rtp < last ? "ℹ️ የጨዋታዎች RTP ተቀይሯል: " : "ℹ️ የጨዋታዎች RTP ተቀይሯል: ") + (last * 100).toFixed(1) + "% → " + pct + "%");
      }
      last = r.rtp;
    } catch (e) { /* offline / not logged in yet */ }
  }
  window.addEventListener("load", function () { setTimeout(poll, 3000); setInterval(poll, 20000); });
})();
