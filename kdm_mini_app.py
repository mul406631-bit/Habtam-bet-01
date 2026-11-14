# ============================================================
# KDM COMBINED FINAL BASE
# This file is based on the latest LIVE/HOUSE30 code.
# The previously merged auction/wallet/admin code is already
# present in this base; functions are not duplicated.
# ============================================================

import sqlite3
import random
import secrets
import logging
import asyncio
import time
import os
import json
import html
import hmac
import hashlib
from io import BytesIO
from urllib.parse import parse_qsl

from aiohttp import web
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

from bonus_wallet import record_invite, release_invite_bonus, invite_requires_deposit, withdrawable as _withdrawable, INVITE_BONUS_ETB

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ANNOUNCEMENT_CHANNEL_ID = os.getenv("ANNOUNCEMENT_CHANNEL_ID", "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

# ============================================================
# TELEGRAM MINI APP CONFIG
# ============================================================
# IMPORTANT: Telegram Mini Apps must be served from HTTPS in production.
# Example: https://your-domain.com/
WEBAPP_URL = os.getenv("MINI_APP_URL", "").strip()
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8080"))
MINI_APP_DIR = os.getenv("MINI_APP_DIR", os.path.dirname(os.path.abspath(__file__)))
MINI_APP_MAX_AUTH_AGE = 86400

# ============================================================
# ADMIN / MANAGER PERMISSIONS
# ============================================================

def is_admin(user_id):
    return int(user_id) == ADMIN_ID

def is_manager(user_id):
    if int(user_id) == ADMIN_ID:
        return True
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT manager FROM users WHERE user_id=?", (int(user_id),))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["manager"])

def is_staff(user_id):
    return is_admin(user_id) or is_manager(user_id)

def get_manager_ids():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE manager=1")
    ids = [int(r["user_id"]) for r in cur.fetchall()]
    conn.close()
    return list(dict.fromkeys([ADMIN_ID] + ids))

async def send_staff_message(context, text, reply_markup=None, parse_mode="Markdown"):
    for staff_id in get_manager_ids():
        try:
            await context.bot.send_message(chat_id=staff_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logging.warning(f"Staff message delivery failed for {staff_id}: {e}")

async def send_staff_photo(context, photo_id, caption, reply_markup=None):
    for staff_id in get_manager_ids():
        try:
            await context.bot.send_photo(chat_id=staff_id, photo=photo_id, caption=caption, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            logging.warning(f"Staff photo delivery failed for {staff_id}: {e}")


ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "").strip()
CBE_ACCOUNT = os.getenv("CBE_ACCOUNT", "").strip()
BOA_ACCOUNT = os.getenv("BOA_ACCOUNT", "").strip()
TELEBIRR_NUMBER = os.getenv("TELEBIRR_NUMBER", "").strip()
ADMIN_PANEL_PIN = os.getenv("ADMIN_PANEL_PIN", "").strip()

DB_FILE = os.getenv("DB_FILE", "auction_wallet.db")

# Shared canonical schema/migrations. This runs before bot-local helpers so
# API-first and bot-first startup produce the same money tables.
from extended_schema import init_extended_schema

def _schedule_bot_coro(bot_application, coro):
    loop = bot_application.bot_data.get("event_loop")
    if loop is None or loop.is_closed():
        raise RuntimeError("Telegram application event loop is not available")
    return asyncio.run_coroutine_threadsafe(coro, loop)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ============================================================
# HIGH-TRAFFIC LIVE CONFIG
# ============================================================
# This protects a weak server from creating an unbounded number of
# simultaneous Telegram API requests.
LIVE_EDIT_CONCURRENCY = 20
LIVE_EDIT_TIMEOUT = 8
LIVE_MAX_RECENT_PLAYERS = 30
LIVE_EDIT_BATCH_SIZE = 100

LIVE_EDIT_SEMAPHORE = asyncio.Semaphore(LIVE_EDIT_CONCURRENCY)


# ============================================================
# OLD AUCTION TIERS
# Used only for automatic fallback auctions
# ============================================================

AUCTION_TIERS = [
    {
        "level": 1,
        "ticket_price": 35.0,
        "prize_amount": 1000.0,
        "days": 0,
        "hours": 23,
    },
    {
        "level": 2,
        "ticket_price": 50.0,
        "prize_amount": 2000.0,
        "days": 1,
        "hours": 23,
    },
    {
        "level": 3,
        "ticket_price": 100.0,
        "prize_amount": 5000.0,
        "days": 2,
        "hours": 23,
    },
    {
        "level": 4,
        "ticket_price": 250.0,
        "prize_amount": 10000.0,
        "days": 3,
        "hours": 23,
    },
    {
        "level": 5,
        "ticket_price": 500.0,
        "prize_amount": 25000.0,
        "days": 4,
        "hours": 23,
    },
]


# ============================================================
# DATABASE
# ============================================================

def db():
    # High-traffic SQLite connection. WAL is enabled once in init_db(),
    # not on every request (journal_mode is expensive when repeated).
    conn = sqlite3.connect(
        DB_FILE,
        timeout=8,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def add_column_if_missing(cur, table, column, definition):
    try:
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )
    except sqlite3.OperationalError:
        pass


def init_db():
    init_extended_schema(DB_FILE, ADMIN_ID)
    conn = db()
    cur = conn.cursor()

    # Set SQLite journal mode once at startup, not once per user request.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            balance REAL DEFAULT 0,
            manager INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS support_messages (
            admin_message_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT,
            direction TEXT DEFAULT 'user_to_admin',
            content_type TEXT DEFAULT 'text'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            first_name TEXT,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            photo_file_id TEXT,
            created_at TEXT,
            approved_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            acc_name TEXT,
            acc_number TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            transaction_type TEXT,
            payment_id INTEGER,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS aviator_rounds (
            round_id INTEGER PRIMARY KEY AUTOINCREMENT,
            crash_multiplier REAL NOT NULL DEFAULT 0,
            status TEXT DEFAULT 'betting',
            created_at TEXT,
            crashed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS aviator_bets (
            bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            bet_amount REAL NOT NULL,
            cashout_multiplier REAL,
            payout REAL DEFAULT 0,
            status TEXT DEFAULT 'placed',
            created_at TEXT,
            FOREIGN KEY(round_id) REFERENCES aviator_rounds(round_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tier_level INTEGER NOT NULL DEFAULT 1,
            prize_amount REAL NOT NULL,
            ticket_price REAL NOT NULL,
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT,
            ends_at TEXT,
            warning_sent INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auction_id INTEGER,
            user_id INTEGER,
            bid_amount REAL NOT NULL,
            created_at TEXT,
            FOREIGN KEY(auction_id) REFERENCES auctions(id)
        )
    """)

    # --------------------------------------------------------
    # Admin-only auction simulation participants
    # These are intentionally kept separate from real bids/users.
    # They never affect winner calculation or real-user wallets.
    # --------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auction_simulations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auction_id INTEGER NOT NULL,
            simulated_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            created_by INTEGER
        )
    """)

    # --------------------------------------------------------
    # Database compatibility
    # --------------------------------------------------------

    add_column_if_missing(
        cur, "auctions",
        "tier_level",
        "INTEGER NOT NULL DEFAULT 1"
    )

    add_column_if_missing(
        cur, "auctions",
        "ends_at",
        "TEXT"
    )

    add_column_if_missing(
        cur, "auctions",
        "warning_sent",
        "INTEGER DEFAULT 0"
    )

    add_column_if_missing(
        cur, "auctions",
        "winner_user_id",
        "INTEGER"
    )

    add_column_if_missing(
        cur, "auctions",
        "winning_bid",
        "REAL"
    )

    add_column_if_missing(
        cur, "auctions",
        "total_collected",
        "REAL DEFAULT 0"
    )

    add_column_if_missing(
        cur, "auctions",
        "closed_at",
        "TEXT"
    )

    add_column_if_missing(
        cur, "withdrawals",
        "acc_name",
        "TEXT"
    )

    add_column_if_missing(cur, "withdrawals", "acc_number", "TEXT")
    add_column_if_missing(cur, "withdrawals", "screenshot_file_id", "TEXT")
    add_column_if_missing(cur, "withdrawals", "staff_reminded_at", "TEXT")
    add_column_if_missing(cur, "auctions", "post_chat_id", "TEXT")
    add_column_if_missing(cur, "auctions", "post_message_id", "INTEGER")
    add_column_if_missing(cur, "users", "last_seen", "TEXT")
    add_column_if_missing(cur, "users", "manager", "INTEGER DEFAULT 0")
    add_column_if_missing(cur, "support_messages", "direction", "TEXT DEFAULT 'user_to_admin'")
    add_column_if_missing(cur, "support_messages", "content_type", "TEXT DEFAULT 'text'")

    # Critical indexes for high-volume users/bets.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_round_status ON aviator_bets(round_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_round_user_status ON aviator_bets(round_id, user_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_round_bet_id ON aviator_bets(round_id, bet_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON transactions(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bids_auction_user ON bids(auction_id, user_id)")
    conn.commit()

    # Auctions are created manually by Admin only.

    conn.close()


# ============================================================
# AUCTION CREATION
# ============================================================

def create_next_automatic_auction(cur):
    cur.execute("""
        SELECT tier_level
        FROM auctions
        ORDER BY id DESC
        LIMIT 1
    """)

    last_auc = cur.fetchone()

    if not last_auc:
        next_level = 1
    else:
        current_level = int(
            last_auc["tier_level"] or 1
        )

        next_level = (
            current_level % len(AUCTION_TIERS)
        ) + 1

    tier = AUCTION_TIERS[next_level - 1]

    now = datetime.now()

    ends_at = now + timedelta(
        days=tier["days"],
        hours=tier["hours"]
    )

    cur.execute("""
        INSERT INTO auctions (
            tier_level,
            prize_amount,
            ticket_price,
            status,
            created_at,
            ends_at,
            warning_sent
        )
        VALUES (?, ?, ?, 'ACTIVE', ?, ?, 0)
    """, (
        tier["level"],
        tier["prize_amount"],
        tier["ticket_price"],
        now.strftime("%Y-%m-%d %H:%M:%S"),
        ends_at.strftime("%Y-%m-%d %H:%M:%S")
    ))


def create_custom_auction(prize, ticket, hours, minutes):
    conn = db()
    cur = conn.cursor()

    now = datetime.now()
    ends_at = now + timedelta(
        hours=hours,
        minutes=minutes
    )

    cur.execute("""
        INSERT INTO auctions (
            tier_level,
            prize_amount,
            ticket_price,
            status,
            created_at,
            ends_at,
            warning_sent
        )
        VALUES (
            0, ?, ?, 'ACTIVE', ?, ?, 0
        )
    """, (
        prize,
        ticket,
        now.strftime("%Y-%m-%d %H:%M:%S"),
        ends_at.strftime("%Y-%m-%d %H:%M:%S")
    ))

    auction_id = cur.lastrowid

    conn.commit()
    conn.close()

    return auction_id


# ============================================================
# AUCTION POST + PARTICIPANT HELPERS
# ============================================================

def build_auction_post(auction):
    # User-facing view: show ONLY the combined participant count.
    # Real/Simulation breakdown remains Admin-only.
    real_participants, _ = get_auction_participant_stats(auction["id"])
    simulated_participants = get_auction_simulation_count(auction["id"])
    total_participants = real_participants + simulated_participants

    return (f"🎯 **ጨረታ #{auction['id']}**\n\n"
            f"🏆 ሽልማት: `{float(auction['prize_amount']):.2f}` ብር\n"
            f"🎟 Ticket: `{float(auction['ticket_price']):.2f}` ብር\n"
            f"⏰ የሚያበቃው: `{auction['ends_at']}`\n"
            f"👥 ተሳታፊዎች: `{total_participants}` ሰዎች\n\n"
            f"💡 Bid ከ 1 ብር ጀምሮ ይቻላል።\n"
            f"🏆 Lowest Unique Bid ያሸንፋል።")

def get_user_auction_bids(auction_id, user_id):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT bid_amount FROM bids WHERE auction_id=? AND user_id=? ORDER BY created_at ASC, id ASC", (auction_id, user_id))
    rows = [float(r["bid_amount"]) for r in cur.fetchall()]
    conn.close()
    return rows


async def _send_auction_results_to_participants(bot_application, final_auction):
    participants, bid_count = get_auction_participant_stats(final_auction["id"])
    if final_auction["winner_user_id"]:
        result_text = (f"📢 **የጨረታ ውጤት**\n\nጨረታ #{final_auction['id']} ተጠናቋል።\n"
                       f"🏆 አሸናፊ: {get_winner_name(final_auction['winner_user_id'])}\n"
                       f"🎯 Unique Bid: `{final_auction['winning_bid']}` ብር\n"
                       f"👥 ተሳታፊዎች: `{participants}` ሰዎች\n🎟 ጠቅላላ Bids: `{bid_count}`")
    else:
        result_text = (f"📢 **የጨረታ ውጤት**\n\nጨረታ #{final_auction['id']} ተጠናቋል።\n"
                       f"❌ Unique Bid ስላልተገኘ አሸናፊ የለም።\n"
                       f"👥 ተሳታፊዎች: `{participants}` ሰዎች\n🎟 ጠቅላላ Bids: `{bid_count}`")
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id FROM bids WHERE auction_id=?", (final_auction["id"],))
    user_ids = [int(r["user_id"]) for r in cur.fetchall()]
    conn.close()
    # Telegram Bot API is rate limited. Send only to actual participants and
    # pace the messages instead of scheduling one coroutine per DB user.
    for i, uid in enumerate(user_ids):
        try:
            bids = get_user_auction_bids(final_auction["id"], uid)
            personal = result_text
            if bids:
                personal += f"\n\n🎯 **የእርስዎ Bid-ዎች:** `{', '.join(f'{x:g}' for x in bids)}` ብር\n"
                if final_auction["winner_user_id"] == uid:
                    personal += "🏆 **እርስዎ አሸንፈዋል!**\n💰 ሽልማቱ Wallet ውስጥ ገብቷል።"
                elif final_auction["winner_user_id"]:
                    personal += f"❌ አላሸነፉም።\n🎯 አሸናፊው Unique Bid: `{final_auction['winning_bid']}` ብር"
                else:
                    personal += "❌ Unique Bid ስላልተገኘ አሸናፊ የለም።"
            await bot_application.bot.send_message(chat_id=uid, text=personal, parse_mode="Markdown")
        except Exception as exc:
            logging.warning("Auction result delivery failed for user %s: %s", uid, exc)
        if i + 1 < len(user_ids):
            await asyncio.sleep(0.05)


def get_auction_participant_stats(auction_id):
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT user_id) participants, COUNT(*) bids FROM bids WHERE auction_id=?",(auction_id,))
    r=cur.fetchone(); conn.close(); return int(r['participants'] or 0), int(r['bids'] or 0)


def get_auction_simulation_count(auction_id):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(simulated_count), 0) AS cnt "
        "FROM auction_simulations WHERE auction_id = ?",
        (auction_id,)
    )
    row = cur.fetchone()
    conn.close()
    return int(row["cnt"] or 0)


def add_auction_simulations(auction_id, count, admin_id):
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO auction_simulations (
            auction_id, simulated_count, created_at, created_by
        ) VALUES (?, ?, ?, ?)
    """, (
        auction_id,
        count,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        admin_id
    ))
    conn.commit()
    conn.close()

def save_auction_post(aid, chat_id, message_id):
    conn=db(); cur=conn.cursor(); cur.execute("UPDATE auctions SET post_chat_id=?,post_message_id=? WHERE id=?",(str(chat_id),message_id,aid)); conn.commit(); conn.close()

async def admin_post_auction(update, context):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer(); return
    if not ANNOUNCEMENT_CHANNEL_ID: await q.answer("⚠️ ANNOUNCEMENT_CHANNEL_ID ያስገቡ።",show_alert=True); return
    aid=int(q.data.split(':')[1])
    conn=db(); cur=conn.cursor(); cur.execute("SELECT * FROM auctions WHERE id=?",(aid,)); auc=cur.fetchone(); conn.close()
    try:
        post=await context.bot.send_message(chat_id=ANNOUNCEMENT_CHANNEL_ID,text=build_auction_post(auc),parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎯 ተሳተፍ",callback_data=f"select_auc:{aid}")]]))
        save_auction_post(aid,ANNOUNCEMENT_CHANNEL_ID,post.message_id); await q.answer("✅ Post ተለጥፏል።",show_alert=True)
    except Exception as e: logging.error(f"Post error: {e}"); await q.answer("❌ Post አልተሳካም። Channel ID/Bot Admin ያረጋግጡ።",show_alert=True)
    await admin_manage_auctions(update,context)

async def admin_edit_auction_post(update, context):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer(); return
    aid=int(q.data.split(':')[1])
    conn=db(); cur=conn.cursor(); cur.execute("SELECT * FROM auctions WHERE id=?",(aid,)); auc=cur.fetchone(); conn.close()
    if not auc or not auc['post_chat_id'] or not auc['post_message_id']: await q.answer("❌ Post የለም።",show_alert=True); return
    try:
        await context.bot.edit_message_text(chat_id=auc['post_chat_id'],message_id=auc['post_message_id'],text=build_auction_post(auc),parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎯 ተሳተፍ",callback_data=f"select_auc:{aid}")]])); await q.answer("✅ Post ተስተካክሏል።",show_alert=True)
    except Exception as e: logging.error(f"Edit post error: {e}"); await q.answer("❌ Post ማስተካከል አልተቻለም።",show_alert=True)
    await admin_manage_auctions(update,context)

async def admin_delete_auction_post(update, context):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer(); return
    aid=int(q.data.split(':')[1])
    conn=db(); cur=conn.cursor(); cur.execute("SELECT post_chat_id,post_message_id FROM auctions WHERE id=?",(aid,)); row=cur.fetchone(); conn.close()
    if not row or not row['post_chat_id'] or not row['post_message_id']: await q.answer("❌ Post የለም።",show_alert=True); return
    try:
        await context.bot.delete_message(chat_id=row['post_chat_id'],message_id=row['post_message_id'])
        conn=db(); cur=conn.cursor(); cur.execute("UPDATE auctions SET post_chat_id=NULL,post_message_id=NULL WHERE id=?",(aid,)); conn.commit(); conn.close(); await q.answer("🗑 Post ተሰርዟል።",show_alert=True)
    except Exception as e: logging.error(f"Delete post error: {e}"); await q.answer("❌ Post መሰረዝ አልተቻለም።",show_alert=True)
    await admin_manage_auctions(update,context)

# ============================================================
# USERS
# ============================================================

def register_user(user):
    conn = db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT INTO users (user_id, first_name, username, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name=excluded.first_name, username=excluded.username, last_seen=excluded.last_seen
    """, (user.id, user.first_name or "", user.username or "", now))
    conn.commit()
    conn.close()

