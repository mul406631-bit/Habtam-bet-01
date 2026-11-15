/* HOME WALLET CARD -- Deposit / Withdraw straight from the Home page.
   Presentation + shortcuts only. It NEVER calls /deposit or /withdraw itself:
   it fills the real Wallet forms (#deposit-form / #withdraw-form) and submits THEM, so every
   existing check (min amount, balance, wager rule, confirm dialog, admin approval) stays exactly the same.
   Loaded after kdm_neon.js. Remove the <link>/<script> in index.html to revert. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return typeof escapeHtml === "function" ? escapeHtml(String(s == null ? "" : s)) : String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var tick = function (t) { try { if (typeof haptic === "function") haptic(t || "light"); } catch (_) {} };
  var open = ""; // "", "deposit", "withdraw"
  var accLoaded = false, pickedFile = null;

  function profile() { try { return typeof lastProfile !== "undefined" ? lastProfile : null; } catch (_) { return null; } }
  function num(v) { v = Number(v); return isFinite(v) ? v : 0; }
  function money(v) { return num(v).toFixed(2); }
  function balance() { var p = profile(); if (p && p.balance != null) return num(p.balance); var b = $("balance"); return b ? parseFloat(b.textContent) || 0 : 0; }
  function withdrawable() { var p = profile(); if (p && p.withdrawable != null) return Math.max(0, num(p.withdrawable)); return balance(); }
  function saved() { try { return JSON.parse(localStorage.getItem("kdm_wd_acc") || "null"); } catch (_) { return null; } }

  /* ------------------------------------------------------------ markup */
  function build() {
    var lobby = $("page-lobby"), hero = $("kn-hero");
    if (!lobby || $("hw-card")) return !!$("hw-card");
    var c = document.createElement("section");
    c.id = "hw-card"; c.setAttribute("aria-label", "Deposit and Withdraw");
    c.innerHTML =
      '<div class="hw-top">' +
        '<div class="hw-bal"><small>ቦርሳዬ • My Wallet</small><b><span id="hw-balance">0.00</span> <em>ETB</em></b>' +
        '<span class="hw-sub">ማውጣት የሚችሉት: <i id="hw-avail">0.00</i> ብር</span></div>' +
        '<button type="button" class="hw-hist" data-hw="history" aria-label="History">📜<span id="hw-dot" hidden></span></button>' +
      '</div>' +
      '<div class="hw-actions">' +
        '<button type="button" class="hw-btn hw-dep" data-hw="deposit"><span>➕</span><b>Deposit</b><small>ገንዘብ አስገባ</small></button>' +
        '<button type="button" class="hw-btn hw-wd" data-hw="withdraw"><span>💸</span><b>Withdraw</b><small>ገንዘብ አውጣ</small></button>' +
      '</div>' +
      '<div class="hw-panel" id="hw-dep-panel" hidden>' +
        '<div class="hw-step">1️⃣ ክፍያውን ወደ አንዱ አካውንት ይላኩ</div>' +
        '<div id="hw-accounts" class="hw-accounts"><div class="hw-muted">⏳ በመጫን ላይ...</div></div>' +
        '<div class="hw-step">2️⃣ የከፈሉትን መጠን ይምረጡ</div>' +
        '<div class="hw-chips" data-for="hw-dep-amount"><button type="button" data-v="50">50</button><button type="button" data-v="100">100</button><button type="button" data-v="200">200</button><button type="button" data-v="500">500</button><button type="button" data-v="1000">1000</button></div>' +
        '<input id="hw-dep-amount" type="number" min="1" step="0.01" inputmode="decimal" placeholder="መጠን (ብር)">' +
        '<div class="hw-step">3️⃣ የክፍያ Screenshot ይላኩ</div>' +
        '<label class="hw-file" for="hw-dep-file"><span id="hw-file-name">📸 Screenshot ይምረጡ</span></label>' +
        '<input id="hw-dep-file" type="file" accept="image/*" hidden>' +
        '<img id="hw-dep-prev" alt="" hidden>' +
        '<button type="button" class="hw-send" id="hw-dep-send">✅ Deposit ላክ</button>' +
        '<p class="hw-status" id="hw-dep-status"></p>' +
      '</div>' +
      '<div class="hw-panel" id="hw-wd-panel" hidden>' +
        '<div class="hw-step">1️⃣ የማውጫ ዘዴ</div>' +
        '<div class="hw-methods" id="hw-methods"><button type="button" data-method="Telebirr">📱 Telebirr</button><button type="button" data-method="CBE">🏦 CBE</button><button type="button" data-method="BOA">🏦 BOA</button></div>' +
        '<div class="hw-step">2️⃣ የሚያወጡት መጠን <small id="hw-wd-hint"></small></div>' +
        '<div class="hw-chips" data-for="hw-wd-amount"><button type="button" data-v="100">100</button><button type="button" data-v="500">500</button><button type="button" data-v="1000">1000</button><button type="button" data-v="max">ሁሉንም</button></div>' +
        '<input id="hw-wd-amount" type="number" min="1" step="0.01" inputmode="decimal" placeholder="መጠን (ብር)">' +
        '<div class="hw-step">3️⃣ የአካውንት መረጃ</div>' +
        '<input id="hw-wd-name" type="text" autocomplete="name" placeholder="የአካውንቱ ባለቤት ስም">' +
        '<input id="hw-wd-number" type="text" inputmode="numeric" autocomplete="off" placeholder="የአካውንት / ስልክ ቁጥር">' +
        '<label class="hw-rem"><input type="checkbox" id="hw-wd-rem" checked> <span>ለቀጣይ አስታውስ</span></label>' +
        '<button type="button" class="hw-send hw-send-wd" id="hw-wd-send">✅ Withdraw ላክ</button>' +
        '<p class="hw-status" id="hw-wd-status"></p>' +
      '</div>';
    if (hero) hero.after(c); else lobby.insertBefore(c, lobby.firstChild);
    wire(c); prefillWithdraw(); paint();
    return true;
  }

  /* ------------------------------------------------------------ behaviour */
  function toggle(which) {
    open = open === which ? "" : which;
    $("hw-dep-panel").hidden = open !== "deposit";
    $("hw-wd-panel").hidden = open !== "withdraw";
    document.querySelectorAll("#hw-card .hw-btn").forEach(function (b) { b.classList.toggle("on", b.dataset.hw === open); });
    if (open === "deposit") loadAccounts();
    if (open === "withdraw") { prefillWithdraw(); paint(); }
    if (open) { var p = $(open === "deposit" ? "hw-dep-panel" : "hw-wd-panel"); setTimeout(function () { try { p.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (_) {} }, 60); }
  }
  function goWallet(sub) {
    try { goToPage("wallet"); } catch (_) {}
    var b = document.querySelector('#wallet-subtabs button[data-wsub="' + sub + '"]'); if (b) b.click();
  }
  function loadAccounts() {
    if (accLoaded) return;
    var box = $("hw-accounts");
    apiFetch("/deposit/info").then(function (i) {
      var rows = [["👤 ስም", i.account_name, false], ["📱 Telebirr", i.telebirr, true], ["🏦 CBE", i.cbe, true], ["🏦 BOA", i.boa, true]].filter(function (r) { return r[1]; });
      box.innerHTML = rows.map(function (r, k) {
        return '<div class="hw-acc"><span>' + esc(r[0]) + ': <b>' + esc(r[1]) + '</b></span>' + (r[2] ? '<button type="button" data-copy="' + k + '">📋 ቅዳ</button>' : "") + "</div>";
      }).join("") || '<div class="hw-muted">የክፍያ አካውንት አልተዘጋጀም</div>';
      box.__rows = rows; accLoaded = true;
    }).catch(function (e) { box.innerHTML = '<div class="hw-muted">❌ ' + esc(e && e.message || "አልተጫነም") + ' <button type="button" class="hw-retry">እንደገና</button></div>'; });
  }
  function prefillWithdraw() {
    var s = saved(); if (!s) return;
    if ($("hw-wd-name") && !$("hw-wd-name").value) $("hw-wd-name").value = s.name || "";
    if ($("hw-wd-number") && !$("hw-wd-number").value) $("hw-wd-number").value = s.number || "";
    if (s.method && !document.querySelector("#hw-methods button.on")) pickMethod(s.method);
  }
  function pickMethod(m) { document.querySelectorAll("#hw-methods button").forEach(function (b) { b.classList.toggle("on", b.dataset.method === m); }); }
  function setStatus(id, txt, cls) { var e = $(id); if (!e) return; e.textContent = txt || ""; e.className = "hw-status" + (cls ? " " + cls : ""); }
  function mirror(srcId, dstId, after) {   // show the real form's result text under the home form
    var src = $(srcId); if (!src || !window.MutationObserver) return;
    new MutationObserver(function () {
      var t = (src.textContent || "").trim(); if (!t || open === "") return;
      setStatus(dstId, t, t.indexOf("✅") === 0 ? "ok" : t.indexOf("❌") === 0 ? "err" : "");
      if (t.indexOf("✅") === 0 && after) after();
    }).observe(src, { childList: true, characterData: true, subtree: true });
  }

  function sendDeposit() {
    var amt = parseFloat($("hw-dep-amount").value), real = $("deposit-form");
    if (!amt || amt < 1) { setStatus("hw-dep-status", "❌ የገንዘብ መጠን ያስገቡ (ዝቅተኛ 1 ብር)", "err"); tick("error"); return; }
    if (!pickedFile) { setStatus("hw-dep-status", "❌ የክፍያ Screenshot ይምረጡ", "err"); tick("error"); return; }
    try {
      var dt = new DataTransfer(); dt.items.add(pickedFile);
      $("deposit-screenshot").files = dt.files; $("deposit-screenshot").dispatchEvent(new Event("change", { bubbles: true }));
    } catch (_) { goWallet("deposit"); $("deposit-amount").value = String(amt); setStatus("hw-dep-status", "ፋይሉን በWallet ገጽ ላይ ይምረጡ", ""); return; }
    $("deposit-amount").value = String(amt);
    setStatus("hw-dep-status", "⏳ በመላክ ላይ...", "");
    if (real.requestSubmit) real.requestSubmit(); else real.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
  }
  function sendWithdraw() {
    var m = document.querySelector("#hw-methods button.on"), amt = parseFloat($("hw-wd-amount").value);
    var name = $("hw-wd-name").value.trim(), numb = $("hw-wd-number").value.trim(), real = $("withdraw-form");
    if (!m) { setStatus("hw-wd-status", "❌ የማውጫ ዘዴ ይምረጡ (Telebirr / CBE / BOA)", "err"); tick("error"); return; }
    if (!amt || amt < 1) { setStatus("hw-wd-status", "❌ ዝቅተኛው የማውጫ መጠን 1 ብር ነው", "err"); tick("error"); return; }
    if (!name || !numb) { setStatus("hw-wd-status", "❌ ስም እና የአካውንት ቁጥር ያስገቡ", "err"); tick("error"); return; }
    var rb = document.querySelector('#withdraw-methods button[data-method="' + m.dataset.method + '"]'); if (rb) rb.click();
    $("withdraw-amount").value = String(amt); $("withdraw-name").value = name; $("withdraw-number").value = numb;
    $("withdraw-remember").checked = $("hw-wd-rem").checked;
    setStatus("hw-wd-status", "", "");
    if (real.requestSubmit) real.requestSubmit(); else real.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));   // existing handler asks "confirm?" then sends
  }

  function wire(c) {
    c.addEventListener("click", function (e) {
      var t = e.target;
      var a = t.closest("[data-hw]");
      if (a) { tick(); if (a.dataset.hw === "history") goWallet("history"); else toggle(a.dataset.hw); return; }
      var ch = t.closest(".hw-chips button");
      if (ch) {
        var inp = $(ch.parentNode.dataset.for), v = ch.dataset.v;
        if (v === "max") v = String(Math.floor(withdrawable() * 100) / 100);
        if (inp && Number(v) > 0) { inp.value = v; tick(); } else if (v === "0") { setStatus("hw-wd-status", "ለማውጣት የሚችሉት ገንዘብ የለም", "err"); }
        return;
      }
      var m = t.closest("#hw-methods button"); if (m) { pickMethod(m.dataset.method); tick(); return; }
      var cp = t.closest("[data-copy]");
      if (cp) {
        var row = $("hw-accounts").__rows && $("hw-accounts").__rows[Number(cp.dataset.copy)]; if (!row) return;
        var done = function () { var o = cp.textContent; cp.textContent = "✅ ተቀድቷል"; tick("success"); setTimeout(function () { cp.textContent = o; }, 1500); };
        try { navigator.clipboard.writeText(String(row[1])).then(done, function () { cp.textContent = "⚠️ ይምረጡ"; }); } catch (_) { cp.textContent = "⚠️ ይምረጡ"; }
        return;
      }
      if (t.closest(".hw-retry")) { accLoaded = false; loadAccounts(); return; }
      if (t.closest("#hw-dep-send")) { sendDeposit(); return; }
      if (t.closest("#hw-wd-send")) { sendWithdraw(); return; }
    });
    $("hw-dep-file").addEventListener("change", function (e) {
      pickedFile = e.target.files && e.target.files[0] || null;
      $("hw-file-name").textContent = pickedFile ? "✅ " + pickedFile.name : "📸 Screenshot ይምረጡ";
      var pv = $("hw-dep-prev");
      if (pickedFile) { pv.src = URL.createObjectURL(pickedFile); pv.hidden = false; } else pv.hidden = true;
    });
    mirror("deposit-status", "hw-dep-status", function () {
      pickedFile = null; $("hw-dep-file").value = ""; $("hw-dep-amount").value = ""; $("hw-dep-prev").hidden = true; $("hw-file-name").textContent = "📸 Screenshot ይምረጡ";
    });
    mirror("withdraw-status", "hw-wd-status", function () { $("hw-wd-amount").value = ""; });
  }

  /* ------------------------------------------------------------ live numbers */
  function paint() {
    var b = $("hw-balance"); if (!b) return;
    b.textContent = money(balance());
    var w = withdrawable(); $("hw-avail").textContent = money(w);
    var hint = $("hw-wd-hint"); if (hint) hint.textContent = "(ከፍተኛ " + money(w) + ")";
    var p = profile(); $("hw-dot").hidden = !(p && p.has_pending);
    // the Wallet page's own "available to withdraw" line used to show the total balance (bonus included): show the withdrawable part
    var wa = $("withdraw-avail"); if (wa && p && p.withdrawable != null) wa.textContent = money(w);
  }

  function boot() {
    var tries = 0, t = setInterval(function () {
      if (build() || ++tries > 60) clearInterval(t);
    }, 250);
    setInterval(paint, 1500);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
