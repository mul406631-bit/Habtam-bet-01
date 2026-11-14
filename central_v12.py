# central_v12.py -- loaded by webapp_api.py via exec() (same globals as rewards_v9.py / royal_v1.py).
#   * WEEKLY LEADERBOARD  : top-10 winners of the current week (Mon 00:00 -> Sun 24:00, server time = Africa/Addis_Ababa)
#                           with the amount each one won, + "my rank" for the caller.
#   * MARGIN / ADMIN REPORT: global 40% margin vs. reality, per-game RTP, bonus cost, deposits/withdrawals, top winners.
#                           Sent to the admin on Telegram (daily + instant alert when margin < target) and available as JSON.
import re as _re
import time as _t2
import threading as _th2
from datetime import datetime as _dt2, timedelta as _td2

import margin_governor as _mg

_GAME_TX_SQL = f"({_pc._STAKE_SQL} OR {_pc._RETURN_SQL})"
_LB_CACHE = {"t": 0.0, "week": None, "rows": None}
LEADERBOARD_SIZE = 10


def _week_bounds(now=None):
    now = now or _dt2.now()
    start = (now - _td2(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _td2(days=7)
    return start, end


def _mask_name(first_name, username, uid):
    n = (first_name or "").strip() or (("@" + username) if username else "") or f"Player{str(uid)[-4:]}"
    n = _re.sub(r"[\r\n\t<>]", " ", n)
    return n[:14]


def _weekly_rows(cur, start):
    """[(user_id, name, net_won)] best first. net_won = payouts - stakes inside the week (refunds net out); winners only."""
    cur.execute(f"""SELECT t.user_id uid, ROUND(SUM(t.amount),2) net, u.first_name fn, u.username un
                    FROM transactions t JOIN users u ON u.user_id=t.user_id
                    WHERE t.created_at>=? AND {_GAME_TX_SQL.replace('transaction_type','t.transaction_type')}
                      AND COALESCE(u.banned,0)=0
                    GROUP BY t.user_id HAVING SUM(t.amount)>0
                    ORDER BY net DESC, t.user_id ASC LIMIT 500""", (start.strftime("%Y-%m-%d %H:%M:%S"),))
    return [(int(r["uid"]), _mask_name(r["fn"], r["un"], r["uid"]), float(r["net"])) for r in cur.fetchall()]


def _leaderboard_rows(cur):
    start, _ = _week_bounds()
    now = _t2.time()
    if _LB_CACHE["rows"] is not None and _LB_CACHE["week"] == start and now - _LB_CACHE["t"] < 30:
        return _LB_CACHE["rows"]
    rows = _weekly_rows(cur, start)
    _LB_CACHE.update(t=now, week=start, rows=rows)
    return rows


@app.route("/api/leaderboard/weekly")
@require_telegram_auth
def leaderboard_weekly():
    conn = db(); cur = conn.cursor()
    try:
        rows = _leaderboard_rows(cur)
        uid = int(g.tg_user["id"])
        start, end = _week_bounds()
        top = [{"rank": i + 1, "name": n, "won": round(w, 2), "is_me": u == uid} for i, (u, n, w) in enumerate(rows[:LEADERBOARD_SIZE])]
        me = None
        for i, (u, n, w) in enumerate(rows):
            if u == uid:
                me = {"rank": i + 1, "won": round(w, 2)}
                break
        if me is None:
            cur.execute(f"SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE user_id=? AND created_at>=? AND {_GAME_TX_SQL}",
                        (uid, start.strftime("%Y-%m-%d %H:%M:%S")))
            me = {"rank": None, "won": round(float(cur.fetchone()["s"]), 2)}   # not in the winners list (net <= 0)
        return jsonify({"week_start": start.strftime("%Y-%m-%d"), "week_end": (end - _td2(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
                        "seconds_left": max(0, int((end - _dt2.now()).total_seconds())), "currency": "ETB",
                        "top": top, "me": me})
    finally:
        conn.close()


@app.route("/api/house/rtp")
@require_telegram_auth
def house_rtp():
    """Public to every player: the RTP the games are paying RIGHT NOW (1 - edge) and why. The Mini App shows it live."""
    conn = db(); cur = conn.cursor()
    try:
        c = _pc.effective_controls(cur); st = c.get("governor") or {}
        edge = float(c["global_house_edge"]) if c["global_house_edge_enabled"] else None
        return jsonify({"rtp": None if edge is None else round(1 - edge, 4), "edge": edge,
                        "adjusting": bool(st.get("correcting")), "base_rtp": None if not st.get("enabled") else round(1 - st["base_edge"], 4)})
    finally:
        conn.close()


# ----------------------------------------------------------------------------------------------------------------
# ADMIN / OWNER REPORT
# ----------------------------------------------------------------------------------------------------------------
def _per_game(cur, since):
    cur.execute(f"""SELECT transaction_type t, COALESCE(SUM(amount),0) s FROM transactions
                    WHERE created_at>=? AND {_GAME_TX_SQL} GROUP BY transaction_type""", (since,))
    games = {}
    for r in cur.fetchall():
        t, a = r["t"], float(r["s"])
        m = _re.match(r"^game_(.+)_(stake|payout|refund)$", t)
        if m:
            name, kind = m.group(1), m.group(2)
        elif t.startswith("sports"):
            name, kind = "sports", ("stake" if t.endswith("_bet") else "payout" if t.endswith("_win") else "refund")
        else:
            name, kind = t.rsplit("_", 1)[0], ("stake" if t.endswith("_bet") else "payout" if t.endswith("_win") else "refund")
        d = games.setdefault(name, {"game": name, "staked": 0.0, "paid": 0.0})
        if kind == "stake": d["staked"] += -a
        elif kind == "refund": d["staked"] -= a
        else: d["paid"] += a
    out = []
    for d in games.values():
        if d["staked"] <= 0: continue
        d["profit"] = round(d["staked"] - d["paid"], 2)
        d["rtp"] = round(d["paid"] / d["staked"], 4)
        d["staked"], d["paid"] = round(d["staked"], 2), round(d["paid"], 2)
        out.append(d)
    return sorted(out, key=lambda x: -x["staked"])


def build_admin_report(cur, days=1):
    days = max(1, min(90, int(days)))
    since = (_dt2.now() - _td2(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    games = _per_game(cur, since)
    inflow = round(sum(x["staked"] for x in games), 2)
    paid = round(sum(x["paid"] for x in games), 2)
    margin = None if inflow <= 0 else round(1 - paid / inflow, 4)
    gov = _mg.status(cur, force=True)
    bonus = _bonus_report_data(cur, days)
    cur.execute("SELECT COUNT(DISTINCT user_id) n FROM transactions WHERE created_at>=? AND " + _GAME_TX_SQL, (since,)); active = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM payments WHERE status='approved' AND COALESCE(approved_at,created_at)>=?", (since,)); dep = cur.fetchone()
    try:
        cur.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM withdrawals WHERE status IN ('approved','paid','completed') AND created_at>=?", (since,)); wd = cur.fetchone()
        wdraw = {"count": int(wd["n"]), "total": round(float(wd["s"]), 2)}
    except Exception:
        wdraw = {"count": 0, "total": 0.0}
    start, _ = _week_bounds()
    top = [{"name": n, "won": round(w, 2)} for _, n, w in _weekly_rows(cur, start)[:5]]
    bonus_cost = float(bonus.get("bonus_given_total", 0) or 0)
    return {
        "generated_at": _now(), "days": days, "target_margin": gov["target_margin"], "target_rtp": gov["target_rtp"],
        "inflow": inflow, "payouts": paid, "house_gross": round(inflow - paid, 2), "margin": margin,
        "rtp": None if margin is None else round(1 - margin, 4),
        "bonus_and_cashback_cost": round(bonus_cost, 2), "house_net_after_bonus": round(inflow - paid - bonus_cost, 2),
        "margin_after_bonus": None if inflow <= 0 else round((inflow - paid - bonus_cost) / inflow, 4),
        "on_target": margin is not None and margin >= gov["target_margin"] - 1e-9,
        "governor": gov, "active_players": active, "deposits": {"count": int(dep["n"]), "total": round(float(dep["s"]), 2)},
        "withdrawals": wdraw, "top_winners_week": top, "games": games,
    }


def format_admin_report(r):
    pct = lambda x: "—" if x is None else f"{x * 100:.1f}%"
    gov = r["governor"]
    lines = [f"📊 የቤቱ ሪፖርት — ያለፉት {r['days']} ቀን",
             f"💰 አጠቃላይ ውርርድ (Inflow): {r['inflow']:.2f} ብር",
             f"🎁 ለተጫዋቾች የተከፈለ: {r['payouts']:.2f} ብር  (RTP {pct(r['rtp'])}, ገደብ {pct(r['target_rtp'])})",
             f"🏠 የቤቱ ትርፍ: {r['house_gross']:.2f} ብር  (Margin {pct(r['margin'])}, ግብ {pct(r['target_margin'])})",
             f"🎟 ቦነስ + Cashback ወጪ: {r['bonus_and_cashback_cost']:.2f} ብር → ከቦነስ በኋላ ትርፍ {r['house_net_after_bonus']:.2f} ብር ({pct(r['margin_after_bonus'])})",
             ("✅ ግቡ ተሟልቷል" if r["on_target"] else "⚠️ ከግቡ በታች ነው") +
             (f" · ሞተሩ እያስተካከለ ነው (edge {pct(gov['effective_edge'])})" if gov.get("correcting") else ""),
             f"👥 ንቁ ተጫዋቾች: {r['active_players']} · 📥 Deposit {r['deposits']['count']} ({r['deposits']['total']:.2f}) · 📤 Withdraw {r['withdrawals']['count']} ({r['withdrawals']['total']:.2f})"]
    if r["games"]:
        lines.append("\n🎮 ከፍተኛ ጨዋታዎች (ውርርድ · RTP · ትርፍ):")
        for x in r["games"][:8]:
            lines.append(f"• {x['game']}: {x['staked']:.0f} · {x['rtp'] * 100:.0f}% · {x['profit']:+.0f}")
        bad = [x for x in r["games"] if x["staked"] >= 200 and x["rtp"] > r["target_rtp"] + 0.10]
        if bad:
            lines.append("⚠️ RTP ከግቡ በላይ: " + ", ".join(f"{x['game']} {x['rtp'] * 100:.0f}%" for x in bad[:5]))
    if r["top_winners_week"]:
        lines.append("\n🏆 የሳምንቱ ከፍተኛ አሸናፊዎች: " + " · ".join(f"{i + 1}. {w['name']} {w['won']:.0f}" for i, w in enumerate(r["top_winners_week"])))
    return "\n".join(lines)


def send_admin_report(days=1, reason="daily"):
    conn = db(); cur = conn.cursor()
    try:
        text = format_admin_report(build_admin_report(cur, days))
    finally:
        conn.close()
    sent = False
    for chat in dict.fromkeys([ADMIN_ID]):          # the admin (OWNER_ID is alerted by the existing cost alert)
        res = tg_send_message(chat, text, parse_mode=None)
        sent = sent or bool(res.get("ok"))
    return sent


def _claim_slot(key, value):
    """True for exactly ONE worker per (key, value): used to dedupe the daily report / alert across gunicorn workers."""
    conn = db(); cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT value FROM platform_settings WHERE key=?", (key,)).fetchone()
        if row and row["value"] == value:
            conn.rollback(); return False
        cur.execute("INSERT INTO platform_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, value, _now()))
        conn.commit(); return True
    finally:
        conn.close()


REPORT_HOUR = int(os.getenv("ADMIN_REPORT_HOUR", "8"))                 # server time (Africa/Addis_Ababa), 0-23
MARGIN_ALERT_MIN_VOLUME = float(os.getenv("MARGIN_ALERT_MIN_VOLUME", "500"))


def _report_tick():
    now = _dt2.now()
    if now.hour >= REPORT_HOUR and _claim_slot("admin_report_last_day", now.strftime("%Y-%m-%d")):
        send_admin_report(1, "daily")
    conn = db(); cur = conn.cursor()
    try:
        st = _mg.status(cur, force=True)
    finally:
        conn.close()
    if st["enabled"] and st["actual_margin"] is not None and st["inflow"] >= MARGIN_ALERT_MIN_VOLUME and st["actual_margin"] < st["target_margin"] - 0.02:
        if _claim_slot("margin_alert_last_hour", now.strftime("%Y-%m-%d %H")):
            tg_send_message(ADMIN_ID,
                            f"⚠️ የትርፍ ማስጠንቀቂያ\nየቤቱ Margin {st['actual_margin'] * 100:.1f}% ነው (ግብ {st['target_margin'] * 100:.0f}%).\n"
                            f"RTP {st['actual_rtp'] * 100:.1f}% · Inflow {st['inflow']:.0f} · ትርፍ {st['house_profit']:.0f} ብር\n"
                            f"ሞተሩ edge ወደ {st['effective_edge'] * 100:.1f}% አሳድጓል።", parse_mode=None)


def _report_worker():
    _t2.sleep(90)
    while True:
        try: _report_tick()
        except Exception: logger.exception("admin report worker failed")
        _t2.sleep(600)


if not os.getenv("DISABLE_ADMIN_REPORT_WORKER"):
    _th2.Thread(target=_report_worker, name="admin-report-worker", daemon=True).start()


@app.route("/api/admin/margin-report", methods=["GET", "POST"])
@require_telegram_auth
@require_admin
@require_fresh_auth
def admin_margin_report():
    """GET ?days=7 -> JSON report. POST {"days":7,"send":true} -> also sends it to the admin's Telegram."""
    body = request.get_json(silent=True) or {} if request.method == "POST" else {}
    try: days = int(body.get("days") or request.args.get("days") or 1)
    except (TypeError, ValueError): return jsonify({"error": "days must be a number"}), 400
    conn = db(); cur = conn.cursor()
    try:
        rep = build_admin_report(cur, days)
    finally:
        conn.close()
    sent = None
    if request.method == "POST" and body.get("send"):
        sent = send_admin_report(days, "manual")
    return jsonify({"report": rep, "text": format_admin_report(rep), "sent_to_admin": sent})