def get_balance(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()

    conn.close()

    return float(row["balance"]) if row else 0.0


# ============================================================
# PAYMENTS
# ============================================================

def create_payment(
    user_id,
    first_name,
    amount,
    photo_file_id
):
    conn = db()
    cur = conn.cursor()

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cur.execute("""
        INSERT INTO payments (
            user_id,
            first_name,
            amount,
            status,
            photo_file_id,
            created_at
        )
        VALUES (?, ?, ?, 'pending', ?, ?)
    """, (
        user_id,
        first_name,
        amount,
        photo_file_id,
        now
    ))

    payment_id = cur.lastrowid

    conn.commit()
    conn.close()

    return payment_id


def approve_payment(payment_id):
    conn = db(); cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        payment = cur.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not payment or payment["status"] != "pending":
            conn.rollback(); conn.close(); return payment, "already_processed"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE payments SET status='approved', approved_at=? WHERE id=? AND status='pending'", (now, payment_id))
        if cur.rowcount != 1:
            conn.rollback(); conn.close(); return payment, "already_processed"
        cur.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (payment["amount"], payment["user_id"]))
        if cur.rowcount != 1:
            conn.rollback(); conn.close(); return None, "user_not_found"
        cur.execute("INSERT INTO transactions(user_id,amount,transaction_type,payment_id,created_at) VALUES(?,?,?,?,?)", (payment["user_id"], payment["amount"], "deposit", payment_id, now))
        try:
            import platform_controls as _pcw
            _pcw.grant_welcome_after_deposit(cur, payment["user_id"], payment["amount"])
        except Exception:
            pass
        _inv = None
        try:
            _inv = release_invite_bonus(cur, payment["user_id"], payment["amount"], now)
        except Exception:
            _inv = None
        conn.commit(); conn.close()
        if _inv and BOT_TOKEN:   # best-effort heads-up to the inviter
            try:
                import requests
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                              json={"chat_id": _inv[0], "text": f"🎉 የጋበዙት ጓደኛ የመጀመሪያ ገንዘብ አስገብቷል! 🎁 +{_inv[1]:g} ብር ቦነስ (ለመጫወት)"}, timeout=5)
            except Exception:
                pass
        return payment, "approved"
    except Exception:
        conn.rollback(); conn.close(); raise


def reject_payment(payment_id):
    """Reject a pending payment exactly once; serialize against approval."""
    conn = db(); cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        payment = cur.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not payment or payment["status"] != "pending":
            conn.rollback(); conn.close()
            return payment, "already_processed"
        cur.execute("UPDATE payments SET status='rejected' WHERE id=? AND status='pending'", (payment_id,))
        if cur.rowcount != 1:
            conn.rollback(); conn.close()
            return payment, "already_processed"
        conn.commit(); conn.close()
        return payment, "rejected"
    except Exception:
        conn.rollback(); conn.close()
        raise


# ============================================================
# CLOSE AUCTION + CALCULATE WINNER
# ============================================================

