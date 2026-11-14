"""Proof for: global 40% margin governor, weekly top-10 leaderboard, admin report.
Run:  python central_v12_e2e_test.py   -> CENTRAL_V12_E2E=PASS"""
import hashlib, hmac, json, os, random, sqlite3, sys, tempfile, time
from urllib.parse import urlencode

TD = tempfile.mkdtemp()
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", ADMIN_ID="1", OWNER_ID="1", ACCOUNT_NAME="T", CBE_ACCOUNT="1", BOA_ACCOUNT="1",
                  TELEBIRR_NUMBER="1", MINI_APP_URL="https://x.example/", MINI_APP_API_URL="https://x.example/api",
                  ADMIN_PANEL_PIN="1234", SUPPORT_CONTACT="@s", DB_FILE=os.path.join(TD, "t.db"), PLATFORM_BANKROLL_ETB="0",
                  DISABLE_COST_ALERT_WORKER="1", DISABLE_ADMIN_REPORT_WORKER="1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webapp_api as W                                   # noqa: E402
import margin_governor as MG                             # noqa: E402

app = W.app; app.config["TESTING"] = True
C = app.test_client(); TOKEN = os.environ["BOT_TOKEN"]


def init_data(uid, name="U"):
    f = {"auth_date": str(int(time.time())), "query_id": "q", "user": json.dumps({"id": uid, "first_name": name, "username": f"u{uid}"})}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    sk = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(sk, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)


def call(method, path, uid, body=None, pin=None):
    h = {"X-Telegram-Init-Data": init_data(uid, f"Player{uid}")}
    if pin: h["X-Admin-Pin-Token"] = pin
    r = getattr(C, method)(path, headers=h, json=body) if body is not None else getattr(C, method)(path, headers=h)
    return r.status_code, (r.get_json(silent=True) or {})


def dbx(sql, args=()):
    c = sqlite3.connect(os.environ["DB_FILE"]); c.row_factory = sqlite3.Row
    try:
        r = [dict(x) for x in c.execute(sql, args).fetchall()]; c.commit(); return r
    finally: c.close()


def ok(cond, msg):
    if not cond: raise SystemExit("FAIL: " + msg)
    print("  ok -", msg)


NOW = time.strftime("%Y-%m-%d %H:%M:%S")
def tx(uid, amt, typ): dbx("INSERT INTO transactions(user_id,amount,transaction_type,created_at) VALUES(?,?,?,?)", (uid, amt, typ, NOW))
def reset_ledger(): dbx("DELETE FROM transactions WHERE transaction_type LIKE 'game\\_%' ESCAPE '\\'"); MG.reset_cache()
def edge_now():
    conn = W.db()
    try: return W._pc.effective_controls(conn.cursor())["global_house_edge"]
    finally: conn.close()

ADMIN = 1
call("get", "/api/profile", ADMIN)
code, r = call("post", "/api/admin/verify-pin", ADMIN, {"pin": "1234"})
PIN = next((v for k, v in r.items() if "token" in k), None)
adm = lambda m, p, b=None: call(m, p, ADMIN, b, PIN)

print("[1] governor: every game starts at the 15% base edge (RTP 85%), no ledger yet")
ok(abs(edge_now() - 0.15) < 1e-9, "effective house edge starts at 15% for ALL games")
for key in ("slot", "plinko", "roulette", "keno", "number"):
    en, mn, mx, edge = W._cabinet_limits(key)
    ok(abs(edge - 0.15) < 1e-9, f"game '{key}' plays with edge {edge:.2f}")
ok(abs(W._pc.effective_rake(0.05, W._controls()) - 0.15) < 1e-9, "pool/sports rake also follows the governed edge")

print("[2] governor: house margin below the 40% target -> edge rises toward it, relaxes back to 15%")
for u in (801, 802): call("get", "/api/profile", u)
tx(801, -3000, "game_slot_stake"); tx(801, 2400, "game_slot_payout")      # RTP 80% -> margin 20% (target 40)
MG.reset_cache(); st = MG.status(W.db().cursor(), force=True)
ok(st["correcting"] and abs(st["actual_margin"] - 0.20) < 1e-9, f"real margin 20% < 40% detected (boost {st['boost']:.3f})")
e1 = edge_now(); ok(abs(e1 - 0.45) < 1e-9, f"edge raised 15% -> {e1:.2f}")
code, pub = call("get", "/api/house/rtp", 801)
ok(code == 200 and abs(pub["rtp"] - 0.55) < 1e-9 and pub["adjusting"] and pub["base_rtp"] == 0.85, f"players can SEE the change: {pub}")
en, mn, mx, edge = W._cabinet_limits("slot"); ok(abs(edge - e1) < 1e-9, "slot pay-table edge follows the governor (same number the player sees as RTP)")
tx(801, -9000, "game_slot_stake"); tx(801, 4000, "game_slot_payout")     # inflow 12000, paid 6400 -> margin 46.7%
MG.reset_cache(); ok(abs(edge_now() - 0.15) < 1e-9, "margin recovered above 40% -> edge relaxes back to the 15% base")
code, pub = call("get", "/api/house/rtp", 801); ok(abs(pub["rtp"] - 0.85) < 1e-9 and not pub["adjusting"], "...and players see RTP back at 85%")
reset_ledger()
tx(801, -10, "game_slot_stake"); tx(801, 10, "game_slot_payout")
MG.reset_cache(); ok(abs(edge_now() - 0.15) < 1e-9, "tiny volume (<min volume) does not trigger corrections")
reset_ledger()
adm("post", "/api/admin/platform-controls", {"governor_enabled": False}); MG.reset_cache()
_raw = edge_now(); ok(abs(_raw - 0.15) > 1e-9 and 0 < _raw < 0.15, f"admin can switch governor off (falls back to the per-game edges, here {_raw:.2f})")
adm("post", "/api/admin/platform-controls", {"governor_enabled": True}); MG.reset_cache()

