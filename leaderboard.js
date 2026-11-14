/* leaderboard.js -- weekly top-10 winners (Mon-Sun). Data: GET /api/leaderboard/weekly. Names are inserted with textContent only. */
(function () {
  "use strict";
  var timer = null, tick = null, secondsLeft = 0;
  var MEDAL = { 1: "🥇", 2: "🥈", 3: "🥉" };

  function fmt(n) { return Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 }); }
  function left(s) {
    s = Math.max(0, Math.floor(s));
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d + "ቀ " + h + "ሰ " + m + "ደ";
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function render(data) {
    var host = document.getElementById("lb-list"); if (!host) return;
    host.textContent = "";
    secondsLeft = data.seconds_left || 0;
    var t = document.getElementById("lb-timer"); if (t) t.textContent = left(secondsLeft);
    if (!data.top || !data.top.length) { host.appendChild(el("div", "lb-empty", "በዚህ ሳምንት ገና አሸናፊ የለም — የመጀመሪያው ይሁኑ! 🏆")); }
    (data.top || []).forEach(function (r) {
      var row = el("div", "lb-row lb-r" + (r.rank <= 3 ? r.rank : "n") + (r.is_me ? " lb-me" : ""));
      row.appendChild(el("span", "lb-rank", MEDAL[r.rank] || String(r.rank)));
      row.appendChild(el("span", "lb-name", r.name + (r.is_me ? " (እርስዎ)" : "")));
      row.appendChild(el("span", "lb-won", fmt(r.won) + " ብር"));
      host.appendChild(row);
    });
    var me = document.getElementById("lb-me");
    if (me && data.me) {
      me.textContent = data.me.rank ? ("ደረጃዎ: #" + data.me.rank + " · " + fmt(data.me.won) + " ብር")
                                    : ("ደረጃዎ: ገና አልተመደቡም · በዚህ ሳምንት " + fmt(data.me.won) + " ብር");
    }
  }

  async function load() {
    var host = document.getElementById("lb-list");
    try { render(await apiFetch("/leaderboard/weekly")); }
    catch (e) { if (host) { host.textContent = ""; host.appendChild(el("div", "lb-empty", "❌ " + (e && e.message || "መጫን አልተቻለም"))); } }
  }

  function start() {
    load(); stop();
    timer = setInterval(function () { var p = document.getElementById("page-leaderboard"); if (p && p.classList.contains("active") && !document.hidden) load(); }, 30000);
    tick = setInterval(function () { secondsLeft = Math.max(0, secondsLeft - 1); var t = document.getElementById("lb-timer"); if (t) t.textContent = left(secondsLeft); }, 1000);
  }
  function stop() { if (timer) clearInterval(timer); if (tick) clearInterval(tick); timer = tick = null; }

  document.addEventListener("click", function (e) {      // profile shortcut (the sidebar button is handled by kdm_ui.js)
    var b = e.target.closest && e.target.closest("#open-leaderboard"); if (b && window.goToPage) goToPage("leaderboard");
  });
  window.loadLeaderboard = start;
  window.stopLeaderboard = stop;
})();
