"""Admin panel regression: every admin change must be saved AND take effect.  Run: python admin_full_regression_test.py"""
import hashlib, hmac, json, os, sqlite3, sys, tempfile, time
from urllib.parse import urlencode
TD = tempfile.mkdtemp()
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", ADMIN_ID="1", OWNER_ID="1", ACCOUNT_NAME="T", CBE_ACCOUNT="1", BOA_ACCOUNT="1",
  TELEBIRR_NUMBER="1", MINI_APP_URL="https://x.example/", MINI_APP_API_URL="https://x.example/api",
  ADMIN_PANEL_PIN="1234", SUPPORT_CONTACT="@s", DB_FILE=os.path.join(TD, "t.db"), PLATFORM_BANKROLL_ETB="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webapp_api as W
app = W.app; app.config["TESTING"] = True; C = app.test_client(); TOKEN = os.environ["BOT_TOKEN"]
def init_data(uid):
    f = {"auth_date": str(int(time.time())), "query_id": "q", "user": json.dumps({"id": uid, "first_name": "U", "username": f"u{uid}"})}
    d = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    f["hash"] = hmac.new(hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest(), d.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)
def call(m, p, uid, body=None, pin=None):
    h = {"X-Telegram-Init-Data": init_data(uid)}
    if pin: h["X-Admin-Pin-Token"] = pin
    r = getattr(C, m)(p, headers=h, **({"json": body} if body is not None else {}))
    return r.status_code, (r.get_json(silent=True) if r.is_json else {})
def ok(c, msg):
    if not c: raise SystemExit("FAIL: " + msg)
    print("  ok -", msg)
call("get", "/api/profile", 1)
c, r = call("post", "/api/admin/verify-pin", 1, {"pin": "1234"}); PIN = r.get("token") or r.get("pin_token") or next(v for k, v in r.items() if "token" in k)
adm = lambda m, p, b=None: call(m, p, 1, b, PIN)
for u in (601, 602):
    call("get", "/api/profile", u); call("post", "/api/accept-terms", u, {"age_confirmed": True, "terms_accepted": True, "accept": True})
def item(k): return [i for i in adm("get", "/api/admin/game-control")[1]["items"] if i["key"] == k][0]

print("[1] every game: save -> read back, toggle OFF/ON reflected for players")
items = adm("get", "/api/admin/game-control")[1]["items"]
ok(any(i["key"] == "minesx" for i in items), "minesx is controllable from the admin list")
for i in items:
    k = i["key"]
    if k in ("sports", "marketplace"): continue
    if k in ("bomb", "number"): body = {"key": k, "enabled": True, "entry_fee": 3.0, "house_edge": 0.1}
    else:
        body = {"key": k, "enabled": True, "min_stake": 2.0, "max_stake": 77.0, "house_edge": 0.07}
        if k == "wheel": body["wheel_prizes"] = i["wheel_prizes"]
    c, r = adm("post", "/api/admin/game-control", body); ok(c == 200, f"{k} saved ({c} {r if c != 200 else ''})")
    n = item(k)
    if k in ("bomb", "number"): ok(abs(n["entry_fee"] - 3.0) < 1e-9 and abs(n["house_edge"] - .1) < 1e-9, f"{k} entry fee/edge read back")
    else:
        ok(abs(n["min_stake"] - 2.0) < 1e-9 and abs(n["max_stake"] - 77.0) < 1e-9 and abs(n["house_edge"] - .07) < 1e-9, f"{k} min/max/edge read back")
        adm("post", "/api/admin/game-control", {**body, "enabled": False})
        c, r = call("get", f"/api/games/{k}/settings", 601); ok(r.get("enabled") is False, f"{k} OFF visible to players")
        adm("post", "/api/admin/game-control", body)

print("[2] wheel: 20 prizes save and are re-normalised to the chosen house edge")
w = item("wheel"); ok(len(w["wheel_prizes"]) == 20, "wheel has 20 sectors in the admin list")
c, r = adm("post", "/api/admin/game-control", {"key": "wheel", "enabled": True, "min_stake": 1, "max_stake": 50, "house_edge": 0.1, "wheel_prizes": [0, 0, 1, 2] * 5})
ok(c == 200, "20 prizes accepted")
ok(abs(sum(item("wheel")["wheel_prizes"]) / 20 - 0.9) < 0.01, "average payout = 1 - house edge")