def close_auction(auction_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM auctions
        WHERE id = ?
    """, (auction_id,))

    auction = cur.fetchone()

    if not auction:
        conn.close()
        return None

    if auction["status"] == "CLOSED":
        conn.close()
        return auction

    # --------------------------------------------------------
    # Total collected
    # Every bid/ticket pays the auction ticket price
    # --------------------------------------------------------

    cur.execute("""
        SELECT
            COUNT(*) AS bid_count
        FROM bids
        WHERE auction_id = ?
    """, (auction_id,))

    bid_count = cur.fetchone()["bid_count"] or 0

    total_collected = (
        float(bid_count)
        * float(auction["ticket_price"])
    )

    # --------------------------------------------------------
    # Lowest unique bid
    # --------------------------------------------------------

    cur.execute("""
        SELECT
            b.user_id,
            b.bid_amount
        FROM bids b
        WHERE b.auction_id = ?
        AND b.bid_amount IN (
            SELECT bid_amount
            FROM bids
            WHERE auction_id = ?
            GROUP BY bid_amount
            HAVING COUNT(*) = 1
        )
        ORDER BY b.bid_amount ASC
        LIMIT 1
    """, (
        auction_id,
        auction_id
    ))

    winner = cur.fetchone()

    winner_id = None
    winning_bid = None

    if winner:
        winner_id = winner["user_id"]
        winning_bid = winner["bid_amount"]

        # Award prize only once
        cur.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            auction["prize_amount"],
            winner_id
        ))

        now = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cur.execute("""
            INSERT INTO transactions (
                user_id,
                amount,
                transaction_type,
                created_at
            )
            VALUES (?, ?, 'auction_win', ?)
        """, (
            winner_id,
            auction["prize_amount"],
            now
        ))

    closed_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cur.execute("""
        UPDATE auctions
        SET status = 'CLOSED',
            winner_user_id = ?,
            winning_bid = ?,
            total_collected = ?,
            closed_at = ?
        WHERE id = ?
    """, (
        winner_id,
        winning_bid,
        total_collected,
        closed_at,
        auction_id
    ))

    conn.commit()

    # Get final auction
    cur.execute("""
        SELECT *
        FROM auctions
        WHERE id = ?
    """, (auction_id,))

    final_auction = cur.fetchone()

    conn.close()

    return final_auction


def get_winner_name(user_id):
    if not user_id:
        return "አሸናፊ የለም"

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT first_name, username
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return str(user_id)

    if row["username"]:
        return (
            f"{row['first_name']} "
            f"(@{row['username']})"
        )

    return row["first_name"] or str(user_id)


# ============================================================
# SEND AUCTION RESULT
# ============================================================

def send_auction_result(bot_application, auction):
    auction_id = auction["id"]

    winner_id = auction["winner_user_id"]
    winning_bid = auction["winning_bid"]
    prize = float(auction["prize_amount"])
    collected = float(
        auction["total_collected"] or 0
    )

    winner_name = get_winner_name(
        winner_id
    )

    # --------------------------------------------------------
    # Admin result
    # --------------------------------------------------------

    if winner_id:
        winner_text = (
            f"🏆 አሸናፊ: {winner_name}\n"
            f"🆔 User ID: `{winner_id}`\n"
            f"🎯 Unique Bid: `{winning_bid}` ብር"
        )
    else:
        winner_text = (
            "🏆 አሸናፊ: የለም\n"
            "🎯 Unique Bid: የለም"
        )

    profit = collected - prize

    admin_text = (
        f"🏁 **የጨረታ ውጤት**\n\n"
        f"📌 Auction ID: `#{auction_id}`\n"
        f"{winner_text}\n"
        f"💰 ሽልማት: `{prize:.2f}` ብር\n"
        f"💵 ከተሳታፊዎች የተሰበሰበ: "
        f"`{collected:.2f}` ብር\n"
        f"📊 ልዩነት: `{profit:.2f}` ብር\n"
        f"📅 የተዘጋበት: `{auction['closed_at']}`"
    )

    try:
        _schedule_bot_coro(
            bot_application,
            bot_application.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_text,
                parse_mode="Markdown"
            ),
        )
    except Exception as e:
        logging.error(
            f"Admin result error: {e}"
        )

    # --------------------------------------------------------
    # Winner notification
    # --------------------------------------------------------

    if winner_id:

        try:
            _schedule_bot_coro(
            bot_application,
            bot_application.bot.send_message(
                    chat_id=winner_id,
                    text=(
                        f"🎉 **እንኳን ደስ አለዎት!**\n\n"
                        f"🏆 ጨረታ #{auction_id}\n"
                        f"🎯 Unique Bid: "
                        f"`{winning_bid}` ብር\n"
                        f"💰 ሽልማት: "
                        f"`{prize:.2f}` ብር\n\n"
                        f"ሽልማቱ ወደ Walletዎ ገብቷል።"
                    ),
                    parse_mode="Markdown"
                ),
                )
        except Exception as e:
            logging.error(
                f"Winner notification error: {e}"
            )


# ============================================================
# AUTOMATIC AUCTION CHECKER
# ============================================================

def check_and_close_auctions(bot_application):

    conn = db()
    cur = conn.cursor()

    now = datetime.now()

    # --------------------------------------------------------
    # One-hour warning
    # --------------------------------------------------------

    cur.execute("""
        SELECT *
        FROM auctions
        WHERE status = 'ACTIVE'
        AND warning_sent = 0
    """)

    active_auctions = cur.fetchall()

    for auc in active_auctions:

        try:
            ends_dt = datetime.strptime(
                auc["ends_at"],
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            continue

        remaining_seconds = (
            ends_dt - now
        ).total_seconds()

        if 0 < remaining_seconds <= 3600:

            cur.execute("""
                UPDATE auctions
                SET warning_sent = 1
                WHERE id = ?
            """, (auc["id"],))

            conn.commit()

            cur.execute("""
                SELECT DISTINCT user_id
                FROM bids
                WHERE auction_id = ?
            """, (auc["id"],))

            bidders = cur.fetchall()

            for b in bidders:

                try:
                    _schedule_bot_coro(
                                bot_application,
                                bot_application.bot.send_message(
                            chat_id=b["user_id"],
                            text=(
                                f"⚠️ **ማስታወሻ**\n\n"
                                f"ጨረታ #{auc['id']} "
                                f"በ1 ሰዓት ውስጥ ይጠናቀቃል።\n"
                                f"💰 ሽልማት: "
                                f"`{auc['prize_amount']}` ብር"
                            ),
                            parse_mode="Markdown"
                        ),
                                )
                except Exception as e:
                    logging.error(
                        f"Warning error: {e}"
                    )

    # --------------------------------------------------------
    # Expired auctions
    # --------------------------------------------------------

    now_str = now.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cur.execute("""
        SELECT *
        FROM auctions
        WHERE status = 'ACTIVE'
        AND ends_at <= ?
    """, (now_str,))

    expired = cur.fetchall()

    conn.close()

    for auc in expired:

        try:

            final_auction = close_auction(
                auc["id"]
            )

            if final_auction:
                send_auction_result(
                    bot_application,
                    final_auction
                )

                # Notify only real auction participants, paced below Telegram
                # rate limits. Never broadcast a private auction result to every user.
                try:
                    _schedule_bot_coro(
                        bot_application,
                        _send_auction_results_to_participants(bot_application, final_auction)
                    )
                except Exception as e:
                    logging.error(f"Auction result scheduling error: {e}")

            # ------------------------------------------------
            # Create automatic next auction only if there is
            # no active auction
            # ------------------------------------------------

            conn3 = db()
            cur3 = conn3.cursor()

            cur3.execute("""
                SELECT COUNT(*) AS cnt
                FROM auctions
                WHERE status = 'ACTIVE'
            """)

            active_count = cur3.fetchone()["cnt"]

            # No automatic next auction. Admin creates the next one manually.

            conn3.close()

        except Exception as e:

            logging.error(
                f"Auction closing error: {e}"
            )


# ============================================================
# HOURLY ADMIN REPORT
# ============================================================

def send_hourly_admin_report(bot_application):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM users"
    )

    total_users = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT *
        FROM auctions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """)

    active_auc = cur.fetchone()

    active_users = 0
    total_ticket_money = 0.0

    if active_auc:

        cur.execute("""
            SELECT
                COUNT(DISTINCT user_id) AS users_cnt,
                COUNT(*) AS bid_count
            FROM bids
            WHERE auction_id = ?
        """, (active_auc["id"],))

        info = cur.fetchone()

        active_users = (
            info["users_cnt"] or 0
        )

        bid_count = info["bid_count"] or 0

        total_ticket_money = (
            bid_count
            * float(active_auc["ticket_price"])
        )

    cur.execute(
        "SELECT SUM(balance) AS total_bal FROM users"
    )

    total_balance = (
        cur.fetchone()["total_bal"] or 0
    )

    conn.close()

    text = (
        f"📊 **የሰዓት ሪፖርት**\n\n"
        f"👥 Users: `{total_users}`\n"
        f"🏃 Active participants: `{active_users}`\n"
        f"🎟 የተሰበሰበ Ticket: "
        f"`{total_ticket_money:.2f}` ብር\n"
        f"💳 Total Wallet: "
        f"`{total_balance:.2f}` ብር\n"
        f"🎯 Active Auction: "
        f"`#{active_auc['id'] if active_auc else 'N/A'}`"
    )

    try:
        _schedule_bot_coro(
            bot_application,
            bot_application.bot.send_message(
                chat_id=ADMIN_ID,
                text=text,
                parse_mode="Markdown"
            ),
        )
    except Exception as e:
        logging.error(
            f"Hourly report error: {e}"
        )



# ============================================================
# BACKUP
# ============================================================
# Database backups are handled by the dedicated backup_db.py container/service.
# Do not schedule DB-file uploads from the bot process.


# ============================================================
# TELEGRAM MINI APP BACKEND
# ============================================================

def _mini_json(data, status=200):
    return web.json_response(data, status=status, dumps=lambda x: json.dumps(x, ensure_ascii=False))


