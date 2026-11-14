/* haptics.js -- vibration feedback for the whole Mini App (loaded BEFORE app.js).
 *  - every button / card / nav tap          -> short tick
 *  - tapping anywhere else on the screen    -> very soft tick (taps only, never while scrolling)
 *  - placing a bet / starting a round       -> medium thump
 *  - winning                                -> success buzz (+ extra pulses for a big win)
 *  - losing                                 -> soft warning
 * Uses Telegram HapticFeedback; falls back to navigator.vibrate() in plain browsers. Players can switch it off
 * (Profile -> 📳 ንዝረት); the choice is remembered. Never throws: a haptic failure must not break a bet. */
(function () {
  "use strict";
  var KEY = "kdm_haptics";
  var tg = (window.Telegram && window.Telegram.WebApp) || null;
  var hf = tg && tg.HapticFeedback;
  var last = 0, lastWin = 0;

  function enabled() { try { return localStorage.getItem(KEY) !== "0"; } catch (e) { return true; } }
  function setEnabled(on) {
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) {}
    if (on) tap();
  }
  function vib(p) { try { if (navigator.vibrate) navigator.vibrate(p); } catch (e) {} }
  function impact(style, fallback) {
    if (!enabled()) return;
    try { if (hf && hf.impactOccurred) { hf.impactOccurred(style); return; } } catch (e) {}
    vib(fallback);
  }
  function notify(type, fallback) {
    if (!enabled()) return;
    try { if (hf && hf.notificationOccurred) { hf.notificationOccurred(type); return; } } catch (e) {}
    vib(fallback);
  }
  function tap() { impact("light", 12); }
  function soft() { impact("soft", 6); }
  function select() {
    if (!enabled()) return;
    try { if (hf && hf.selectionChanged) { hf.selectionChanged(); return; } } catch (e) {}
    vib(8);
  }
  function bet() { impact("medium", 25); }
  function win(big) {
    var now = Date.now();
    if (now - lastWin < 700) return;           // several hooks can report the same win
    lastWin = now;
    notify("success", [40, 40, 90]);
    if (big) {
      setTimeout(function () { impact("heavy", 60); }, 160);
      setTimeout(function () { impact("heavy", 60); }, 320);
      setTimeout(function () { notify("success", [30, 30, 30]); }, 520);
    }
  }
  function lose() { notify("warning", [30]); }

  /* ---- taps ---- */
  var INTERACTIVE = 'button,[role="button"],a[href],summary,select,label,input,textarea,[data-go],[data-page],[data-quick-page],.game-card,.game-tile,.mine-cell,.road-step,.fish-target';
  document.addEventListener("pointerdown", function (e) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    var t = e.target && e.target.closest ? e.target.closest(INTERACTIVE) : null;
    if (!t || t.disabled) return;
    var now = Date.now();
    if (now - last < 35) return;
    last = now;
    if (t.closest("#bottom-nav") || t.hasAttribute("data-go") || t.hasAttribute("data-page")) select(); else tap();
  }, true);
  document.addEventListener("click", function (e) {          // a tap on empty screen area (click is not fired for scrolls)
    var now = Date.now();
    if (now - last < 120) return;                             // a button already vibrated
    last = now;
    soft();
  }, true);

  /* ---- game events: called from app.js apiFetch ---- */
  var GAME_PATH = /\/(games|sports|cabinet|live|realtime|blackjack|baccarat|roulette|keno|plinko|mines|crash)\b.*\/(play|bet|pick|deal|hit|stand|cashout|spin|drop|join|place|start|reveal|step)\b|\/sports\/bet|\/bets?$/i;
  function onRequest(path, method) {
    if (String(method || "GET").toUpperCase() !== "POST") return;
    if (GAME_PATH.test(String(path))) bet();
  }
  function fromResponse(path, method, body) {
    if (String(method || "GET").toUpperCase() !== "POST" || !body || typeof body !== "object") return;
    if (!GAME_PATH.test(String(path))) return;
    var payout = Number(body.payout), stake = Number(body.stake);
    var won = body.won === true || body.status === "WON" || (isFinite(payout) && isFinite(stake) && stake > 0 && payout > stake + 0.005);
    var lost = body.won === false || body.status === "LOST";
    if (won) win(isFinite(payout) && isFinite(stake) && stake > 0 && payout >= stake * 5);
    else if (lost) lose();
  }

  window.KdmHaptics = { enabled: enabled, setEnabled: setEnabled, tap: tap, soft: soft, select: select, bet: bet, win: win, lose: lose,
                        onRequest: onRequest, fromResponse: fromResponse,
                        notify: notify, impact: impact };

  /* games that celebrate with the shared confetti helper also buzz (live rounds do not go through apiFetch) */
  window.addEventListener("load", function () {
    var orig = window.celebrateWin;
    if (typeof orig === "function" && !orig.__haptic) {
      window.celebrateWin = function () { win(false); return orig.apply(this, arguments); };
      window.celebrateWin.__haptic = true;
    }
    var box = document.getElementById("haptics-toggle");
    if (box) { box.checked = enabled(); box.addEventListener("change", function () { setEnabled(box.checked); }); }
  });
})();