print("[3] cabinet-games partial update keeps min/max/edge (no silent reset to 1/500)")
adm("post", "/api/admin/game-control", {"key": "slot", "enabled": True, "min_stake": 5, "max_stake": 900, "house_edge": 0.08})
c, r = adm("post", "/api/admin/cabinet-games/settings", {"game_key": "slot", "enabled": False}); ok(c == 200, "toggle only 'enabled'")
n = item("slot"); ok(n["min_stake"] == 5 and n["max_stake"] == 900 and abs(n["house_edge"] - .08) < 1e-9 and n["enabled"] is False, "limits untouched, game OFF")
adm("post", "/api/admin/cabinet-games/settings", {"game_key": "slot", "enabled": True})

print("[4] global override is visible to the admin (no silent ignoring)")
adm("post", "/api/admin/platform-controls", {"global_min_bet_enabled": True, "global_min_bet": 4, "global_house_edge_enabled": False})
n = item("slot"); ok(n["min_overridden"] is True and n["effective_min_stake"] == 4.0, "slot flagged: global min 4 overrides its own 5")
adm("post", "/api/admin/platform-controls", {"global_min_bet_enabled": False})
n = item("slot"); ok(n["min_overridden"] is False and n["effective_min_stake"] == 5.0, "global off -> slot's own min 5 applies")
c, r = call("post", "/api/games/slot/round/start", 601, {"stake": 2}); ok(c == 400 and "between" in str(r.get("error")), "player stake 2 < admin min 5 refused")

print("[5] deposit bonus: partial update keeps the other fields")
adm("post", "/api/admin/deposit-bonus", {"enabled": True, "bonus_percent": 15, "delay_hours": 2, "min_deposit": 50, "max_bonus": 500})
c, r = adm("post", "/api/admin/deposit-bonus", {"enabled": False}); st = r["settings"]
ok(c == 200 and st["enabled"] == 0 and st["bonus_percent"] == 15 and st["min_deposit"] == 50 and st["max_bonus"] == 500, "only 'enabled' changed")

print("[6] global House Edge: own 4-field save works, overrides every game, per-game save reports 'not applied', global OFF restores own value")
adm("post", "/api/admin/game-control", {"key": "dice", "enabled": True, "min_stake": 1, "max_stake": 100, "house_edge": 0.03})
c, r = adm("post", "/api/admin/platform-controls", {"global_min_bet_enabled": False, "global_min_bet": 1, "global_house_edge_enabled": True, "global_house_edge_percent": 6})
ok(c == 200 and abs(r["controls"]["global_house_edge"] - .06) < 1e-9, "global edge 6% saved with only the 4 section fields")
for k in ("dice", "slot", "keno"):
    ok(abs(call("get", f"/api/games/{k}/settings", 601)[1]["house_edge"] - .06) < 1e-9, f"{k}: players get global 6%")
c, r = adm("post", "/api/admin/game-control", {"key": "dice", "enabled": True, "min_stake": 1, "max_stake": 100, "house_edge": 0.09})
ok(r["edge_overridden"] is True and abs(r["global_house_edge"] - .06) < 1e-9, "per-game save tells the admin it is overridden by global 6%")
ok(abs(call("get", "/api/games/dice/settings", 601)[1]["house_edge"] - .06) < 1e-9, "dice still 6% while global is ON")
adm("post", "/api/admin/platform-controls", {"global_house_edge_enabled": False})
ok(abs(call("get", "/api/games/dice/settings", 601)[1]["house_edge"] - .09) < 1e-9, "global OFF -> dice uses its own 9%")
c, r = adm("post", "/api/admin/game-control", {"key": "dice", "enabled": True, "min_stake": 1, "max_stake": 100, "house_edge": 0.04})
ok(r["edge_overridden"] is False and abs(call("get", "/api/games/dice/settings", 601)[1]["house_edge"] - .04) < 1e-9, "per-game edge change applies at once (4%)")

print("ADMIN_FULL_REGRESSION=PASS")