def validate_mini_app_init_data(init_data: str):
    """Validate Telegram.WebApp.initData before trusting user identity."""
    if not init_data:
        raise web.HTTPUnauthorized(text="Missing Telegram initData")
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        raise web.HTTPUnauthorized(text="Invalid initData")

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise web.HTTPUnauthorized(text="Missing initData hash")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(parsed.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise web.HTTPUnauthorized(text="Invalid initData signature")

    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        raise web.HTTPUnauthorized(text="Invalid auth_date")
    if auth_date <= 0 or time.time() - auth_date > MINI_APP_MAX_AUTH_AGE:
        raise web.HTTPUnauthorized(text="Expired initData")

    try:
        tg_user = json.loads(parsed["user"])
    except Exception:
        raise web.HTTPUnauthorized(text="Invalid Telegram user data")
    if not tg_user.get("id"):
        raise web.HTTPUnauthorized(text="Telegram user id missing")
    return tg_user


# Only these frontend files are public (source/.env/DB must never be reachable over HTTP).
MINI_PUBLIC_ASSETS = {
    "/app.js": "application/javascript; charset=utf-8",
    "/kdm_fix.js": "application/javascript; charset=utf-8",
    "/kdm_betpro.css": "text/css; charset=utf-8",
    "/kdm_betpro.js": "application/javascript; charset=utf-8",
    "/kdm_neon.css": "text/css; charset=utf-8",
    "/habtam_theme.css": "text/css; charset=utf-8",
    "/megaplay_theme.css": "text/css; charset=utf-8",
    "/kdm_neon.js": "application/javascript; charset=utf-8",
    "/rewards.js": "application/javascript; charset=utf-8",
    "/kdm_skin_skymasters.js": "application/javascript; charset=utf-8",
    "/kdm_skin_highflyer.js": "application/javascript; charset=utf-8",
    "/kdm_skin_crash.js": "application/javascript; charset=utf-8",
    "/kdm_skin_aviator.js": "application/javascript; charset=utf-8",
    "/kdm_skin_jet.js": "application/javascript; charset=utf-8",
    "/kdm_covers.js": "application/javascript; charset=utf-8",
    "/kdm_fix.css": "text/css; charset=utf-8",
    "/premium.js": "application/javascript; charset=utf-8",
    "/engagement.js": "application/javascript; charset=utf-8",
    "/game_stage.js": "application/javascript; charset=utf-8",
    "/style.css": "text/css; charset=utf-8",
    "/premium.css": "text/css; charset=utf-8",
    "/engagement.css": "text/css; charset=utf-8",
    "/visual_refresh.css": "text/css; charset=utf-8",
    "/onboarding.js": "application/javascript; charset=utf-8",
    "/onboarding.css": "text/css; charset=utf-8",
    "/road_game.js": "application/javascript; charset=utf-8",
    "/road_game.css": "text/css; charset=utf-8",
    "/new_games.js": "application/javascript; charset=utf-8",
    "/new_games.css": "text/css; charset=utf-8",
    "/kdm_design.css": "text/css; charset=utf-8",
    "/kdm_ui.js": "application/javascript; charset=utf-8",
    "/kdm_art.js": "application/javascript; charset=utf-8",
    "/game_catalog.js": "application/javascript; charset=utf-8",
    "/game_help.js": "application/javascript; charset=utf-8",
    "/game_fx.js": "application/javascript; charset=utf-8",
    "/haptics.js": "application/javascript; charset=utf-8",
    "/leaderboard.js": "application/javascript; charset=utf-8",
    "/leaderboard.css": "text/css; charset=utf-8",
    "/rtp_badge.js": "application/javascript; charset=utf-8",
    "/cards_ui.js": "application/javascript; charset=utf-8",
    "/coin_dice_ui.js": "application/javascript; charset=utf-8",
    "/kdm_ingame.css": "text/css; charset=utf-8",
    "/kdm_ingame.js": "application/javascript; charset=utf-8",
    "/kdm_aviator.js": "application/javascript; charset=utf-8",
    "/kdm_highflyer_live.js": "application/javascript; charset=utf-8",
    "/kdm_skymasters_live.js": "application/javascript; charset=utf-8",
    "/kdm_mines.js": "application/javascript; charset=utf-8",
    "/kdm_plinko.js": "application/javascript; charset=utf-8",
    "/kdm_keno.js": "application/javascript; charset=utf-8",
    "/kdm_triplegold.js": "application/javascript; charset=utf-8",
    "/kdm_flame.js": "application/javascript; charset=utf-8",
    "/kdm_aztec.js": "application/javascript; charset=utf-8",
    "/kdm_buffalo.js": "application/javascript; charset=utf-8",
    "/kdm_rabbit.js": "application/javascript; charset=utf-8",
    "/kdm_penalty.js": "application/javascript; charset=utf-8",
    "/kdm_chicken.js": "application/javascript; charset=utf-8",
    "/kdm_blackjack.js": "application/javascript; charset=utf-8",
    "/kdm_baccarat.js": "application/javascript; charset=utf-8",
    "/kdm_roulette.js": "application/javascript; charset=utf-8",
    "/kdm_wheel.js": "application/javascript; charset=utf-8",
    "/kdm_fish.js": "application/javascript; charset=utf-8",
    "/kdm_bingo.js": "application/javascript; charset=utf-8",
    "/kdm_fastkeno.js": "application/javascript; charset=utf-8",
    "/kdm_superkeno_live.js": "application/javascript; charset=utf-8",
    "/kdm_slot.js": "application/javascript; charset=utf-8",
    "/kdm_number.js": "application/javascript; charset=utf-8",
    "/kdm_number.css": "text/css; charset=utf-8",
    "/kdm_keno_live.js": "application/javascript; charset=utf-8",
    "/kdm_live_crash.js": "application/javascript; charset=utf-8",
    "/kdm_roast.js": "application/javascript; charset=utf-8",
    "/kdm_chicken2.js": "application/javascript; charset=utf-8",
    "/kdm_boom.js": "application/javascript; charset=utf-8",
    "/kdm_jet.js": "application/javascript; charset=utf-8",
    "/kdm_rocket.js": "application/javascript; charset=utf-8",
    "/kdm_sports.js": "application/javascript; charset=utf-8",
    "/kdm_slot.css": "text/css; charset=utf-8",
    "/kdm_bingo.css": "text/css; charset=utf-8",
    "/kdm_fastkeno.css": "text/css; charset=utf-8",
    "/kdm_boom.css": "text/css; charset=utf-8",
    "/kdm_jet.css": "text/css; charset=utf-8",
    "/kdm_rocket.css": "text/css; charset=utf-8",
    "/kdm_triplegold.css": "text/css; charset=utf-8",
    "/kdm_flame.css": "text/css; charset=utf-8",
    "/kdm_aztec.css": "text/css; charset=utf-8",
}


@web.middleware
async def mini_auth_middleware(request, handler):
    if request.path in ("/health", "/", "/index.html") or request.path in MINI_PUBLIC_ASSETS:
        return await handler(request)
    if request.path.startswith("/assets/"):
        return await handler(request)
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    tg_user = validate_mini_app_init_data(init_data)
    request["tg_user"] = tg_user
    register_mini_user(tg_user)
    return await handler(request)


def register_mini_user(tg_user):
    conn = db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT INTO users (user_id, first_name, username, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name=excluded.first_name,
            username=excluded.username,
            last_seen=excluded.last_seen
    """, (
        int(tg_user["id"]),
        tg_user.get("first_name", ""),
        tg_user.get("username", ""),
        now,
    ))
    conn.commit()
    conn.close()


def mini_user_id(request):
    return int(request["tg_user"]["id"])


def mini_user_is_staff(request):
    return is_staff(mini_user_id(request))


def mini_auction_dict(row):
    participants, bids = get_auction_participant_stats(row["id"])
    simulated = get_auction_simulation_count(row["id"])
    return {
        "id": row["id"],
        "prize": float(row["prize_amount"]),
        "ticket_price": float(row["ticket_price"]),
        "ends_at": row["ends_at"],
        "participants": participants + simulated,
        "real_participants": participants,
        "bids": bids,
        "status": row["status"],
        "tier_level": int(row["tier_level"] or 0),
    }


async def mini_index(request):
    path = os.path.join(MINI_APP_DIR, "index.html")
    if not os.path.exists(path):
        raise web.HTTPNotFound(text="miniapp/index.html not found")
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


async def mini_health(request):
    return _mini_json({"ok": True, "service": "Habtam Bet"})


async def mini_me(request):
    uid = mini_user_id(request)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT user_id, first_name, username, balance, manager FROM users WHERE user_id=?", (uid,))
    user = cur.fetchone()
    conn.close()
    return _mini_json({
        "user": dict(user) if user else {"user_id": uid},
        "is_admin": is_admin(uid),
        "is_staff": is_staff(uid),
    })


async def mini_auctions(request):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM auctions WHERE status='ACTIVE' ORDER BY id DESC")
    rows = cur.fetchall(); conn.close()
    return _mini_json({"auctions": [mini_auction_dict(r) for r in rows]})


async def mini_bid(request):
    try:
        payload = await request.json()
        auction_id = int(payload.get("auction_id"))
        bid_amount = float(payload.get("bid_amount"))
    except Exception:
        return _mini_json({"ok": False, "error": "Invalid bid data"}, 400)

    uid = mini_user_id(request)
    if bid_amount < 1 or round(bid_amount, 2) != bid_amount:
        return _mini_json({"ok": False, "error": "Bid ቢያንስ 1 ብር እና እስከ 2 decimal ብቻ ነው።"}, 400)

    conn = db(); cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM auctions WHERE id=? AND status='ACTIVE'", (auction_id,))
        auction = cur.fetchone()
        if not auction:
            return _mini_json({"ok": False, "error": "ጨረታው ተዘግቷል።"}, 404)
        ticket = float(auction["ticket_price"])
        cur.execute("BEGIN")
        cur.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (ticket, uid, ticket))
        if cur.rowcount != 1:
            conn.rollback()
            return _mini_json({"ok": False, "error": "Wallet በቂ አይደለም።", "ticket_price": ticket}, 400)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO bids(auction_id,user_id,bid_amount,created_at) VALUES(?,?,?,?)", (auction_id, uid, bid_amount, now))
        cur.execute("INSERT INTO transactions(user_id,amount,transaction_type,created_at) VALUES(?,?,?,?)", (uid, -ticket, "auction_ticket", now))
        conn.commit()
        cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        balance = float(cur.fetchone()["balance"])
    except Exception as e:
        conn.rollback()
        logging.exception("Mini App bid error")
        return _mini_json({"ok": False, "error": "Bid ሲመዘገብ ችግኝ ተፈጥሯል።"}, 500)
    finally:
        conn.close()

    # Keep the existing public Telegram auction post updated.
    try:
        conn2 = db(); cur2 = conn2.cursor(); cur2.execute("SELECT * FROM auctions WHERE id=?", (auction_id,)); updated = cur2.fetchone(); conn2.close()
        if updated and updated["post_chat_id"] and updated["post_message_id"]:
            await request.app["ptb_app"].bot.edit_message_text(
                chat_id=updated["post_chat_id"],
                message_id=updated["post_message_id"],
                text=build_auction_post(updated),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎯 ተሳተፍ", callback_data=f"select_auc:{auction_id}")]]),
            )
    except Exception as e:
        logging.warning(f"Mini bid auction post refresh failed: {e}")

    return _mini_json({"ok": True, "balance": balance, "message": "Bid ተመዝግቧል።"})


async def mini_history(request):
    uid = mini_user_id(request)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT amount, transaction_type, created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,))
    rows = [dict(r) for r in cur.fetchall()]; conn.close()
    return _mini_json({"history": rows})


async def mini_deposit(request):
    try:
        reader = await request.multipart()
        amount = None
        photo_bytes = None
        filename = "payment.jpg"
        async for part in reader:
            if part.name == "amount":
                amount = float((await part.text()).strip())
            elif part.name == "screenshot":
                filename = part.filename or filename
                photo_bytes = await part.read()
        if amount is None or amount <= 0 or not photo_bytes:
            return _mini_json({"ok": False, "error": "Amount እና Screenshot ያስገቡ።"}, 400)
    except Exception:
        return _mini_json({"ok": False, "error": "Invalid deposit request"}, 400)

    uid = mini_user_id(request)
    tg_user = request["tg_user"]
    app = request.app["ptb_app"]
    conn = db(); cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("INSERT INTO payments(user_id,first_name,amount,status,photo_file_id,created_at) VALUES(?,?,?,'pending',?,?)", (uid, tg_user.get("first_name", ""), amount, "", now))
    payment_id = cur.lastrowid
    conn.commit(); conn.close()

    try:
        sent = await app.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=BytesIO(photo_bytes),
            caption=(f"📥 **Payment Request — Mini App**\n\n👤 {tg_user.get('first_name','N/A')}\n🆔 `{uid}`\n💰 `{amount:.2f}` ብር\n🆔 Payment: `{payment_id}`"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{payment_id}")],
                [InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{payment_id}")],
            ]),
        )
        file_id = sent.photo[-1].file_id if sent.photo else ""
        conn = db(); cur = conn.cursor(); cur.execute("UPDATE payments SET photo_file_id=? WHERE id=?", (file_id, payment_id)); conn.commit(); conn.close()
    except Exception as e:
        logging.exception("Mini App deposit admin delivery error")
        conn = db(); cur = conn.cursor(); cur.execute("DELETE FROM payments WHERE id=?", (payment_id,)); conn.commit(); conn.close()
        return _mini_json({"ok": False, "error": "Screenshot ወደ Admin መላክ አልተቻለም።"}, 500)

    return _mini_json({"ok": True, "payment_id": payment_id, "message": "Screenshot ተልኳል። Admin ካረጋገጠው Wallet ይጨምራል።"})


async def mini_withdraw(request):
    try:
        payload = await request.json()
        amount = float(payload.get("amount"))
        acc_name = str(payload.get("acc_name", "")).strip()
        acc_number = str(payload.get("acc_number", "")).strip()
    except Exception:
        return _mini_json({"ok": False, "error": "Invalid withdrawal data"}, 400)
    if amount <= 0 or not acc_name or not acc_number:
        return _mini_json({"ok": False, "error": "Amount, Account Name እና Account Number ያስገቡ።"}, 400)

    uid = mini_user_id(request)
    conn = db(); cur = conn.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        _b, _lk, _can = _withdrawable(cur, uid)
        if _lk > 0 and amount > _can + 0.001:
            conn.rollback(); return _mini_json({"ok": False, "error": f"🎁 Bonus ({_lk:.2f} ብር) ለመጫወት ብቻ ነው -- ማውጣት የሚችሉት {_can:.2f} ብር ነው"}, 400)
        cur.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, uid, amount))
        if cur.rowcount != 1:
            conn.rollback(); return _mini_json({"ok": False, "error": "Wallet በቂ አይደለም።"}, 400)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO withdrawals(user_id,amount,acc_name,acc_number,status,created_at) VALUES(?,?,?,?,'pending',?)", (uid, amount, acc_name, acc_number, now))
        withdrawal_id = cur.lastrowid
        cur.execute("INSERT INTO transactions(user_id,amount,transaction_type,created_at) VALUES(?,?,?,?)", (uid, -amount, "withdraw", now))
        conn.commit()
    except Exception:
        conn.rollback(); logging.exception("Mini withdrawal error")
        return _mini_json({"ok": False, "error": "Withdrawal ሲመዘገብ ችግኝ ተፈጥሯል።"}, 500)
    finally:
        conn.close()

    await request.app["ptb_app"].bot.send_message(
        chat_id=ADMIN_ID,
        text=(f"💸 <b>Withdrawal Request — Mini App</b>\n\n🆔 User: <code>{uid}</code>\n💰 <code>{amount:.2f}</code> ብር\n👤 Account: <code>{html.escape(acc_name)}</code>\n📱/🏦 Number: <code>{html.escape(acc_number)}</code>\n🆔 Withdrawal: <code>{withdrawal_id}</code>"),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ APPROVE", callback_data=f"w_approve:{withdrawal_id}")],
            [InlineKeyboardButton("❌ REJECT", callback_data=f"w_reject:{withdrawal_id}")],
        ]),
    )
    return _mini_json({"ok": True, "withdrawal_id": withdrawal_id, "message": "Withdrawal Request ተልኳል።"})


async def mini_support(request):
    try:
        reader = await request.multipart()
        text = ""
        photo_bytes = None
        filename = "support.jpg"
        async for part in reader:
            if part.name == "text":
                text = (await part.text()).strip()
            elif part.name == "photo":
                filename = part.filename or filename
                photo_bytes = await part.read()
    except Exception:
        return _mini_json({"ok": False, "error": "Invalid support request"}, 400)

    if not text and not photo_bytes:
        return _mini_json({"ok": False, "error": "Message ወይም Photo ያስገቡ።"}, 400)

    uid = mini_user_id(request); tg_user = request["tg_user"]; app = request.app["ptb_app"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"📩 **Player Support — Mini App**\n\n👤 {tg_user.get('first_name','N/A')}\n🆔 `{uid}`\n🔗 @{tg_user.get('username','N/A')}\n🕒 `{now}`\n\n"
    try:
        if photo_bytes:
            msg = await app.bot.send_photo(chat_id=ADMIN_ID, photo=BytesIO(photo_bytes), caption=prefix + (text or "📸 Photo ተልኳል።"), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Reply to Player", callback_data=f"support_reply:{uid}")]]))
            content_type = "photo"
        else:
            msg = await app.bot.send_message(chat_id=ADMIN_ID, text=prefix + text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Reply to Player", callback_data=f"support_reply:{uid}")]]))
            content_type = "text"
        conn=db(); cur=conn.cursor(); cur.execute("INSERT OR REPLACE INTO support_messages(admin_message_id,user_id,created_at,direction,content_type) VALUES(?,?,?,?,?)", (msg.message_id, uid, now, "user_to_admin", content_type)); conn.commit(); conn.close()
    except Exception:
        logging.exception("Mini support delivery error")
        return _mini_json({"ok": False, "error": "Support መልዕክቱን መላክ አልተቻለም።"}, 500)
    return _mini_json({"ok": True, "message": "መልዕክትዎ Admin ጋር ደርሷል።"})


async def mini_aviator_state(request):
    uid = mini_user_id(request)
    rid = AVIATOR_STATE.get("round_id")
    b = AVIATOR_STATE.get("player_bets", {}).get(uid)
    return _mini_json({
        "round_id": rid,
        "phase": AVIATOR_STATE.get("phase"),
        "multiplier": aviator_current_multiplier() if AVIATOR_STATE.get("phase") == "flying" else (float(AVIATOR_STATE.get("crash", 1.0)) if AVIATOR_STATE.get("phase") == "crashed" else 1.0),
        "crash": float(AVIATOR_STATE.get("crash", 1.0)),
        "players": int(AVIATOR_STATE.get("total_players", 0)),
        "total_bets": float(AVIATOR_STATE.get("total_bets", 0)),
        "total_payout": float(AVIATOR_STATE.get("total_payout", 0)),
        "cashed_out": int(AVIATOR_STATE.get("cashed_out_count", 0)),
        "my_bet": b,
        "balance": get_balance(uid),
    })


async def mini_aviator_bet(request):
    try:
        amount = float((await request.json()).get("amount"))
    except Exception:
        return _mini_json({"ok": False, "error": "Invalid amount"}, 400)
    uid = mini_user_id(request); rid = AVIATOR_STATE.get("round_id")
    if AVIATOR_STATE.get("phase") != "betting" or not rid:
        return _mini_json({"ok": False, "error": "አሁን Bet መጫን አይቻልም።"}, 400)
    if amount <= 0:
        return _mini_json({"ok": False, "error": "Bet ከ0 በላይ ይሁን።"}, 400)

    conn=db(); cur=conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,)); row=cur.fetchone(); balance=float(row["balance"] or 0) if row else 0
        if balance < amount:
            conn.rollback(); return _mini_json({"ok": False, "error": "Wallet በቂ አይደለም።", "balance": balance}, 400)
        cur.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, uid, amount))
        if cur.rowcount != 1:
            conn.rollback(); return _mini_json({"ok": False, "error": "Wallet በቂ አይደለም።"}, 400)
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO aviator_bets(round_id,user_id,bet_amount,status,created_at) VALUES(?,?,?,'placed',?)", (rid,uid,amount,now))
        bet_id=cur.lastrowid
        cur.execute("INSERT INTO transactions(user_id,amount,transaction_type,created_at) VALUES(?,?,?,?)", (uid,-amount,"aviator_bet",now))
        conn.commit()
    except Exception:
        conn.rollback(); logging.exception("Mini Aviator bet error")
        return _mini_json({"ok": False, "error": "Aviator Bet ሲመዘገብ ችግኝ ተፈጥሯል።"}, 500)
    finally: conn.close()

    AVIATOR_STATE.setdefault("player_bets", {})[uid] = {"bet_id": int(bet_id), "bet_amount": float(amount), "status": "placed", "cashout_multiplier": None, "payout": 0.0, "name": request["tg_user"].get("first_name") or str(uid)}
    AVIATOR_STATE["total_players"] = len(AVIATOR_STATE["player_bets"])
    AVIATOR_STATE["total_bets"] = float(AVIATOR_STATE.get("total_bets",0)) + amount
    return _mini_json({"ok": True, "bet_id": int(bet_id), "balance": get_balance(uid)})


async def mini_aviator_cashout(request):
    try:
        payload = await request.json(); rid=int(payload.get("round_id")); bet_id=int(payload.get("bet_id"))
    except Exception:
        return _mini_json({"ok": False, "error": "Invalid cashout data"}, 400)
    uid=mini_user_id(request)
    if AVIATOR_STATE.get("round_id") != rid or AVIATOR_STATE.get("phase") != "flying":
        return _mini_json({"ok": False, "error": "ይህ round አልነቃም።"}, 400)
    multiplier=aviator_current_multiplier()
    if multiplier >= float(AVIATOR_STATE.get("crash", 1.0)):
        return _mini_json({"ok": False, "error": "💥 Crash ሆኗል።"}, 400)
    conn=db(); cur=conn.cursor()
    try:
        cur.execute("SELECT * FROM aviator_bets WHERE bet_id=? AND round_id=? AND user_id=? AND status='placed'", (bet_id,rid,uid)); bet=cur.fetchone()
        if not bet:
            return _mini_json({"ok": False, "error": "ንቁ Bet የለዎትም።"}, 404)
        payout=round(float(bet["bet_amount"])*multiplier,2); now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("UPDATE aviator_bets SET status='cashed_out',cashout_multiplier=?,payout=? WHERE bet_id=? AND status='placed'", (multiplier,payout,bet_id))
        if cur.rowcount != 1:
            conn.rollback(); return _mini_json({"ok": False, "error": "Bet ቀድሞ ተሰርቷል።"}, 409)
        cur.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (payout,uid))
        cur.execute("INSERT INTO transactions(user_id,amount,transaction_type,created_at) VALUES(?,?,?,?)", (uid,payout,"aviator_win",now))
        conn.commit()
    except Exception:
        conn.rollback(); logging.exception("Mini Aviator cashout error")
        return _mini_json({"ok": False, "error": "Cash Out አልተሳካም።"}, 500)
    finally: conn.close()
    cached=AVIATOR_STATE.setdefault("player_bets",{}).get(uid)
    if cached:
        cached.update({"status":"cashed_out","cashout_multiplier":float(multiplier),"payout":float(payout)})
    AVIATOR_STATE["total_payout"]=float(AVIATOR_STATE.get("total_payout",0))+payout
    AVIATOR_STATE["cashed_out_count"]=int(AVIATOR_STATE.get("cashed_out_count",0))+1
    return _mini_json({"ok": True, "payout": payout, "multiplier": multiplier, "balance": get_balance(uid)})


async def mini_admin_summary(request):
    if not mini_user_is_staff(request):
        raise web.HTTPForbidden(text="Staff only")
    conn=db(); cur=conn.cursor()
    cur.execute("SELECT COUNT(*) cnt, COALESCE(SUM(balance),0) wallet FROM users"); users=cur.fetchone()
    cur.execute("SELECT COUNT(*) cnt FROM auctions WHERE status='ACTIVE'"); active=cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) cnt FROM aviator_rounds WHERE status='crashed'"); rounds=cur.fetchone()["cnt"]
    cur.execute("SELECT COALESCE(SUM(bet_amount),0) bets, COALESCE(SUM(payout),0) payouts FROM aviator_bets"); av=cur.fetchone()
    conn.close()
    return _mini_json({"users":users["cnt"],"wallet":float(users["wallet"]),"active_auctions":active,"aviator_rounds":rounds,"aviator_bets":float(av["bets"]),"aviator_payouts":float(av["payouts"]),"settings":EXTRA_GAME_SETTINGS})


async def mini_app_server_start(application):
    if not os.path.isdir(MINI_APP_DIR):
        logging.warning("Mini App directory missing: %s", MINI_APP_DIR)
        return
    app = web.Application(middlewares=[mini_auth_middleware])
    app["ptb_app"] = application
    app.router.add_get("/", mini_index)
    app.router.add_get("/index.html", mini_index)
    app.router.add_get("/health", mini_health)

    async def _mini_root_asset(request):
        content_type = MINI_PUBLIC_ASSETS.get(request.path)
        if not content_type:
            raise web.HTTPNotFound()
        filename = request.path.lstrip("/")
        path = os.path.join(MINI_APP_DIR, filename)
        if not os.path.isfile(path):
            raise web.HTTPNotFound(text=f"Mini App asset not found: {filename}")
        return web.FileResponse(path, headers={"Content-Type": content_type, "Cache-Control": "no-cache"})

    # Never expose MINI_APP_DIR as a directory. Only the known frontend files in
    # MINI_PUBLIC_ASSETS are public.
    for _asset_path in MINI_PUBLIC_ASSETS:
        app.router.add_get(_asset_path, _mini_root_asset)
    # No legacy /api/* handlers here. webapp_api.py is the single HTTP API owner.

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    application.bot_data["mini_runner"] = runner
    logging.info("Telegram Mini App server listening on %s:%s", WEBAPP_HOST, WEBAPP_PORT)


async def mini_app_server_stop(application):
    runner = application.bot_data.pop("mini_runner", None)
    if runner:
        await runner.cleanup()


# ============================================================
# MAIN MENU — TELEGRAM MINI APP
# ============================================================

OPEN_APP_TEXT = "Habtam Bet 🥳"


async def open_app_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bottom keyboard button pressed -> reply with an inline Web App button (works with initData)."""
    if not WEBAPP_URL.startswith("https://"):
        return
    await update.message.reply_text(
        "👇 ለመጫወት ይጫኑ",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Habtam Bet 🥳", web_app=WebAppInfo(url=WEBAPP_URL))]]
        ),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Deep-link payload: /start ref_<inviter id>  (invite link)  or  /start g_<game>  (post / channel link)
    start_arg = (context.args[0] if getattr(context, "args", None) else "").strip()
    _conn = db(); _cur = _conn.cursor()
    _cur.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,)); _is_new = _cur.fetchone() is None
    _conn.close()
    register_user(user)
    if _is_new and start_arg.startswith("ref_") and start_arg[4:].isdigit():
        inviter = int(start_arg[4:])
        if inviter != user.id:
            try:
                _conn = db(); _cur = _conn.cursor()
                if record_invite(_cur, inviter, user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
                    _conn.commit()
                    try:   # tell the inviter
                        _c2 = db(); _k2 = _c2.cursor(); _def = invite_requires_deposit(_k2); _c2.close()
                        await context.bot.send_message(inviter, (f"🎉 አዲስ ጓደኛ ተቀላቅሏል! 🎁 +{INVITE_BONUS_ETB:g} ብር ቦነስ ጓደኛዎ የመጀመሪያ ገንዘብ ሲያስገባ ይከፈላል።" if _def
                                                                 else f"🎉 አዲስ ጓደኛ ተቀላቅሏል! 🎁 +{INVITE_BONUS_ETB:g} ብር ቦነስ (ለመጫወት)"))
                    except Exception:
                        pass
                _conn.close()
            except Exception as e:
                logging.warning(f"referral save failed: {e}")
    start_game = start_arg[2:] if start_arg.startswith("g_") and PROMO_KEY_RE.match(start_arg[2:] or "-") else ""

    # Reset temporary conversation state without discarding a valid Admin PIN
    # session. /start and the "main" callback both call this handler; clearing
    # all user_data here previously erased admin_pin_verified_until and forced
    # staff back to the ordinary user flow after PIN verification.
    pin_verified_until = float(
        context.user_data.get("admin_pin_verified_until", 0) or 0
    )
    context.user_data.clear()
    if pin_verified_until > time.time():
        context.user_data["admin_pin_verified_until"] = pin_verified_until

    # Persistent bottom button is a PLAIN text button: a keyboard-button Web App launch
    # carries no initData ("Missing initData"). Tapping it makes the bot reply with an
    # inline Web App button (see open_app_button), which does carry initData.
    markup = ReplyKeyboardMarkup(
        [[KeyboardButton(OPEN_APP_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )
    inline_open = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Habtam Bet 🥳", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )

    balance = get_balance(user.id)
    text = (
        f"ሰላም {user.first_name or 'ተጠቃሚ'}! 👋\n\n"
        "Habtam Bet 🥳 ወደ Telegram Mini App ተቀይሯል።\n\n"
        f"💰 Wallet: {balance:.2f} ብር\n\n"
        "ከታች ያለውን «Habtam Bet 🥳» ይጫኑ።"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)
        if WEBAPP_URL.startswith("https://"):
            await update.message.reply_text("👇 ለመጫወት ይጫኑ", reply_markup=inline_open)
        if start_arg:
            # Invite / post link: one tap opens the Mini App (on the chosen game when the link names one).
            game_url = WEBAPP_URL + ((("&" if "?" in WEBAPP_URL else "?") + "game=" + start_game) if start_game and start_game != "lobby" else "")
            if WEBAPP_URL.startswith("https://"):
                await update.message.reply_text(
                    "👇 ለመጫወት ይጫኑ",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Habtam Bet 🥳", web_app=WebAppInfo(url=game_url))]]),
                )

# ============================================================
# PLAYER <-> ADMIN SUPPORT
# ============================================================

async def support_start(update, context):
    q = update.callback_query
    await q.answer()
    if is_staff(q.from_user.id):
        return
    context.user_data.clear()
    context.user_data["waiting_support_message"] = True
    await q.edit_message_text(
        "📩 **Admin / Support**\n\n"
        "ለAdmin መልዕክት ይላኩ።\n"
        "✍️ Text ወይም 📸 Photo መላክ ይችላሉ።\n\n"
        "❌ ለመሰረዝ /cancel ይጻፉ።",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ዋና ማውጫ", callback_data="main")]])
    )

async def send_support_to_admin(update, context, photo_id=None, text=None):
    user = update.effective_user
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = text or (update.message.caption or "")
    prefix = (
        "📩 **Player Support**\n\n"
        f"👤 {user.first_name or 'N/A'}\n"
        f"🆔 `{user.id}`\n"
        f"🔗 @{user.username if user.username else 'N/A'}\n"
        f"🕒 `{now}`\n\n"
    )
    try:
        if photo_id:
            msg = await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo_id,
                caption=prefix + (body if body else "📸 Photo ተልኳል።"),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Reply to Player", callback_data=f"support_reply:{user.id}")]])
            )
            content_type = "photo"
        else:
            msg = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=prefix + (body or ""),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Reply to Player", callback_data=f"support_reply:{user.id}")]])
            )
            content_type = "text"
        conn = db(); cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO support_messages(admin_message_id,user_id,created_at,direction,content_type) VALUES(?,?,?,?,?)", (msg.message_id, user.id, now, "user_to_admin", content_type))
        conn.commit(); conn.close()
        context.user_data.clear()
        await update.message.reply_text("✅ መልዕክትዎ Admin ጋር ደርሷል። ምላሽ ሲመጣ በዚህ Telegram ይደርስዎታል።")
    except Exception as e:
        logging.error(f"Support delivery error: {e}")
        await update.message.reply_text("❌ መልዕክቱን Admin ጋር መላክ አልተቻለም።")

async def support_reply_start(update, context, user_id):
    q = update.callback_query
    await q.answer()
    if not is_staff(q.from_user.id):
        return
    context.user_data.clear()
    context.user_data["support_reply_user_id"] = int(user_id)
    await q.message.reply_text(
        f"↩️ User `{user_id}` ላይ ምላሽ ለመላክ Text ወይም Photo ይላኩ።\n\n❌ /cancel",
        parse_mode="Markdown"
    )

async def send_admin_support_reply(update, context, photo_id=None, text=None):
    if not is_staff(update.effective_user.id):
        return False
    user_id = int(context.user_data.get("support_reply_user_id"))
    try:
        if photo_id:
            await context.bot.send_photo(chat_id=user_id, photo=photo_id, caption=text or "📩 Admin ምላሽ", parse_mode="Markdown")
            content_type = "photo"
        else:
            await context.bot.send_message(chat_id=user_id, text="📩 **Admin ምላሽ**\n\n" + (text or ""), parse_mode="Markdown")
            content_type = "text"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn=db(); cur=conn.cursor()
        cur.execute("INSERT INTO support_messages(admin_message_id,user_id,created_at,direction,content_type) VALUES(?,?,?,?,?)", (-int(datetime.now().timestamp()*1000000), user_id, now, "admin_to_user", content_type))
        conn.commit(); conn.close()
        context.user_data.pop("support_reply_user_id", None)
        await update.message.reply_text(f"✅ ምላሽ ለUser `{user_id}` ተልኳል።", parse_mode="Markdown")
        return True
    except Exception as e:
        logging.error(f"Admin support reply error: {e}")
        await update.message.reply_text("❌ ምላሽ መላክ አልተቻለም።")
        return True

# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_panel_handler(update, context):

    query = update.callback_query

    await query.answer()

    if not is_staff(query.from_user.id):
        return

    text = (
        "👑 **አድሚን / Manager ፓነል**\n\n"
        "የሚፈልጉትን ይምረጡ።"
    )

    keyboard = [

        [
            InlineKeyboardButton(
                "➕ አዲስ ጨረታ መፍጠር",
                callback_data="admin_create_auction"
            )
        ],

        [
            InlineKeyboardButton(
                "🎯 ንቁ ጨረታዎች",
                callback_data="admin_manage_auctions"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 የተጠናቀቁ ጨረታዎች",
                callback_data="admin_results"
            )
        ],
        [InlineKeyboardButton("👥 Users / Bot ተጠቃሚዎች", callback_data="admin_users:0")],

        [
            InlineKeyboardButton(
                "🏃‍♂️ ንቁ ተጫዋቾች እና ዋጋዎች",
                callback_data="admin_active_players"
            )
        ],

        [InlineKeyboardButton("📊 ጠቅላላ Users / Wallet", callback_data="admin_total_wallets")],

        [InlineKeyboardButton("🎮 Game Settings", callback_data="admin_game_settings")],

        [InlineKeyboardButton("✈️ Aviator Statistics", callback_data="aviator_admin_stats")],

        [InlineKeyboardButton("📢 ማስታወቂያ Post ላክ", callback_data="admin_announcement")],

        [InlineKeyboardButton("📩 Support Messages", callback_data="support_inbox")],

        *([[InlineKeyboardButton("👥 Manager አስተዳደር", callback_data="admin_managers")]] if is_admin(query.from_user.id) else []),

        [
            InlineKeyboardButton(
                "🔙 ዋና ማውጫ",
                callback_data="main"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================================
# ADMIN ANNOUNCEMENT / POST
# ============================================================

MAX_HOME_BANNERS = 5

async def save_announcement_as_banner(context, photo_id, link_page=""):
    """A PHOTO announcement is also shown in the Mini App home carousel (newest 5 kept). Shares the
    `banners` table + banners/ folder (same data volume) with the API's engagement_v8.py."""
    try:
        tg_file = await context.bot.get_file(photo_id)
        data = bytes(await tg_file.download_as_bytearray())
        ext = "png" if data.startswith(b"\x89PNG") else ("webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "jpg")
        folder = os.path.join(os.path.dirname(os.path.abspath(DB_FILE)) or ".", "banners")
        os.makedirs(folder, exist_ok=True)
        name = f"{secrets.token_hex(16)}.{ext}"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        conn = db(); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, link_page TEXT,
            seconds INTEGER NOT NULL DEFAULT 4, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
        cur.execute("INSERT INTO banners(filename,link_page,seconds,active,created_at) VALUES (?,?,?,1,?)",
                    (name, (link_page or "")[:30], 4, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        cur.execute("SELECT id, filename FROM banners WHERE active=1 ORDER BY id DESC")
        for r in cur.fetchall()[MAX_HOME_BANNERS:]:
            cur.execute("UPDATE banners SET active=0 WHERE id=?", (r["id"],))
            try: os.remove(os.path.join(folder, os.path.basename(r["filename"])))
            except OSError: pass
        conn.commit(); conn.close()
        return True
    except Exception as e:
        logging.warning(f"Could not save announcement photo as home banner: {e}")
        return False

async def send_admin_announcement(update, context, message_text=None, photo_id=None, video_id=None, animation_id=None, caption=None):
    """Broadcast text/photo/GIF/video with a Play Now URL button to users and optional channel."""
    banner_saved = await save_announcement_as_banner(context, photo_id) if photo_id else False
    conn=db(); cur=conn.cursor(); cur.execute("SELECT user_id FROM users WHERE banned=0"); users=cur.fetchall(); conn.close()
    delivered=failed=0; caption=(caption or "").strip()
    body = caption or (message_text or "").strip() or "🎮 ወደ ጨዋታዎቻችን ይግቡ!"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Play Now", url=WEBAPP_URL)]]) if WEBAPP_URL.startswith("https://") else None

    async def send_one(chat_id):
        prefix = "📢 ማስታወቂያ\n\n"
        if animation_id:
            return await context.bot.send_animation(chat_id=chat_id, animation=animation_id, caption=prefix+body, reply_markup=keyboard)
        if photo_id:
            return await context.bot.send_photo(chat_id=chat_id, photo=photo_id, caption=prefix+body, reply_markup=keyboard)
        if video_id:
            return await context.bot.send_video(chat_id=chat_id, video=video_id, caption=prefix+body, reply_markup=keyboard)
        return await context.bot.send_message(chat_id=chat_id, text=prefix+body, reply_markup=keyboard)

    # Publish to configured announcement channel as well as the user list.
    if ANNOUNCEMENT_CHANNEL_ID:
        try:
            await send_one(ANNOUNCEMENT_CHANNEL_ID)
        except Exception as e:
            failed += 1
            logging.warning(f"Announcement channel delivery failed: {e}")
    for u in users:
        try:
            await send_one(u["user_id"])
            delivered += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Announcement delivery failed for {u['user_id']}: {e}")
    await update.message.reply_text(
        f"📢 ማስታወቂያ ተልኳል\n\n👥 ተጠቃሚዎች: {len(users)}\n✅ Delivered: {delivered}\n❌ Failed: {failed}"
        + ("\n🖼 App ላይ banner ሆኖ ተለጥፏል" if banner_saved else "")
        + ("\n🎮 Play Now ቁልፍ ተጨምሯል።" if keyboard else "\n⚠️ MINI_APP_URL/WEBAPP_URL HTTPS አልተዋቀረም።"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 አድሚን ፓነል",callback_data="admin_panel")]])
    )




# ============================================================
# PROMO POST BUILDER
#   Admin/Manager: 1) picks the game the post opens  2) sends text / photo / video / GIF (Telegram formatting is kept:
#   bold, quote, emoji ...)  3) sees an exact PREVIEW  4) optionally adds extra buttons / vote buttons  5) publishes to
#   the channel and/or every user. Voting buttons (e.g. Liverpool / Arsenal) count one vote per person and show the totals.
# ============================================================
import re as _re
from telegram.error import RetryAfter, Forbidden, BadRequest, TelegramError

PROMO_SPECIAL = [("lobby", "🏠 ሎቢ (ሁሉም ጨዋታዎች)"), ("sports", "⚽ Sports"), ("marketplace", "🛍 Marketplace"), ("wheel", "🎡 Lucky Spin")]
PROMO_GAMES = [
    ("aviator", "✈️ Aviator"), ("crash", "📈 Crash"), ("rocket", "🚀 Rocket"), ("highflyer", "🛩️ High Flyer"),
    ("skymasters", "🛫 Sky Masters"), ("jet", "🚀 Jet"), ("keno", "🎯 Keno"), ("fastkeno", "⚡ Fast Keno"),
    ("keno_power", "🎱 Power Keno"), ("keno_super", "🟢 Super Keno"), ("keno_insta", "💎 Insta Keno"), ("mines", "💣 Mines"),
    ("boom", "💣 Boom"), ("number", "🔢 Number"), ("dice2", "🎲 Dice"), ("plinko", "🟣 Plinko"),
    ("roulette", "🎡 Roulette"), ("bingo", "🎱 Bingo"), ("slot", "🎰 777 Slot"), ("triplegold", "🔔 Triple Gold"),
    ("triplegold_deluxe", "🔔 Triple Gold Deluxe"), ("flame", "🔥 Flame Classics"), ("aztec", "🗿 Aztec Gold"),
    ("buffalo", "🦬 Buffalo Plains"), ("chicken", "🐔 Chicken Road"), ("chicken2", "🐔 Chicken Road 2"),
    ("rabbit", "🐇 Rabbit Road"), ("roast", "🔥 Chicken Roast"), ("tower", "🏗 Tower"), ("fish", "🐟 Fish"),
    ("penalty", "⚽ Penalty Shootout"), ("penaltystreak", "⚽ Penalty Streak"), ("coinstreak", "🪙 Coin Flip"),
    ("blackjack", "🃏 Blackjack"), ("baccarat", "🎴 Baccarat"), ("dragontiger", "🐉 Dragon Tiger"),
    ("andarbahar", "🃏 Andar Bahar"), ("card", "🃏 Card Table"),
]
PROMO_LABELS = dict(PROMO_SPECIAL + PROMO_GAMES)
PROMO_PAGE_SIZE = 12
PROMO_CAPTION_MAX = 1024
PROMO_TEXT_MAX = 4096
PROMO_MAX_EXTRA = 8
PROMO_KEY_RE = _re.compile(r"^[a-z0-9_]{1,40}$")


def promo_init_tables():
    conn = db(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS promo_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_by INTEGER, created_at TEXT, game TEXT, buttons_json TEXT, audience TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS promo_votes (
        post_id INTEGER NOT NULL, user_id INTEGER NOT NULL, choice INTEGER NOT NULL, created_at TEXT,
        PRIMARY KEY (post_id, user_id))""")
    conn.commit(); conn.close()


def promo_bot_username(context=None):
    name = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    if not name and context is not None:
        try: name = (context.bot.username or "").lstrip("@")
        except Exception: name = ""
    return name


def promo_open_button(text, key, private, bot_username):
    """Button that opens the Mini App on a given game. In a private chat it is a real Mini App button (so the player is
    logged in); in a channel Telegram only allows link buttons, so it is a t.me ?startapp= link."""
    key = (key or "").strip()
    if key in ("", "none"):
        return None
    if key == "lobby":
        key = ""
    if private and WEBAPP_URL.startswith("https://"):
        url = WEBAPP_URL + ((("&" if "?" in WEBAPP_URL else "?") + "game=" + key) if key else "")
        return InlineKeyboardButton(text, web_app=WebAppInfo(url=url))
    if bot_username:
        short = os.getenv("MINI_APP_SHORT_NAME", "").strip().strip("/")
        base = f"https://t.me/{bot_username}" + (f"/{short}" if short else "")
        return InlineKeyboardButton(text, url=f"{base}?startapp=g_{key or 'lobby'}")
    if WEBAPP_URL.startswith("https://"):
        return InlineKeyboardButton(text, url=WEBAPP_URL + ((("&" if "?" in WEBAPP_URL else "?") + "game=" + key) if key else ""))
    return None


def promo_keyboard(promo, post_id, private, bot_username, counts=None):
    """Main game button on top, vote buttons (2 per row, with totals once counts is a dict), then extra buttons."""
    rows = []
    main = promo_open_button(promo.get("btn_text") or "▶️ አሁን ይጫወቱ", promo.get("game"), private, bot_username)
    if main: rows.append([main])
    votes, others, vi = [], [], 0
    for e in promo.get("extra") or []:
        if e.get("t") == "vote":
            label = e["text"] + (f" ({int((counts or {}).get(vi, 0))})" if counts is not None else "")
            votes.append(InlineKeyboardButton(label, callback_data=f"pv:{int(post_id)}:{vi}")); vi += 1
        elif e.get("t") == "game":
            b = promo_open_button(e["text"], e.get("v"), private, bot_username)
            if b: others.append([b])
        elif e.get("t") == "url":
            others.append([InlineKeyboardButton(e["text"], url=e["v"])])
    for i in range(0, len(votes), 2): rows.append(votes[i:i + 2])
    rows += others
    return InlineKeyboardMarkup(rows) if rows else None


def promo_parse_buttons(text):
    """One button per line:  Text | vote   or   Text | game:keno   or   Text | https://link"""
    out, errors = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line: continue
        if "|" not in line:
            errors.append(f"«{line[:30]}» -- `ጽሑፍ | vote` ወይም `ጽሑፍ | game:keno` ወይም `ጽሑፍ | https://...` ይጠቀሙ"); continue
        label, target = [x.strip() for x in line.split("|", 1)]
        if not label or len(label) > 32:
            errors.append(f"«{label[:30]}» -- የቁልፍ ጽሑፍ 1-32 ፊደል መሆን አለበት"); continue
        low = target.lower()
        if low == "vote": out.append({"t": "vote", "text": label})
        elif low.startswith("game:"):
            key = low[5:].strip()
            if key in PROMO_LABELS or PROMO_KEY_RE.match(key): out.append({"t": "game", "text": label, "v": key})
            else: errors.append(f"«{label}» -- የጨዋታ ስም ትክክል አይደለም")
        elif low.startswith("https://"): out.append({"t": "url", "text": label, "v": target})
        else: errors.append(f"«{label}» -- ከ vote / game:ስም / https://... አንዱን ብቻ ይጠቀሙ")
    if len(out) > PROMO_MAX_EXTRA:
        errors.append(f"ከ{PROMO_MAX_EXTRA} በላይ ቁልፍ አይፈቀድም"); out = out[:PROMO_MAX_EXTRA]
    return out, errors


def promo_picker_markup(page=0):
    items = PROMO_SPECIAL + PROMO_GAMES
    pages = max(1, (len(items) + PROMO_PAGE_SIZE - 1) // PROMO_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    chunk = items[page * PROMO_PAGE_SIZE:(page + 1) * PROMO_PAGE_SIZE]
    rows = []
    for i in range(0, len(chunk), 2):
        rows.append([InlineKeyboardButton(l, callback_data=f"ap_g:{k}") for k, l in chunk[i:i + 2]])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"ap_pg:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="ap_noop"))
    if page < pages - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"ap_pg:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🚫 ቁልፍ አይኑር (ጽሑፍ/ፎቶ ብቻ)", callback_data="ap_g:none")])
    rows.append([InlineKeyboardButton("❌ ሰርዝ", callback_data="ap_cancel")])
    return InlineKeyboardMarkup(rows)


PROMO_PICK_TEXT = ("📢 Post ማስታወቂያ\n\n1️⃣ ይህ Post የሚከፍተውን ጨዋታ ይምረጡ።\n"
                   "ተጫዋቹ የ «አሁን ይጫወቱ» ቁልፉን ሲነካ Mini App በቀጥታ በዚያ ጨዋታ ላይ ይከፈታል።")


async def admin_announcement(update, context):
    q = update.callback_query; await q.answer()
    if not is_staff(q.from_user.id): return
    context.user_data.clear()
    await q.edit_message_text(PROMO_PICK_TEXT, reply_markup=promo_picker_markup(0))


async def promo_send_one(bot, chat_id, promo, kb):
    kind = promo.get("kind", "text"); h = promo.get("html") or ""
    if kind == "photo":
        return await bot.send_photo(chat_id=chat_id, photo=promo["file_id"], caption=h or None, parse_mode="HTML", reply_markup=kb)
    if kind == "video":
        return await bot.send_video(chat_id=chat_id, video=promo["file_id"], caption=h or None, parse_mode="HTML", reply_markup=kb)
    if kind == "animation":
        return await bot.send_animation(chat_id=chat_id, animation=promo["file_id"], caption=h or None, parse_mode="HTML", reply_markup=kb)
    return await bot.send_message(chat_id=chat_id, text=h or "🎮", parse_mode="HTML", reply_markup=kb)


async def promo_show_preview(message, context):
    promo = context.user_data.get("promo") or {}
    bu = promo_bot_username(context)
    try:
        await promo_send_one(context.bot, message.chat_id, promo, promo_keyboard(promo, 0, True, bu))
    except Exception as e:
        await message.reply_text(f"❌ ቅድመ እይታ አልተሳካም: {e}\nጽሑፉን እንደገና ይላኩ።"); return
    conn = db(); cur = conn.cursor(); cur.execute("SELECT COUNT(*) c FROM users WHERE banned=0"); n_users = cur.fetchone()["c"]; conn.close()
    game = promo.get("game") or "none"
    label = "ቁልፍ የለም" if game == "none" else PROMO_LABELS.get(game, game)
    extra = promo.get("extra") or []
    lines = ["👆 ይህ ነው የሚላከው (ቅድመ እይታ)።", "", f"🎮 የሚከፈት ጨዋታ: {label}",
             f"🔘 ተጨማሪ ቁልፎች: {len(extra)}" + (f" (ድምጽ: {sum(1 for e in extra if e['t'] == 'vote')})" if extra else ""),
             f"👥 Users: {n_users}", "📣 Channel: " + (f"✅ {ANNOUNCEMENT_CHANNEL_ID}" if ANNOUNCEMENT_CHANNEL_ID else "⚠️ ANNOUNCEMENT_CHANNEL_ID አልተዋቀረም"),
             "", "ሌላ ጽሑፍ/ፎቶ ቢልኩ ይህን ይተካል።"]
    rows = []
    if ANNOUNCEMENT_CHANNEL_ID:
        rows.append([InlineKeyboardButton("✅ ለሁሉም ላክ (Users + Channel)", callback_data="ap_send:all")])
        rows.append([InlineKeyboardButton("📣 Channel ብቻ", callback_data="ap_send:ch"), InlineKeyboardButton("👥 Users ብቻ", callback_data="ap_send:us")])
    else:
        rows.append([InlineKeyboardButton("✅ ለሁሉም Users ላክ", callback_data="ap_send:us")])
    rows.append([InlineKeyboardButton("➕ ተጨማሪ ቁልፎች / ድምጽ", callback_data="ap_btns"), InlineKeyboardButton("🎮 ጨዋታ ቀይር", callback_data="ap_game")])
    rows.append([InlineKeyboardButton("❌ ሰርዝ", callback_data="ap_cancel")])
    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def promo_content_received(update, context, kind, file_id=None):
    """Called from the text / photo / video / GIF handlers while an admin is building a post."""
    msg = update.message
    promo = context.user_data.setdefault("promo", {"game": "lobby", "extra": []})
    plain = (msg.text if kind == "text" else msg.caption) or ""
    html_text = (msg.text_html if kind == "text" else msg.caption_html) or ""
    limit = PROMO_TEXT_MAX if kind == "text" else PROMO_CAPTION_MAX
    if len(plain) > limit:
        await msg.reply_text(f"❌ ጽሑፉ በጣም ረጅም ነው ({len(plain)}). ከፍተኛው {limit} ፊደል ነው -- አሳጥረው ይላኩ።"); return
    if kind == "text" and not plain.strip():
        await msg.reply_text("❌ ባዶ ጽሑፍ ነው።"); return
    promo.update({"kind": kind, "file_id": file_id, "html": html_text, "wait_buttons": False})
    await promo_show_preview(msg, context)


async def promo_buttons_received(update, context):
    promo = context.user_data.get("promo") or {}
    out, errors = promo_parse_buttons(update.message.text or "")
    if errors:
        await update.message.reply_text("⚠️ እነዚህ መስመሮች አልተቀበሉም:\n" + "\n".join("• " + x for x in errors) + "\n\nእንደገና ይላኩ ወይም /cancel።"); return
    promo["extra"] = out; promo["wait_buttons"] = False
    await promo_show_preview(update.message, context)


def promo_counts(post_id):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT choice, COUNT(*) c FROM promo_votes WHERE post_id=? GROUP BY choice", (int(post_id),))
    d = {int(r["choice"]): int(r["c"]) for r in cur.fetchall()}; conn.close()
    return d


async def promo_vote(update, context):
    q = update.callback_query
    try:
        _, pid, idx = (q.data or "").split(":"); pid = int(pid); idx = int(idx)
    except Exception:
        await q.answer(); return
    if pid == 0:
        await q.answer("👀 ይህ ቅድመ እይታ ነው -- ድምጽ አይቆጠርም", show_alert=False); return
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT game, buttons_json FROM promo_posts WHERE id=?", (pid,)); row = cur.fetchone()
    if not row:
        conn.close(); await q.answer("Post አልተገኘም", show_alert=True); return
    extra = json.loads(row["buttons_json"] or "[]")
    n_votes = sum(1 for e in extra if e.get("t") == "vote")
    if idx < 0 or idx >= n_votes:
        conn.close(); await q.answer(); return
    cur.execute("""INSERT INTO promo_votes(post_id,user_id,choice,created_at) VALUES(?,?,?,?)
                   ON CONFLICT(post_id,user_id) DO UPDATE SET choice=excluded.choice, created_at=excluded.created_at""",
                (pid, q.from_user.id, idx, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()
    names = [e["text"] for e in extra if e.get("t") == "vote"]
    await q.answer(f"✅ ድምጽዎ ተመዝግቧል: {names[idx]}")
    try:
        private = (q.message.chat.type == "private") if q.message else True
        await q.edit_message_reply_markup(reply_markup=promo_keyboard({"game": row["game"], "extra": extra}, pid, private, promo_bot_username(context), promo_counts(pid)))
    except BadRequest:
        pass   # "message is not modified" (same totals) -- harmless
    except Exception as e:
        logging.warning(f"promo vote markup update failed: {e}")


async def promo_broadcast(application, admin_chat_id, promo, audience, post_id):
    bot = application.bot; bu = os.getenv("BOT_USERNAME", "").strip().lstrip("@") or (bot.username or "")
    sent = blocked = failed = 0; ch_status = "—"
    banner_saved = False
    if audience in ("all", "ch") and ANNOUNCEMENT_CHANNEL_ID:
        try:
            await promo_send_one(bot, ANNOUNCEMENT_CHANNEL_ID, promo, promo_keyboard(promo, post_id, False, bu))
            ch_status = "✅ ተልኳል"
        except Exception as e:
            ch_status = f"❌ {e}"; logging.warning(f"promo channel delivery failed: {e}")
    if audience in ("all", "us"):
        conn = db(); cur = conn.cursor(); cur.execute("SELECT user_id FROM users WHERE banned=0"); users = [r["user_id"] for r in cur.fetchall()]; conn.close()
        kb = promo_keyboard(promo, post_id, True, bu)
        for uid in users:
            for attempt in (1, 2):
                try:
                    await promo_send_one(bot, uid, promo, kb); sent += 1; break
                except RetryAfter as e:
                    await asyncio.sleep(float(e.retry_after) + 1)
                except Forbidden:
                    blocked += 1; break
                except Exception as e:
                    failed += 1; logging.warning(f"promo delivery failed for {uid}: {e}"); break
            await asyncio.sleep(0.05)     # stay far below Telegram's 30 messages / second limit
    if promo.get("kind") == "photo" and promo.get("file_id"):
        link = promo.get("game") if promo.get("game") not in (None, "none") else ""
        class _Ctx: pass
        _c = _Ctx(); _c.bot = bot
        banner_saved = await save_announcement_as_banner(_c, promo["file_id"], link_page=(link or "")[:30])
    summary = ["📢 Post ተጠናቀቀ", ""]
    if audience in ("all", "ch"): summary.append(f"📣 Channel: {ch_status}")
    if audience in ("all", "us"): summary.append(f"👥 ተልኳል: {sent}   🚫 Bot ያገዱ: {blocked}   ❌ አልተሳካም: {failed}")
    if banner_saved: summary.append("🖼 App ላይ banner ሆኖ ተለጥፏል")
    if any(e.get("t") == "vote" for e in promo.get("extra") or []): summary.append("🗳 ድምጽ ቁልፎች ንቁ ናቸው -- ጠቅላላ ድምጽ በቁልፉ ላይ ይታያል")
    try:
        await bot.send_message(admin_chat_id, "\n".join(summary), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 አድሚን ፓነል", callback_data="admin_panel")]]))
    except Exception:
        pass


async def promo_callback(update, context):
    q = update.callback_query; data = q.data or ""
    if data.startswith("pv:"):
        await promo_vote(update, context); return
    if not is_staff(q.from_user.id):
        await q.answer("⛔ ለAdmin / Manager ብቻ", show_alert=True); return
    if data == "ap_noop":
        await q.answer(); return
    if data.startswith("ap_pg:"):
        await q.answer()
        try: await q.edit_message_reply_markup(reply_markup=promo_picker_markup(int(data.split(":")[1])))
        except Exception: pass
        return
    if data == "ap_cancel":
        context.user_data.clear(); await q.answer("ተሰርዟል")
        try: await q.edit_message_text("❌ ማስታወቂያው ተሰርዟል።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 አድሚን ፓነል", callback_data="admin_panel")]]))
        except Exception: pass
        return
    if data.startswith("ap_g:"):
        key = data.split(":", 1)[1]
        if key != "none" and key not in PROMO_LABELS:
            await q.answer("ያልታወቀ ጨዋታ", show_alert=True); return
        promo = context.user_data.setdefault("promo", {"game": "lobby", "extra": []})
        promo["game"] = key; context.user_data["waiting_admin_announcement"] = True
       label = "ቁልፍ አይኖርም" if key == "none" else PROMO_LABELS.get(key, key)
        await q.answer()
        if promo.get("kind"):
            await promo_show_preview(q.message, context); return
        try:
            await q.edit_message_text(
                f"✅ ጨዋታ: {label}\n\n2️⃣ አሁን Post ይላኩ:\n• ✍️ ጽሑፍ  • 📸 ፎቶ  • 🎥 ቪዲዮ  • GIF\n"
                "ፎቶ/ቪዲዮ ከሆነ ጽሑፉን Caption ላይ ይጻፉ። Bold፣ Quote፣ Emoji ሁሉ እንዳለ ይቀራል።\n\n"
                "ከመላክዎ በፊት ቅድመ እይታ ያያሉ። ለመሰረዝ /cancel",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 ጨዋታ ቀይር", callback_data="ap_game"), InlineKeyboardButton("❌ ሰርዝ", callback_data="ap_cancel")]]))
        except Exception:
            pass
        return
    if data == "ap_game":
        await q.answer()
        context.user_data.setdefault("promo", {"game": "lobby", "extra": []})["wait_buttons"] = False
        await q.message.reply_text(PROMO_PICK_TEXT, reply_markup=promo_picker_markup(0)); return
    if data == "ap_btns":
        promo = context.user_data.get("promo")
        if not promo or not promo.get("kind"):
            await q.answer("መጀመሪያ Post ይላኩ", show_alert=True); return
        promo["wait_buttons"] = True; context.user_data["waiting_admin_announcement"] = True
        await q.answer()
        await q.message.reply_text(
            "➕ ተጨማሪ ቁልፎች -- አንድ መስመር = አንድ ቁልፍ:\n\n"
            "🗳 ድምጽ ቁልፍ:\n  ሊቨርፑል | vote\n  አርሰናል | vote\n\n"
            "🎮 ሌላ ጨዋታ የሚከፍት:\n  Keno ተጫወት | game:keno\n\n"
            "🔗 ሊንክ:\n  Channel | https://t.me/yourchannel\n\n"
            f"(ከፍተኛ {PROMO_MAX_EXTRA} ቁልፍ።) ሁሉንም ቁልፎች ለማጥፋት clear ብለው ይጻፉ።  ለመሰረዝ /cancel")
        return
    if data.startswith("ap_send:"):
        promo = context.user_data.get("promo")
        if not pro
