"""Central margin governor: keeps the HOUSE'S net margin at (at least) 40% across ALL games.

How it works (honest odds, not rigged rounds)
---------------------------------------------
Every game already derives its pay-table / multipliers from ONE house-edge number that is read through
``platform_controls`` (``global_house_edge``).  The governor sits on that single number:

  * start/floor: games start at ``governor_base_edge`` (default 0.15 -> RTP 85%) and relax back to it.
  * correction : it measures the REAL cumulative result of every game from the ``transactions`` ledger
                 (stakes - refunds = inflow, payouts = outflow).  When the real margin is below target the edge is
                 raised for the next bets (more house edge in the pay-tables) until the margin recovers, then it
                 relaxes back to the floor.  Capped by ``governor_max_edge``.
  * transparent: every change is shown to players (RTP badge + toast, GET /api/house/rtp); the edge the governor chooses is the same one the games publish as RTP (1 - edge), so what the
                 player sees is what is played.  Individual rounds are never overridden after the fact.

Because rounds are random, no system can GUARANTEE 40% in a short window - this converges over volume.  Until
``governor_min_volume`` ETB of inflow exists the floor edge is used.

Settings live in ``platform_settings`` (admin-editable, take effect on the very next bet; read cache <= 15 s).
"""
import time

from platform_controls import _f, _STAKE_SQL, _RETURN_SQL   # noqa: F401  (shared ledger transaction-type filters)

import os as _os
GOV_DEFAULTS = {
    "governor_enabled": _os.getenv("MARGIN_GOVERNOR_DEFAULT", "1"),   # tests of older fixed-edge behaviour set this to 0
    "governor_base_edge": "0.15",      # edge games START with (and relax back to); players see RTP = 1 - edge
    "target_margin": "0.40",          # house net margin target (0.40 = house keeps 40%, players get back 60%)
    "governor_min_volume": "500",     # ETB of inflow needed before the live correction starts
    "governor_window_days": "30",     # measure the last N days (0 = all time)
    "governor_gain": "1.5",           # extra edge = shortfall * gain
    "governor_max_edge": "0.60",      # absolute ceiling for the effective edge
}

_REFUND_SQL = ("(transaction_type LIKE 'game\\_%\\_refund' ESCAPE '\\' "
               "OR transaction_type IN ('group_mines_timeout_refund','sports_refund'))")

_CACHE = {"t": 0.0, "key": None, "val": None}
CACHE_SECONDS = 15.0


def ensure_defaults(cur):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for k, v in GOV_DEFAULTS.items():
        cur.execute("INSERT OR IGNORE INTO platform_settings(key,value,updated_at) VALUES(?,?,?)", (k, v, now))


def read_settings(cur):
    raw = dict(GOV_DEFAULTS)
    try:
        for r in cur.execute("SELECT key,value FROM platform_settings WHERE key IN (%s)" % ",".join("?" * len(GOV_DEFAULTS)),
                             tuple(GOV_DEFAULTS)).fetchall():
            raw[r[0]] = r[1]
    except Exception:
        pass
    t = min(0.95, max(0.0, _f(raw["target_margin"], 0.40)))
    base = min(0.95, max(0.0, _f(raw["governor_base_edge"], 0.15)))
    return {
        "base_edge": base,
        "enabled": raw["governor_enabled"] == "1",
        "target_margin": t,
        "min_volume": max(0.0, _f(raw["governor_min_volume"], 500.0)),
        "window_days": max(0.0, _f(raw["governor_window_days"], 30.0)),
        "gain": max(0.0, _f(raw["governor_gain"], 1.5)),
        "max_edge": min(0.95, max(t, base, _f(raw["governor_max_edge"], 0.60))),
    }


def ledger_totals(cur, window_days=0.0):
    """-> (inflow, payouts). inflow = stakes net of refunds; payouts = prize money paid out. All games."""
    since = "0000-00-00 00:00:00"
    if window_days:
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - window_days * 86400))
    row = cur.execute(
        f"""SELECT
              COALESCE(SUM(CASE WHEN {_STAKE_SQL} THEN -amount ELSE 0 END),0)                       AS staked,
              COALESCE(SUM(CASE WHEN {_REFUND_SQL} THEN amount ELSE 0 END),0)                        AS refunded,
              COALESCE(SUM(CASE WHEN {_RETURN_SQL} AND NOT {_REFUND_SQL} THEN amount ELSE 0 END),0)  AS paid
            FROM transactions WHERE created_at>=?""", (since,)).fetchone()
    staked, refunded, paid = float(row[0]), float(row[1]), float(row[2])
    return max(0.0, staked - refunded), max(0.0, paid)


def status(cur, force=False):
    """Live governor state. Cached ~15 s per process (the ledger SUM is the only expensive part)."""
    s = read_settings(cur)
    key = tuple(sorted(s.items()))
    now = time.time()
    if not force and _CACHE["val"] is not None and _CACHE["key"] == key and now - _CACHE["t"] < CACHE_SECONDS:
        return _CACHE["val"]
    inflow, paid = ledger_totals(cur, s["window_days"])
    margin = (1.0 - paid / inflow) if inflow > 0 else None
    boost = 0.0
    active = False
    if s["enabled"] and margin is not None and inflow >= s["min_volume"] and margin < s["target_margin"]:
        shortfall = s["target_margin"] - margin
        boost = min(s["max_edge"] - s["base_edge"], shortfall * s["gain"])
        active = boost > 0
    floor = s["base_edge"] if s["enabled"] else None
    effective = (min(s["max_edge"], s["base_edge"] + boost)) if s["enabled"] else None
    val = {
        "enabled": s["enabled"], "target_margin": s["target_margin"], "target_rtp": round(1 - s["target_margin"], 6),
        "inflow": round(inflow, 2), "payouts": round(paid, 2), "house_profit": round(inflow - paid, 2),
        "actual_margin": None if margin is None else round(margin, 6),
        "actual_rtp": None if margin is None else round(1 - margin, 6),
        "boost": round(boost, 6), "base_edge": s["base_edge"], "current_rtp": None if effective is None else round(1 - effective, 6), "correcting": active, "floor_edge": floor, "effective_edge": effective,
        "window_days": s["window_days"], "min_volume": s["min_volume"], "max_edge": s["max_edge"],
        "on_target": (margin is not None and margin >= s["target_margin"] - 1e-9),
    }
    _CACHE.update(t=now, key=key, val=val)
    return val


def apply_to_controls(cur, c):
    """Mutate the typed controls dict from platform_controls.get_controls so every game sees the governed edge."""
    st = status(cur)
    c["governor"] = st
    if not st["enabled"]:
        return c
    base = c["global_house_edge"] if c["global_house_edge_enabled"] else 0.0
    c["global_house_edge_enabled"] = True
    c["global_house_edge"] = min(st["max_edge"], max(base, st["base_edge"]) + st["boost"])
    return c


def reset_cache():
    _CACHE.update(t=0.0, key=None, val=None)