print("[3] convergence simulation: 200k random bets, a high-variance game, players on a lucky streak at the start")
def simulate(use_governor, seed):
    rnd = random.Random(seed); inflow = paid = 0.0
    for i in range(200000):
        stake = 10.0
        if use_governor and inflow >= 2000:
            margin = 1 - paid / inflow
            edge = 0.40 + (min(0.20, (0.40 - margin) * 1.5) if margin < 0.40 else 0.0)
        else: edge = 0.40
        p = 0.10; mult = (1 - edge) / p
        win = rnd.random() < (p + (0.04 if i < 3000 else 0))   # lucky streak: true hit-rate 14% for the first 3000 bets
        inflow += stake; paid += stake * mult if win else 0
    return 1 - paid / inflow
no_gov = simulate(False, 7); with_gov = simulate(True, 7)
print(f"      margin without governor {no_gov*100:.2f}%   with governor {with_gov*100:.2f}%")
ok(with_gov >= 0.395, "with the governor the long-run margin stays at ~40% or better")
ok(with_gov > no_gov, "the governor beats the ungoverned house after the lucky streak")

print("[4] weekly leaderboard")
reset_ledger()
for i, (uid, net) in enumerate([(901, 5000), (902, 3200), (903, 900), (904, -400), (905, 150)]):
    call("get", "/api/profile", uid)
    tx(uid, -1000, "game_slot_stake"); tx(uid, 1000 + net, "game_slot_payout")
dbx("UPDATE users SET banned=1 WHERE user_id=902")
tx(906, -50, "game_slot_stake"); tx(906, 50, "game_slot_refund")                 # refund nets to 0 -> not a winner
dbx("INSERT OR IGNORE INTO users(user_id,first_name) VALUES(907,'Old')")
dbx("INSERT INTO transactions(user_id,amount,transaction_type,created_at) VALUES(907,99999,'game_slot_payout','2020-01-01 00:00:00')")  # prior week
W._LB_CACHE.update(t=0.0, rows=None)
code, lb = call("get", "/api/leaderboard/weekly", 905)
ok(code == 200, "endpoint answers")
names = [(r["rank"], r["won"]) for r in lb["top"]]
ok(names == [(1, 5000.0), (2, 900.0), (3, 150.0)], f"ranked by amount won, banned/loser/refund/old-week excluded: {names}")
ok(lb["me"] == {"rank": 3, "won": 150.0}, "caller's own rank returned")
ok(any(r["is_me"] for r in lb["top"]), "caller is highlighted")
code, lb2 = call("get", "/api/leaderboard/weekly", 904)
ok(lb2["me"]["rank"] is None and lb2["me"]["won"] == -400.0, "a losing player sees no rank but his real week result")
ok(lb["seconds_left"] > 0 and len(lb["top"]) <= 10, "week countdown + max 10 rows")
for i in range(15):
    call("get", "/api/profile", 1000 + i); tx(1000 + i, -10, "game_slot_stake"); tx(1000 + i, 10 + (i + 1) * 7, "game_slot_payout")
W._LB_CACHE.update(t=0.0, rows=None)
code, lb3 = call("get", "/api/leaderboard/weekly", 905)
ok(len(lb3["top"]) == 10 and [r["rank"] for r in lb3["top"]] == list(range(1, 11)), "exactly top 10 (1..10)")
ok(call("get", "/api/leaderboard/weekly", 905)[0] == 200 and C.get("/api/leaderboard/weekly").status_code in (401, 403), "needs Telegram auth")

print("[5] admin report")
reset_ledger()
for u, st_, pd in ((801, 1000, 700), (802, 500, 100)):
    tx(u, -st_, "game_slot_stake"); tx(u, pd, "game_slot_payout")
conn = W.db(); rep = W.build_admin_report(conn.cursor(), 1); conn.close()
ok(rep["inflow"] == 1500 and rep["payouts"] == 800 and abs(rep["margin"] - (700 / 1500)) < 1e-4, f"report numbers correct (margin {rep['margin']})")
ok(rep["on_target"] is True and rep["games"][0]["game"] == "slot", "target check + per-game table")
txt = W.format_admin_report(rep); ok("RTP" in txt and "ሪፖርት" in txt, "Amharic report text built")
sent = []
W.tg_send_message = lambda chat, text, *a, **k: (sent.append((chat, text)) or {"ok": True})
code, r = adm("post", "/api/admin/margin-report", {"days": 7, "send": True})
ok(code == 200 and r["sent_to_admin"] is True and sent and sent[0][0] == 1, "POST sends the report to the admin on Telegram")
ok(call("get", "/api/admin/margin-report", 905)[0] in (401, 403), "regular players cannot read it")
W.REPORT_HOUR = 0   # make the test independent of the time of day
sent.clear(); W._report_tick()
ok(len(sent) >= 1 and "ሪፖርት" in sent[0][1], "daily scheduler tick sends exactly once...")
n = len(sent); W._report_tick(); ok(len(sent) == n, "...and not again the same day")
reset_ledger(); tx(801, -1000, "game_slot_stake"); tx(801, 900, "game_slot_payout"); MG.reset_cache(); sent.clear()
W._claim_slot("margin_alert_last_hour", "x"); W._report_tick()
ok(any("ማስጠንቀቂያ" in t for _, t in sent), "instant alert when real margin is far below 40%")
print("CENTRAL_V12_E2E=PASS")
