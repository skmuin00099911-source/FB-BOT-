#!/usr/bin/env python3
"""
Facebook Task Bot - Telegram Bot
A complete task management bot for earning BDT by completing Facebook tasks.
Built with pyTelegramBotAPI, SQLite, and deployed on Railway.
"""

import os
import sqlite3
import random
import string
import logging
import time
import threading
from datetime import datetime, timedelta
from functools import wraps

import telebot
from telebot import types

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "123456789").split(",")))

# Bot settings (overridable via Admin Panel → Settings in DB)
DEFAULT_TASK_REWARD     = 4.00      # BDT
DEFAULT_MIN_WITHDRAW    = 100.00    # BDT
DEFAULT_TASK_PASSWORD   = "fbemon@16"
DEFAULT_REFERRAL_BONUS  = 10.00     # BDT
DEFAULT_DAILY_BONUS     = 2.00      # BDT
TASK_COOLDOWN_SECONDS   = 3600      # 1 hour between tasks
DB_PATH                 = "bot.db"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ─────────────────────────────────────────────
# NAME POOLS
# ─────────────────────────────────────────────

FIRST_NAMES = [
    "Oliver", "James", "David", "Sophia", "Emma", "Liam", "Noah", "Ava",
    "Isabella", "Mia", "Ethan", "Mason", "Logan", "Lucas", "Jackson",
    "Aiden", "Caden", "Grayson", "Elijah", "Ryan", "Sebastian", "Mateo",
    "Scarlett", "Victoria", "Aria", "Grace", "Chloe", "Penelope", "Lily",
    "Riley", "Zoey", "Nora", "Hannah", "Eleanor", "Charlotte", "Amelia",
    "Benjamin", "William", "Alexander", "Henry", "Samuel", "Joseph", "Owen",
    "Daniel", "Gabriel", "Carter", "Wyatt", "Julian", "Levi", "Isaac",
    "Lincoln", "Jaxon", "Ezra", "Thiago", "Maverick", "Hudson", "Asher",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "Jackson", "White",
    "Harris", "Martin", "Thompson", "Lee", "Walker", "Hall", "Allen",
    "Young", "Hernandez", "King", "Wright", "Lopez", "Hill", "Scott",
    "Green", "Adams", "Baker", "Nelson", "Carter", "Mitchell", "Roberts",
    "Turner", "Phillips", "Campbell", "Parker", "Evans", "Collins", "Edwards",
    "Stewart", "Morris", "Sanchez", "Rogers", "Reed", "Cook", "Bailey",
    "Bell", "Cooper", "Richardson", "Cox", "Howard", "Ward", "Torres",
]

def generate_name():
    """Generate a random realistic English full name."""
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)

# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

def get_db():
    """Get a database connection with row_factory."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Initialize all database tables."""
    conn = get_db()
    c = conn.cursor()

    # Users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            balance     REAL    DEFAULT 0.0,
            total_earned REAL   DEFAULT 0.0,
            referral_code TEXT  UNIQUE,
            referred_by INTEGER DEFAULT NULL,
            referral_count INTEGER DEFAULT 0,
            is_banned   INTEGER DEFAULT 0,
            joined_date TEXT    DEFAULT (datetime('now')),
            last_daily  TEXT    DEFAULT NULL,
            state       TEXT    DEFAULT NULL
        )
    """)

    # Tasks table (admin-defined task types)
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            description TEXT,
            reward      REAL    NOT NULL,
            password    TEXT    DEFAULT 'fbemon@16',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)

    # Completed / pending task submissions
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_submissions (
            sub_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            task_id     INTEGER NOT NULL,
            first_name  TEXT,
            last_name   TEXT,
            fb_uid      TEXT,
            status      TEXT    DEFAULT 'pending',
            submitted_at TEXT   DEFAULT (datetime('now')),
            reviewed_at  TEXT   DEFAULT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(task_id) REFERENCES tasks(task_id)
        )
    """)

    # Withdraw requests
    c.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            withdraw_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            amount       REAL    NOT NULL,
            method       TEXT    DEFAULT 'USDT (BEP20)',
            wallet_addr  TEXT    NOT NULL,
            status       TEXT    DEFAULT 'pending',
            requested_at TEXT    DEFAULT (datetime('now')),
            reviewed_at  TEXT    DEFAULT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

    # Force-join channels
    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_link TEXT    NOT NULL,
            channel_name TEXT,
            is_active    INTEGER DEFAULT 1
        )
    """)

    # Bot settings key-value store
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key     TEXT PRIMARY KEY,
            value   TEXT
        )
    """)

    # Admin action logs
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER,
            action      TEXT,
            target_id   INTEGER DEFAULT NULL,
            details     TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # Task cooldown tracking
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_cooldowns (
            user_id     INTEGER NOT NULL,
            task_id     INTEGER NOT NULL,
            last_start  TEXT,
            PRIMARY KEY (user_id, task_id)
        )
    """)

    # Insert default settings
    defaults = {
        "task_reward":      str(DEFAULT_TASK_REWARD),
        "min_withdraw":     str(DEFAULT_MIN_WITHDRAW),
        "task_password":    DEFAULT_TASK_PASSWORD,
        "referral_bonus":   str(DEFAULT_REFERRAL_BONUS),
        "daily_bonus":      str(DEFAULT_DAILY_BONUS),
        "maintenance_mode": "0",
        "task_cooldown":    str(TASK_COOLDOWN_SECONDS),
        "admin_username":   "@YourAdminUsername",
    }
    for key, value in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # Insert default Facebook task
    c.execute("""
        INSERT OR IGNORE INTO tasks (task_id, title, description, reward, password)
        VALUES (1, '📘 Facebook any/mail', 'Create a Facebook account using any email.', 4.00, 'fbemon@16')
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized.")

# ─────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────

def get_setting(key: str, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_or_create_user(user: types.User):
    """Fetch user from DB or create if new. Returns dict."""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    if not row:
        ref_code = generate_referral_code(user.id)
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        conn.execute("""
            INSERT INTO users (user_id, username, full_name, referral_code)
            VALUES (?, ?, ?, ?)
        """, (user.id, user.username, full_name, ref_code))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
        logger.info(f"New user registered: {user.id} ({full_name})")
    conn.close()
    return dict(row)

def get_user(user_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_balance(user_id: int, amount: float, add: bool = True):
    conn = get_db()
    if add:
        conn.execute(
            "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?",
            (amount, amount, user_id)
        )
    else:
        conn.execute(
            "UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?",
            (amount, user_id)
        )
    conn.commit()
    conn.close()

def set_user_state(user_id: int, state: str):
    conn = get_db()
    conn.execute("UPDATE users SET state = ? WHERE user_id = ?", (state, user_id))
    conn.commit()
    conn.close()

def get_user_state(user_id: int) -> str:
    conn = get_db()
    row = conn.execute("SELECT state FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["state"] if row else None

def generate_referral_code(user_id: int) -> str:
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"REF{user_id}{suffix}"

def log_admin_action(admin_id: int, action: str, target_id: int = None, details: str = ""):
    conn = get_db()
    conn.execute(
        "INSERT INTO admin_logs (admin_id, action, target_id, details) VALUES (?, ?, ?, ?)",
        (admin_id, action, target_id, details)
    )
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(
        types.KeyboardButton("📋 Tasks"),
        types.KeyboardButton("💰 Balance"),
        types.KeyboardButton("👤 Profile"),
    )
    kb.add(
        types.KeyboardButton("🎁 Daily Bonus"),
        types.KeyboardButton("👥 Referral"),
        types.KeyboardButton("📩 Support"),
    )
    kb.add(types.KeyboardButton("📤 Withdraw"))
    return kb

def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📊 Dashboard"),
        types.KeyboardButton("👥 User Management"),
    )
    kb.add(
        types.KeyboardButton("📋 Manage Tasks"),
        types.KeyboardButton("📄 Pending Tasks"),
    )
    kb.add(
        types.KeyboardButton("📤 Pending Withdraws"),
        types.KeyboardButton("📢 Broadcast"),
    )
    kb.add(
        types.KeyboardButton("⚙️ Settings"),
        types.KeyboardButton("🚪 Exit Admin"),
    )
    return kb

def cancel_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❌ Cancel"))
    return kb

def task_action_keyboard(sub_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_task_{sub_id}"),
        types.InlineKeyboardButton("❌ Reject",  callback_data=f"reject_task_{sub_id}"),
    )
    return kb

def withdraw_action_keyboard(withdraw_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_wd_{withdraw_id}"),
        types.InlineKeyboardButton("❌ Reject",  callback_data=f"reject_wd_{withdraw_id}"),
    )
    return kb

def start_task_keyboard(task_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("▶️ Start Task", callback_data=f"start_task_{task_id}"))
    return kb

def submit_cancel_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Submit", callback_data="submit_task"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_task"),
    )
    return kb

# ─────────────────────────────────────────────
# DECORATORS
# ─────────────────────────────────────────────

def admin_only(func):
    """Decorator: restrict handler to admin IDs only."""
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        if message.from_user.id not in ADMIN_IDS:
            bot.send_message(message.chat.id, "🚫 <b>Access Denied.</b>\nThis command is for admins only.")
            return
        return func(message, *args, **kwargs)
    return wrapper

def maintenance_check(func):
    """Decorator: block non-admin users when maintenance mode is on."""
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        if get_setting("maintenance_mode") == "1" and message.from_user.id not in ADMIN_IDS:
            bot.send_message(
                message.chat.id,
                "🔧 <b>Maintenance Mode</b>\n\nThe bot is currently under maintenance.\nPlease check back later. ⏳"
            )
            return
        return func(message, *args, **kwargs)
    return wrapper

def ban_check(func):
    """Decorator: block banned users."""
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        user = get_user(message.from_user.id)
        if user and user["is_banned"]:
            bot.send_message(
                message.chat.id,
                "🚫 <b>Account Banned</b>\n\nYour account has been banned.\nContact support if you believe this is an error."
            )
            return
        return func(message, *args, **kwargs)
    return wrapper

# ─────────────────────────────────────────────
# FORCE JOIN VERIFICATION
# ─────────────────────────────────────────────

def check_force_join(user_id: int) -> list:
    """Returns list of channels user has NOT joined."""
    conn = get_db()
    channels = conn.execute(
        "SELECT * FROM channels WHERE is_active = 1"
    ).fetchall()
    conn.close()

    not_joined = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch["channel_link"], user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(dict(ch))
        except Exception:
            not_joined.append(dict(ch))
    return not_joined

def force_join_message(user_id: int, chat_id: int) -> bool:
    """
    Send force-join message if user hasn't joined required channels.
    Returns True if user must join (block further action), False if ok.
    """
    missing = check_force_join(user_id)
    if not missing:
        return False

    kb = types.InlineKeyboardMarkup()
    for ch in missing:
        kb.add(types.InlineKeyboardButton(
            f"📢 Join {ch['channel_name'] or ch['channel_link']}",
            url=f"[t.me](https://t.me/{ch)['channel_link'].lstrip('@')}"
        ))
    kb.add(types.InlineKeyboardButton("✅ I've Joined", callback_data="check_joined"))

    bot.send_message(
        chat_id,
        "📢 <b>Join Required Channels</b>\n\n"
        "You must join our channels to use this bot.\n"
        "Click the buttons below to join, then press <b>✅ I've Joined</b>.",
        reply_markup=kb
    )
    return True

# ─────────────────────────────────────────────
# /start COMMAND
# ─────────────────────────────────────────────

@bot.message_handler(commands=["start"])
@maintenance_check
def cmd_start(message: types.Message):
    user_data = message.from_user
    args = message.text.split()

    # Handle referral: /start REF123456ABC
    referred_by = None
    if len(args) > 1:
        ref_code = args[1]
        conn = get_db()
        referrer = conn.execute(
            "SELECT * FROM users WHERE referral_code = ?", (ref_code,)
        ).fetchone()
        conn.close()
        if referrer and referrer["user_id"] != user_data.id:
            referred_by = referrer["user_id"]

    # Get or create user
    existing = get_user(user_data.id)
    is_new = existing is None
    user = get_or_create_user(user_data)

    # Process referral bonus for new users
    if is_new and referred_by:
        bonus = float(get_setting("referral_bonus", DEFAULT_REFERRAL_BONUS))
        conn = get_db()
        conn.execute(
            "UPDATE users SET referred_by = ?, referral_count = referral_count + 1 WHERE user_id = ?",
            (referred_by, user_data.id)
        )
        conn.commit()
        conn.close()
        # Give bonus to referrer
        update_user_balance(referred_by, bonus)
        try:
            bot.send_message(
                referred_by,
                f"🎉 <b>Referral Bonus!</b>\n\n"
                f"Someone joined using your referral link.\n"
                f"<b>+{bonus:.2f} BDT</b> has been added to your balance! 💰"
            )
        except Exception:
            pass

    # Force join check
    if force_join_message(user_data.id, message.chat.id):
        return

    name = user_data.first_name or "there"
    greeting = "👋 Welcome back" if not is_new else "👋 Welcome"

    bot.send_message(
        message.chat.id,
        f"{greeting}, <b>{name}</b>!\n\n"
        f"🤖 <b>Facebook Task Bot</b>\n\n"
        f"💰 Earn money by completing Facebook account creation tasks.\n\n"
        f"📌 <b>How it works:</b>\n"
        f"  1️⃣ Go to <b>📋 Tasks</b>\n"
        f"  2️⃣ Create a Facebook account with the given details\n"
        f"  3️⃣ Submit your Facebook UID or Profile Link\n"
        f"  4️⃣ Get paid after admin approval! 💸\n\n"
        f"Choose an option below 👇",
        reply_markup=main_keyboard()
    )
    set_user_state(user_data.id, None)

# ─────────────────────────────────────────────
# 📋 TASKS
# ─────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📋 Tasks")
@maintenance_check
@ban_check
def show_tasks(message: types.Message):
    if force_join_message(message.from_user.id, message.chat.id):
        return

    conn = get_db()
    tasks = conn.execute("SELECT * FROM tasks WHERE is_active = 1").fetchall()
    conn.close()

    if not tasks:
        bot.send_message(message.chat.id, "😔 <b>No tasks available right now.</b>\nCheck back later!")
        return

    for task in tasks:
        task = dict(task)
        bot.send_message(
            message.chat.id,
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"  {task['title']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 <b>Description:</b>\n"
            f"{task['description']}\n\n"
            f"💰 <b>Reward:</b> <code>{task['reward']:.2f} BDT</code>\n\n"
            f"🔑 <b>Password:</b> <code>{task['password']}</code>",
            reply_markup=start_task_keyboard(task["task_id"])
        )

# ─────────────────────────────────────────────
# ▶️ START TASK (Inline Button)
# ─────────────────────────────────────────────

# Temporary in-memory store for active task sessions
# Key: user_id → {"task_id": int, "first_name": str, "last_name": str}
active_tasks = {}

@bot.callback_query_handler(func=lambda c: c.data.startswith("start_task_"))
def handle_start_task(call: types.CallbackQuery):
    user_id  = call.from_user.id
    task_id  = int(call.data.split("_")[-1])
    user     = get_user(user_id)

    if not user:
        bot.answer_callback_query(call.id, "Please send /start first.")
        return
    if user["is_banned"]:
        bot.answer_callback_query(call.id, "🚫 Your account is banned.", show_alert=True)
        return

    # Cooldown check
    cooldown = int(get_setting("task_cooldown", TASK_COOLDOWN_SECONDS))
    conn = get_db()
    cd_row = conn.execute(
        "SELECT last_start FROM task_cooldowns WHERE user_id = ? AND task_id = ?",
        (user_id, task_id)
    ).fetchone()
    conn.close()

    if cd_row and cd_row["last_start"]:
        last = datetime.fromisoformat(cd_row["last_start"])
        elapsed = (datetime.utcnow() - last).total_seconds()
        if elapsed < cooldown:
            remaining = cooldown - int(elapsed)
            mins, secs = divmod(remaining, 60)
            bot.answer_callback_query(
                call.id,
                f"⏳ Cooldown active! Wait {mins}m {secs}s before starting this task again.",
                show_alert=True
            )
            return

    # Fetch task
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE task_id = ? AND is_active = 1", (task_id,)).fetchone()
    conn.close()
    if not task:
        bot.answer_callback_query(call.id, "Task not found.", show_alert=True)
        return
    task = dict(task)

    # Generate names
    first_name, last_name = generate_name()

    # Store session
    active_tasks[user_id] = {
        "task_id":    task_id,
        "first_name": first_name,
        "last_name":  last_name,
    }
    set_user_state(user_id, "awaiting_submit")

    bot.answer_callback_query(call.id, "✅ Task started!")
    bot.send_message(
        call.message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  📋 <b>Task Details</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>First Name:</b>  <code>{first_name}</code>\n"
        f"👤 <b>Last Name:</b>   <code>{last_name}</code>\n"
        f"🔑 <b>Password:</b>    <code>{task['password']}</code>\n\n"
        f"💰 <b>Reward:</b> <code>{task['reward']:.2f} BDT</code>\n\n"
        f"📌 <i>Create a Facebook account using the above details, then submit your UID or Profile Link.</i>",
        reply_markup=submit_cancel_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data == "cancel_task")
def handle_cancel_task(call: types.CallbackQuery):
    user_id = call.from_user.id
    active_tasks.pop(user_id, None)
    set_user_state(user_id, None)
    bot.answer_callback_query(call.id, "❌ Task cancelled.")
    bot.send_message(call.message.chat.id, "❌ <b>Task cancelled.</b>", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data == "submit_task")
def handle_submit_task_button(call: types.CallbackQuery):
    user_id = call.from_user.id
    if user_id not in active_tasks:
        bot.answer_callback_query(call.id, "No active task. Please start a task first.", show_alert=True)
        return

    set_user_state(user_id, "awaiting_fb_uid")
    bot.answer_callback_query(call.id, "📩 Please send your Facebook UID or Profile Link.")
    bot.send_message(
        call.message.chat.id,
        "📩 <b>Submit Your Task</b>\n\n"
        "Please send your <b>Facebook UID</b> or <b>Profile Link</b>.\n\n"
        "<i>Example:</i>\n"
        "<code>100012345678901</code>\n"
        "or\n"
        "<code>[facebook.com](https://www.facebook.com/profile.php?id=100012345678901)</code>",
        reply_markup=cancel_keyboard()
    )

# ─────────────────────────────────────────────
# HANDLE TEXT MESSAGES (State Machine)
# ─────────────────────────────────────────────

@bot.message_handler(func=lambda m: True, content_types=["text"])
@maintenance_check
@ban_check
def handle_text(message: types.Message):
    user_id  = message.from_user.id
    text     = message.text.strip()
    state    = get_user_state(user_id)

    # ── Main menu buttons ──────────────────────────
    if text == "💰 Balance":
        return show_balance(message)
    if text == "👤 Profile":
        return show_profile(message)
    if text == "📩 Support":
        return show_support(message)
    if text == "📤 Withdraw":
        return start_withdraw(message)
    if text == "🎁 Daily Bonus":
        return claim_daily_bonus(message)
    if text == "👥 Referral":
        return show_referral(message)
    if text == "❌ Cancel":
        active_tasks.pop(user_id, None)
        set_user_state(user_id, None)
        return bot.send_message(message.chat.id, "❌ <b>Cancelled.</b>", reply_markup=main_keyboard())

    # ── Admin menu buttons ─────────────────────────
    if user_id in ADMIN_IDS:
        if text == "📊 Dashboard":
            return admin_dashboard(message)
        if text == "👥 User Management":
            return admin_user_menu(message)
        if text == "📋 Manage Tasks":
            return admin_manage_tasks(message)
        if text == "📄 Pending Tasks":
            return admin_pending_tasks(message)
        if text == "📤 Pending Withdraws":
            return admin_pending_withdrawals(message)
        if text == "📢 Broadcast":
            return admin_broadcast_start(message)
        if text == "⚙️ Settings":
            return admin_settings_menu(message)
        if text == "🚪 Exit Admin":
            set_user_state(user_id, None)
            return bot.send_message(
                message.chat.id,
                "🚪 <b>Exited Admin Panel.</b>",
                reply_markup=main_keyboard()
            )

    # ── State-based handlers ───────────────────────
    if state == "awaiting_fb_uid":
        return process_fb_uid_submission(message)

    if state == "awaiting_withdraw_amount":
        return process_withdraw_amount(message)

    if state == "awaiting_withdraw_wallet":
        return process_withdraw_wallet(message)

    # Admin states
    if state and state.startswith("admin_"):
        return handle_admin_state(message, state)

# ─────────────────────────────────────────────
# PROCESS FB UID SUBMISSION
# ─────────────────────────────────────────────

def process_fb_uid_submission(message: types.Message):
    user_id = message.from_user.id
    fb_uid  = message.text.strip()

    session = active_tasks.get(user_id)
    if not session:
        set_user_state(user_id, None)
        bot.send_message(message.chat.id, "⚠️ Session expired. Please start a new task.", reply_markup=main_keyboard())
        return

    task_id    = session["task_id"]
    first_name = session["first_name"]
    last_name  = session["last_name"]

    # Save submission to DB
    conn = get_db()
    conn.execute("""
        INSERT INTO task_submissions (user_id, task_id, first_name, last_name, fb_uid)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, task_id, first_name, last_name, fb_uid))

    # Update cooldown
    conn.execute("""
        INSERT OR REPLACE INTO task_cooldowns (user_id, task_id, last_start)
        VALUES (?, ?, datetime('now'))
    """, (user_id, task_id))
    sub_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.commit()
    conn.close()

    # Notify user
    active_tasks.pop(user_id, None)
    set_user_state(user_id, None)

    bot.send_message(
        message.chat.id,
        f"✅ <b>Task Submitted!</b>\n\n"
        f"📋 <b>Task:</b> {task['title']}\n"
        f"👤 <b>Name Used:</b> {first_name} {last_name}\n"
        f"🔗 <b>FB UID/Link:</b> <code>{fb_uid}</code>\n"
        f"💰 <b>Pending Reward:</b> {task['reward']:.2f} BDT\n\n"
        f"⏳ <i>Your submission is under review. Reward will be credited after admin approval.</i>",
        reply_markup=main_keyboard()
    )

    # Notify all admins
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"📥 <b>New Task Submission</b>\n\n"
                f"👤 <b>User:</b> {user['full_name']} (@{user['username'] or 'N/A'})\n"
                f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
                f"📋 <b>Task:</b> {task['title']}\n"
                f"👤 <b>Name Used:</b> {first_name} {last_name}\n"
                f"🔗 <b>FB UID/Link:</b> <code>{fb_uid}</code>\n"
                f"💰 <b>Reward:</b> {task['reward']:.2f} BDT\n"
                f"🕐 <b>Submitted:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
                reply_markup=task_action_keyboard(sub_id)
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# ─────────────────────────────────────────────
# APPROVE / REJECT TASK (Admin Inline)
# ─────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("approve_task_") or c.data.startswith("reject_task_"))
def handle_task_review(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "🚫 Admins only.", show_alert=True)
        return

    parts  = call.data.split("_")
    action = parts[0]   # "approve" or "reject"
    sub_id = int(parts[2])

    conn = get_db()
    sub  = conn.execute(
        "SELECT ts.*, t.reward, t.title FROM task_submissions ts JOIN tasks t ON ts.task_id = t.task_id WHERE ts.sub_id = ?",
        (sub_id,)
    ).fetchone()

    if not sub:
        bot.answer_callback_query(call.id, "Submission not found.", show_alert=True)
        conn.close()
        return
    sub = dict(sub)

    if sub["status"] != "pending":
        bot.answer_callback_query(call.id, f"Already {sub['status']}.", show_alert=True)
        conn.close()
        return

    if action == "approve":
        conn.execute(
            "UPDATE task_submissions SET status = 'approved', reviewed_at = datetime('now') WHERE sub_id = ?",
            (sub_id,)
        )
        conn.commit()
        conn.close()

        update_user_balance(sub["user_id"], sub["reward"])
        bot.answer_callback_query(call.id, f"✅ Approved! +{sub['reward']:.2f} BDT added.", show_alert=True)

        # Edit admin message
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"✅ Task #{sub_id} <b>Approved</b> — +{sub['reward']:.2f} BDT credited.")

        # Notify user
        try:
            bot.send_message(
                sub["user_id"],
                f"🎉 <b>Task Approved!</b>\n\n"
                f"📋 <b>Task:</b> {sub['title']}\n"
                f"💰 <b>+{sub['reward']:.2f} BDT</b> has been added to your balance!\n\n"
                f"Keep completing tasks to earn more! 💪"
            )
        except Exception:
            pass

        log_admin_action(call.from_user.id, "APPROVE_TASK", sub["user_id"], f"sub_id={sub_id}, reward={sub['reward']}")

    else:  # reject
        conn.execute(
            "UPDATE task_submissions SET status = 'rejected', reviewed_at = datetime('now') WHERE sub_id = ?",
            (sub_id,)
        )
        conn.commit()
        conn.close()

        bot.answer_callback_query(call.id, "❌ Rejected.", show_alert=True)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"❌ Task #{sub_id} <b>Rejected</b>.")

        try:
            bot.send_message(
                sub["user_id"],
                f"❌ <b>Task Rejected</b>\n\n"
                f"📋 <b>Task:</b> {sub['title']}\n"
                f"Your submission was not approved by the admin.\n\n"
                f"Please ensure you follow the task instructions carefully and resubmit."
            )
        except Exception:
            pass

        log_admin_action(call.from_user.id, "REJECT_TASK", sub["user_id"], f"sub_id={sub_id}")

# ─────────────────────────────────────────────
# 💰 BALANCE
# ─────────────────────────────────────────────

def show_balance(message: types.Message):
    user_id = message.from_user.id
    user    = get_user(user_id)
    if not user:
        return

    conn = get_db()
    completed = conn.execute(
        "SELECT COUNT(*) as c FROM task_submissions WHERE user_id = ? AND status = 'approved'",
        (user_id,)
    ).fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) as c FROM task_submissions WHERE user_id = ? AND status = 'pending'",
        (user_id,)
    ).fetchone()["c"]
    conn.close()

    min_wd = float(get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW))
    withdrawable = user["balance"] if user["balance"] >= min_wd else 0.0

    bot.send_message(
        message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  💰 <b>Your Balance</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💵 <b>Current Balance:</b>   <code>{user['balance']:.2f} BDT</code>\n"
        f"📈 <b>Total Earned:</b>       <code>{user['total_earned']:.2f} BDT</code>\n"
        f"✅ <b>Completed Tasks:</b>    <code>{completed}</code>\n"
        f"⏳ <b>Pending Tasks:</b>      <code>{pending}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Withdrawable:</b>  <code>{withdrawable:.2f} BDT</code>\n"
        f"🔒 <b>Min Withdraw:</b>  <code>{min_wd:.2f} BDT</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

# ─────────────────────────────────────────────
# 👤 PROFILE
# ─────────────────────────────────────────────

def show_profile(message: types.Message):
    user_id = message.from_user.id
    user    = get_user(user_id)
    tg_user = message.from_user
    if not user:
        return

    conn = get_db()
    completed = conn.execute(
        "SELECT COUNT(*) as c FROM task_submissions WHERE user_id = ? AND status = 'approved'",
        (user_id,)
    ).fetchone()["c"]
    conn.close()

    joined = user["joined_date"][:10] if user["joined_date"] else "N/A"
    username_display = f"@{tg_user.username}" if tg_user.username else "Not set"

    bot.send_message(
        message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  👤 <b>Your Profile</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 <b>Name:</b>          {tg_user.full_name}\n"
        f"🔖 <b>Username:</b>      {username_display}\n"
        f"🆔 <b>User ID:</b>       <code>{user_id}</code>\n\n"
        f"💰 <b>Balance:</b>        <code>{user['balance']:.2f} BDT</code>\n"
        f"📈 <b>Total Earned:</b>   <code>{user['total_earned']:.2f} BDT</code>\n"
        f"✅ <b>Tasks Done:</b>     <code>{completed}</code>\n"
        f"👥 <b>Referrals:</b>      <code>{user['referral_count']}</code>\n\n"
        f"📅 <b>Joined:</b>         <code>{joined}</code>\n"
        f"🎫 <b>Referral Code:</b>  <code>{user['referral_code']}</code>"
    )

# ─────────────────────────────────────────────
# 📩 SUPPORT
# ─────────────────────────────────────────────

def show_support(message: types.Message):
    admin_username = get_setting("admin_username", "@YourAdminUsername")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📩 Contact Admin", url=f"[t.me](https://t.me/{admin_username.lstrip()'@')}"))

    bot.send_message(
        message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  📩 <b>Support</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Need help? Our admin is here for you!\n\n"
        f"👨‍💼 <b>Admin:</b> {admin_username}\n\n"
        f"📋 <b>Common Issues:</b>\n"
        f"  • Task not approved\n"
        f"  • Withdraw not received\n"
        f"  • Account issues\n\n"
        f"<i>Response time: within 24 hours</i>",
        reply_markup=kb
    )

# ─────────────────────────────────────────────
# 📤 WITHDRAW
# ─────────────────────────────────────────────

def start_withdraw(message: types.Message):
    user_id = message.from_user.id
    user    = get_user(user_id)
    min_wd  = float(get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW))

    if user["balance"] < min_wd:
        bot.send_message(
            message.chat.id,
            f"❌ <b>Insufficient Balance</b>\n\n"
            f"💰 <b>Your Balance:</b> <code>{user['balance']:.2f} BDT</code>\n"
            f"🔒 <b>Minimum Withdraw:</b> <code>{min_wd:.2f} BDT</code>\n\n"
            f"Keep completing tasks to reach the minimum! 💪"
        )
        return

    set_user_state(user_id, "awaiting_withdraw_wallet")
    bot.send_message(
        message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  📤 <b>Withdraw Request</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Available Balance:</b> <code>{user['balance']:.2f} BDT</code>\n"
        f"🔒 <b>Minimum:</b> <code>{min_wd:.2f} BDT</code>\n\n"
        f"💳 <b>Method:</b> USDT (BEP20)\n\n"
        f"📝 Please enter your <b>USDT BEP20 wallet address</b>:",
        reply_markup=cancel_keyboard()
    )

def process_withdraw_wallet(message: types.Message):
    user_id = message.from_user.id
    wallet  = message.text.strip()

    if len(wallet) < 10:
        bot.send_message(message.chat.id, "⚠️ Invalid wallet address. Please enter a valid USDT BEP20 address.")
        return

    # Store wallet in temp session using state string
    set_user_state(user_id, f"awaiting_withdraw_amount|{wallet}")
    bot.send_message(
        message.chat.id,
        f"✅ <b>Wallet Address Saved</b>\n\n"
        f"<code>{wallet}</code>\n\n"
        f"💵 Now enter the <b>amount</b> you want to withdraw (in BDT):",
        reply_markup=cancel_keyboard()
    )

def process_withdraw_amount(message: types.Message):
    user_id = message.from_user.id
    state   = get_user_state(user_id)
    user    = get_user(user_id)

    try:
        amount = float(message.text.strip())
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Please enter a valid number.")
        return

    min_wd = float(get_setting("min_withdraw", DEFAULT_MIN_WITHDRAW))
    if amount < min_wd:
        bot.send_message(message.chat.id, f"⚠️ Minimum withdraw is <b>{min_wd:.2f} BDT</b>.")
        return
    if amount > user["balance"]:
        bot.send_message(message.chat.id, f"⚠️ Insufficient balance. Your balance: <b>{user['balance']:.2f} BDT</b>.")
        return

    # Extract wallet from state
    wallet = state.split("|", 1)[1] if "|" in state else "N/A"

    # Save to DB
    conn = get_db()
    conn.execute("""
        INSERT INTO withdrawals (user_id, amount, wallet_addr)
        VALUES (?, ?, ?)
    """, (user_id, amount, wallet))
    wd_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    set_user_state(user_id, None)

    bot.send_message(
        message.chat.id,
        f"✅ <b>Withdraw Request Submitted!</b>\n\n"
        f"💰 <b>Amount:</b> <code>{amount:.2f} BDT</code>\n"
        f"💳 <b>Wallet:</b> <code>{wallet}</code>\n"
        f"📋 <b>Method:</b> USDT (BEP20)\n\n"
        f"⏳ <i>Your request is under review. You'll be notified once processed.</i>",
        reply_markup=main_keyboard()
    )

    # Notify admins
    tg_user = message.from_user
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"📤 <b>New Withdraw Request</b>\n\n"
                f"👤 <b>User:</b> {tg_user.full_name} (@{tg_user.username or 'N/A'})\n"
                f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
                f"💰 <b>Amount:</b> <code>{amount:.2f} BDT</code>\n"
                f"💳 <b>Wallet:</b> <code>{wallet}</code>\n"
                f"📋 <b>Method:</b> USDT (BEP20)\n"
                f"🕐 <b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
                reply_markup=withdraw_action_keyboard(wd_id)
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

# ─────────────────────────────────────────────
# APPROVE / REJECT WITHDRAW (Admin Inline)
# ─────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("approve_wd_") or c.data.startswith("reject_wd_"))
def handle_withdraw_review(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "🚫 Admins only.", show_alert=True)
        return

    parts     = call.data.split("_")
    action    = parts[0]
    wd_id     = int(parts[2])

    conn = get_db()
    wd = conn.execute("SELECT * FROM withdrawals WHERE withdraw_id = ?", (wd_id,)).fetchone()
    if not wd:
        bot.answer_callback_query(call.id, "Request not found.", show_alert=True)
        conn.close()
        return
    wd = dict(wd)

    if wd["status"] != "pending":
        bot.answer_callback_query(call.id, f"Already {wd['status']}.", show_alert=True)
        conn.close()
        return

    if action == "approve":
        # Deduct balance
        user = conn.execute("SELECT balance FROM users WHERE user_id = ?", (wd["user_id"],)).fetchone()
        if user["balance"] < wd["amount"]:
            bot.answer_callback_query(call.id, "❌ User has insufficient balance!", show_alert=True)
            conn.close()
            return

        conn.execute(
            "UPDATE withdrawals SET status = 'approved', reviewed_at = datetime('now') WHERE withdraw_id = ?",
            (wd_id,)
        )
        conn.commit()
        conn.close()

        update_user_balance(wd["user_id"], wd["amount"], add=False)
        bot.answer_callback_query(call.id, f"✅ Withdraw #{wd_id} approved!", show_alert=True)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"✅ Withdraw #{wd_id} <b>Approved</b> — {wd['amount']:.2f} BDT deducted.")

        try:
            bot.send_message(
                wd["user_id"],
                f"🎉 <b>Withdraw Approved!</b>\n\n"
                f"💰 <b>Amount:</b> <code>{wd['amount']:.2f} BDT</code>\n"
                f"💳 <b>Wallet:</b> <code>{wd['wallet_addr']}</code>\n\n"
                f"Your payment is being processed. Thank you! 🙏"
            )
        except Exception:
            pass

        log_admin_action(call.from_user.id, "APPROVE_WITHDRAW", wd["user_id"], f"wd_id={wd_id}, amount={wd['amount']}")

    else:  # reject
        conn.execute(
            "UPDATE withdrawals SET status = 'rejected', reviewed_at = datetime('now') WHERE withdraw_id = ?",
            (wd_id,)
        )
        conn.commit()
        conn.close()

        bot.answer_callback_query(call.id, "❌ Rejected.", show_alert=True)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"❌ Withdraw #{wd_id} <b>Rejected</b>.")

        try:
            bot.send_message(
                wd["user_id"],
                f"❌ <b>Withdraw Rejected</b>\n\n"
                f"💰 <b>Amount:</b> <code>{wd['amount']:.2f} BDT</code>\n\n"
                f"Your withdraw request was rejected. Please contact support for details."
            )
        except Exception:
            pass

        log_admin_action(call.from_user.id, "REJECT_WITHDRAW", wd["user_id"], f"wd_id={wd_id}")

# ─────────────────────────────────────────────
# 🎁 DAILY BONUS
# ─────────────────────────────────────────────

def claim_daily_bonus(message: types.Message):
    user_id = message.from_user.id
    user    = get_user(user_id)
    bonus   = float(get_setting("daily_bonus", DEFAULT_DAILY_BONUS))

    now       = datetime.utcnow()
    last_daily = user.get("last_daily")

    if last_daily:
        last_dt   = datetime.fromisoformat(last_daily)
        next_bonus = last_dt + timedelta(hours=24)
        if now < next_bonus:
            remaining  = next_bonus - now
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            mins       = rem // 60
            bot.send_message(
                message.chat.id,
                f"⏳ <b>Daily Bonus Already Claimed</b>\n\n"
                f"Come back in <b>{hours}h {mins}m</b> to claim your next bonus!\n"
                f"💰 Bonus: <code>{bonus:.2f} BDT</code>"
            )
            return

    # Grant bonus
    conn = get_db()
    conn.execute(
        "UPDATE users SET last_daily = datetime('now'), balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?",
        (bonus, bonus, user_id)
    )
    conn.commit()
    conn.close()

    bot.send_message(
        message.chat.id,
        f"🎁 <b>Daily Bonus Claimed!</b>\n\n"
        f"💰 <b>+{bonus:.2f} BDT</b> has been added to your balance!\n\n"
        f"Come back tomorrow for another bonus! 🔄"
    )

# ─────────────────────────────────────────────
# 👥 REFERRAL
# ─────────────────────────────────────────────

def show_referral(message: types.Message):
    user_id = message.from_user.id
    user    = get_user(user_id)
    bonus   = float(get_setting("referral_bonus", DEFAULT_REFERRAL_BONUS))

    bot_info  = bot.get_me()
    ref_link  = f"[t.me](https://t.me/{bot_info.username}?start={user)['referral_code']}"

    bot.send_message(
        message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  👥 <b>Referral Program</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎁 Earn <b>{bonus:.2f} BDT</b> for every friend you invite!\n\n"
        f"🔗 <b>Your Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 <b>Total Referrals:</b> <code>{user['referral_count']}</code>\n"
        f"💰 <b>Total Earned via Referrals:</b> <code>{user['referral_count'] * bonus:.2f} BDT</code>\n\n"
        f"<i>Share your link and earn when friends join!</i>"
    )

# ─────────────────────────────────────────────
# FORCE JOIN CHECK CALLBACK
# ─────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "check_joined")
def handle_check_joined(call: types.CallbackQuery):
    user_id = call.from_user.id
    missing = check_force_join(user_id)
    if missing:
        bot.answer_callback_query(
            call.id,
            "❌ You haven't joined all required channels yet!",
            show_alert=True
        )
    else:
        bot.answer_callback_query(call.id, "✅ All channels joined!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(
            call.message.chat.id,
            "✅ <b>Verified!</b> You can now use the bot.",
            reply_markup=main_keyboard()
        )

# ─────────────────────────────────────────────
# ADMIN PANEL
# ─────────────────────────────────────────────

@bot.message_handler(commands=["adminpanel"])
@admin_only
def cmd_admin_panel(message: types.Message):
    bot.send_message(
        message.chat.id,
        f"🔐 <b>Admin Panel</b>\n\n"
        f"Welcome, <b>{message.from_user.first_name}</b>!\n"
        f"Use the buttons below to manage the bot.",
        reply_markup=admin_keyboard()
    )

def admin_dashboard(message: types.Message):
    conn = get_db()
    total_users    = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    banned_users   = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    total_tasks    = conn.execute("SELECT COUNT(*) as c FROM task_submissions").fetchone()["c"]
    pending_tasks  = conn.execute("SELECT COUNT(*) as c FROM task_submissions WHERE status = 'pending'").fetchone()["c"]
    approved_tasks = conn.execute("SELECT COUNT(*) as c FROM task_submissions WHERE status = 'approved'").fetchone()["c"]
    total_wds      = conn.execute("SELECT COUNT(*) as c FROM withdrawals").fetchone()["c"]
    pending_wds    = conn.execute("SELECT COUNT(*) as c FROM withdrawals WHERE status = 'pending'").fetchone()["c"]
    total_paid     = conn.execute("SELECT COALESCE(SUM(amount), 0) as s FROM withdrawals WHERE status = 'approved'").fetchone()["s"]
    conn.close()

    maintenance = "🟡 ON" if get_setting("maintenance_mode") == "1" else "🟢 OFF"

    bot.send_message(
        message.chat.id,
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  📊 <b>Bot Dashboard</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Users:</b>       <code>{total_users}</code>\n"
        f"🚫 <b>Banned Users:</b>      <code>{banned_users}</code>\n\n"
        f"📋 <b>Total Submissions:</b> <code>{total_tasks}</code>\n"
        f"⏳ <b>Pending Tasks:</b>     <code>{pending_tasks}</code>\n"
        f"✅ <b>Approved Tasks:</b>    <code>{approved_tasks}</code>\n\n"
        f"📤 <b>Total Withdraws:</b>   <code>{total_wds}</code>\n"
        f"⏳ <b>Pending Withdraws:</b> <code>{pending_wds}</code>\n"
        f"💸 <b>Total Paid Out:</b>    <code>{total_paid:.2f} BDT</code>\n\n"
        f"🔧 <b>Maintenance:</b>       {maintenance}"
    )

def admin_pending_tasks(message: types.Message):
    conn = get_db()
    subs = conn.execute("""
        SELECT ts.*, t.title, t.reward, u.full_name, u.username
        FROM task_submissions ts
        JOIN tasks t ON ts.task_id = t.task_id
        JOIN users u ON ts.user_id = u.user_id
        WHERE ts.status = 'pending'
        ORDER BY ts.submitted_at ASC
        LIMIT 20
    """).fetchall()
    conn.close()

    if not subs:
        bot.send_message(message.chat.id, "✅ <b>No pending task submissions.</b>")
        return

    bot.send_message(message.chat.id, f"📄 <b>Pending Tasks ({len(subs)})</b>")
    for sub in subs:
        sub = dict(sub)
        bot.send_message(
            message.chat.id,
            f"🆔 <b>Sub #{sub['sub_id']}</b>\n"
            f"👤 <b>User:</b> {sub['full_name']} (@{sub['username'] or 'N/A'})\n"
            f"📋 <b>Task:</b> {sub['title']}\n"
            f"👤 <b>Name:</b> {sub['first_name']} {sub['last_name']}\n"
            f"🔗 <b>FB UID:</b> <code>{sub['fb_uid']}</code>\n"
            f"💰 <b>Reward:</b> {sub['reward']:.2f} BDT\n"
            f"🕐 <code>{sub['submitted_at'][:16]}</code>",
            reply_markup=task_action_keyboard(sub["sub_id"])
        )

def admin_pending_withdrawals(message: types.Message):
    conn = get_db()
    wds = conn.execute("""
        SELECT w.*, u.full_name, u.username
        FROM withdrawals w
        JOIN users u ON w.user_id = u.user_id
        WHERE w.status = 'pending'
        ORDER BY w.requested_at ASC
        LIMIT 20
    """).fetchall()
    conn.close()

    if not wds:
        bot.send_message(message.chat.id, "✅ <b>No pending withdraw requests.</b>")
        return

    bot.send_message(message.chat.id, f"📤 <b>Pending Withdrawals ({len(wds)})</b>")
    for wd in wds:
        wd = dict(wd)
        bot.send_message(
            message.chat.id,
            f"🆔 <b>WD #{wd['withdraw_id']}</b>\n"
            f"👤 <b>User:</b> {wd['full_name']} (@{wd['username'] or 'N/A'})\n"
            f"💰 <b>Amount:</b> <code>{wd['amount']:.2f} BDT</code>\n"
            f"💳 <b>Wallet:</b> <code>{wd['wallet_addr']}</code>\n"
            f"📋 <b>Method:</b> {wd['method']}\n"
            f"🕐 <code>{wd['requested_at'][:16]}</code>",
            reply_markup=withdraw_action_keyboard(wd["withdraw_id"])
        )

def admin_manage_tasks(message: types.Message):
    conn = get_db()
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    conn.close()

    text = "📋 <b>Task Management</b>\n\n"
    for t in tasks:
        t = dict(t)
        status = "✅ Active" if t["is_active"] else "❌ Inactive"
        text += (
            f"<b>#{t['task_id']}.</b> {t['title']}\n"
            f"   💰 {t['reward']:.2f} BDT | {status}\n\n"
        )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add Task",    callback_data="admin_add_task"),
        types.InlineKeyboardButton("✏️ Edit Task",  callback_data="admin_edit_task"),
        types.InlineKeyboardButton("❌ Delete Task", callback_data="admin_del_task"),
    )
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ["admin_add_task", "admin_edit_task", "admin_del_task"])
def handle_task_mgmt(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return

    if call.data == "admin_add_task":
        set_user_state(call.from_user.id, "admin_add_task_title")
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "📝 Enter the new task <b>title</b>:", reply_markup=cancel_keyboard())

    elif call.data == "admin_edit_task":
        set_user_state(call.from_user.id, "admin_edit_task_id")
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "✏️ Enter the <b>Task ID</b> to edit:", reply_markup=cancel_keyboard())

    elif call.data == "admin_del_task":
        set_user_state(call.from_user.id, "admin_del_task_id")
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "❌ Enter the <b>Task ID</b> to delete:", reply_markup=cancel_keyboard())

def admin_user_menu(message: types.Message):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔍 Find User",       callback_data="admin_find_user"),
        types.InlineKeyboardButton("💵 Add Balance",     callback_data="admin_add_balance"),
        types.InlineKeyboardButton("➖ Remove Balance",  callback_data="admin_rem_balance"),
        types.InlineKeyboardButton("🚫 Ban User",        callback_data="admin_ban_user"),
        types.InlineKeyboardButton("✅ Unban User",      callback_data="admin_unban_user"),
    )
    bot.send_message(message.chat.id, "👥 <b>User Management</b>\nChoose an action:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_") and c.from_user.id in ADMIN_IDS)
def handle_admin_callbacks(call: types.CallbackQuery):
    data    = call.data
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)

    state_map = {
        "admin_find_user":    ("admin_find_user_id",    "🔍 Enter <b>User ID</b> to look up:"),
        "admin_add_balance":  ("admin_add_balance_id",  "💵 Enter <b>User ID</b> to add balance to:"),
        "admin_rem_balance":  ("admin_rem_balance_id",  "➖ Enter <b>User ID</b> to remove balance from:"),
        "admin_ban_user":     ("admin_ban_user_id",     "🚫 Enter <b>User ID</b> to ban:"),
        "admin_unban_user":   ("admin_unban_user_id",   "✅ Enter <b>User ID</b> to unban:"),
    }
    if data in state_map:
        state, prompt = state_map[data]
        set_user_state(user_id, state)
        bot.send_message(call.message.chat.id, prompt, reply_markup=cancel_keyboard())

def admin_broadcast_start(message: types.Message):
    set_user_state(message.from_user.id, "admin_broadcast_msg")
    bot.send_message(
        message.chat.id,
        "📢 <b>Broadcast Message</b>\n\n"
        "Type the message you want to send to <b>all users</b>:",
        reply_markup=cancel_keyboard()
    )

def admin_settings_menu(message: types.Message):
    min_wd     = get_setting("min_withdraw")
    task_rew   = get_setting("task_reward")
    ref_bonus  = get_setting("referral_bonus")
    daily_b    = get_setting("daily_bonus")
    cooldown   = get_setting("task_cooldown")
    maint      = "🟡 ON" if get_setting("maintenance_mode") == "1" else "🟢 OFF"
    admin_user = get_setting("admin_username")

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💰 Min Withdraw",    callback_data="setting_min_withdraw"),
        types.InlineKeyboardButton("🎁 Task Reward",     callback_data="setting_task_reward"),
        types.InlineKeyboardButton("👥 Referral Bonus",  callback_data="setting_referral_bonus"),
        types.InlineKeyboardButton("🎁 Daily Bonus",     callback_data="setting_daily_bonus"),
        types.InlineKeyboardButton("⏱ Task Cooldown",   callback_data="setting_task_cooldown"),
        types.InlineKeyboardButton("🔧 Toggle Maintenance", callback_data="setting_maintenance"),
        types.InlineKeyboardButton("📢 Add Channel",     callback_data="setting_add_channel"),
        types.InlineKeyboardButton("💾 Backup DB",       callback_data="setting_backup_db"),
    )
    bot.send_message(
        message.chat.id,
        f"⚙️ <b>Bot Settings</b>\n\n"
        f"💰 Min Withdraw: <code>{min_wd} BDT</code>\n"
        f"🎁 Task Reward: <code>{task_rew} BDT</code>\n"
        f"👥 Referral Bonus: <code>{ref_bonus} BDT</code>\n"
        f"🎁 Daily Bonus: <code>{daily_b} BDT</code>\n"
        f"⏱ Cooldown: <code>{cooldown}s</code>\n"
        f"🔧 Maintenance: {maint}\n"
        f"👨‍💼 Admin: {admin_user}",
        reply_markup=kb
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("setting_") and c.from_user.id in ADMIN_IDS)
def handle_settings_callbacks(call: types.CallbackQuery):
    data    = call.data
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)

    if data == "setting_maintenance":
        current = get_setting("maintenance_mode")
        new_val = "0" if current == "1" else "1"
        set_setting("maintenance_mode", new_val)
        status = "🟡 ENABLED" if new_val == "1" else "🟢 DISABLED"
        bot.send_message(call.message.chat.id, f"🔧 <b>Maintenance Mode:</b> {status}")
        log_admin_action(user_id, "TOGGLE_MAINTENANCE", details=f"new_value={new_val}")
        return

    if data == "setting_backup_db":
        try:
            with open(DB_PATH, "rb") as f:
                bot.send_document(call.message.chat.id, f, caption="💾 <b>Database Backup</b>")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Backup failed: {e}")
        return

    setting_map = {
        "setting_min_withdraw":   ("admin_set_min_withdraw",   "💰 Enter new <b>minimum withdraw</b> amount (BDT):"),
        "setting_task_reward":    ("admin_set_task_reward",    "🎁 Enter new <b>task reward</b> amount (BDT):"),
        "setting_referral_bonus": ("admin_set_referral_bonus", "👥 Enter new <b>referral bonus</b> amount (BDT):"),
        "setting_daily_bonus":    ("admin_set_daily_bonus",    "🎁 Enter new <b>daily bonus</b> amount (BDT):"),
        "setting_task_cooldown":  ("admin_set_task_cooldown",  "⏱ Enter new <b>task cooldown</b> in seconds:"),
        "setting_add_channel":    ("admin_add_channel",        "📢 Enter channel username (e.g. @mychannel):"),
    }
    if data in setting_map:
        state, prompt = setting_map[data]
        set_user_state(user_id, state)
        bot.send_message(call.message.chat.id, prompt, reply_markup=cancel_keyboard())

# ─────────────────────────────────────────────
# ADMIN STATE MACHINE
# ─────────────────────────────────────────────

def handle_admin_state(message: types.Message, state: str):
    user_id = message.from_user.id
    text    = message.text.strip()

    # ── Find User ─────────────────────────────
    if state == "admin_find_user_id":
        try:
            target_id = int(text)
            u = get_user(target_id)
            if not u:
                bot.send_message(message.chat.id, "❌ User not found.")
            else:
                bot.send_message(
                    message.chat.id,
                    f"👤 <b>User Info</b>\n\n"
                    f"🆔 ID: <code>{u['user_id']}</code>\n"
                    f"📛 Name: {u['full_name']}\n"
                    f"🔖 Username: @{u['username'] or 'N/A'}\n"
                    f"💰 Balance: <code>{u['balance']:.2f} BDT</code>\n"
                    f"📈 Total Earned: <code>{u['total_earned']:.2f} BDT</code>\n"
                    f"👥 Referrals: {u['referral_count']}\n"
                    f"🚫 Banned: {'Yes' if u['is_banned'] else 'No'}\n"
                    f"📅 Joined: {u['joined_date'][:10]}"
                )
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid User ID.")
        set_user_state(user_id, None)

    # ── Add Balance ────────────────────────────
    elif state == "admin_add_balance_id":
        try:
            target_id = int(text)
            if not get_user(target_id):
                bot.send_message(message.chat.id, "❌ User not found.")
                set_user_state(user_id, None)
                return
            set_user_state(user_id, f"admin_add_balance_amt|{target_id}")
            bot.send_message(message.chat.id, f"💵 Enter amount to <b>add</b> to user <code>{target_id}</code>:")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid User ID.")
            set_user_state(user_id, None)

    elif state.startswith("admin_add_balance_amt|"):
        target_id = int(state.split("|")[1])
        try:
            amount = float(text)
            update_user_balance(target_id, amount)
            bot.send_message(message.chat.id, f"✅ <b>+{amount:.2f} BDT</b> added to user <code>{target_id}</code>.")
            try:
                bot.send_message(target_id, f"💵 <b>Balance Updated!</b>\n\n<b>+{amount:.2f} BDT</b> has been added to your account by admin.")
            except Exception:
                pass
            log_admin_action(user_id, "ADD_BALANCE", target_id, f"amount={amount}")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid amount.")
        set_user_state(user_id, None)

    # ── Remove Balance ─────────────────────────
    elif state == "admin_rem_balance_id":
        try:
            target_id = int(text)
            if not get_user(target_id):
                bot.send_message(message.chat.id, "❌ User not found.")
                set_user_state(user_id, None)
                return
            set_user_state(user_id, f"admin_rem_balance_amt|{target_id}")
            bot.send_message(message.chat.id, f"➖ Enter amount to <b>remove</b> from user <code>{target_id}</code>:")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid User ID.")
            set_user_state(user_id, None)

    elif state.startswith("admin_rem_balance_amt|"):
        target_id = int(state.split("|")[1])
        try:
            amount = float(text)
            update_user_balance(target_id, amount, add=False)
            bot.send_message(message.chat.id, f"✅ <b>-{amount:.2f} BDT</b> removed from user <code>{target_id}</code>.")
            log_admin_action(user_id, "REMOVE_BALANCE", target_id, f"amount={amount}")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid amount.")
        set_user_state(user_id, None)

    # ── Ban / Unban ────────────────────────────
    elif state == "admin_ban_user_id":
        try:
            target_id = int(text)
            conn = get_db()
            conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
            conn.commit()
            conn.close()
            bot.send_message(message.chat.id, f"🚫 User <code>{target_id}</code> has been <b>banned</b>.")
            log_admin_action(user_id, "BAN_USER", target_id)
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid User ID.")
        set_user_state(user_id, None)

    elif state == "admin_unban_user_id":
        try:
            target_id = int(text)
            conn = get_db()
            conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,))
            conn.commit()
            conn.close()
            bot.send_message(message.chat.id, f"✅ User <code>{target_id}</code> has been <b>unbanned</b>.")
            log_admin_action(user_id, "UNBAN_USER", target_id)
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid User ID.")
        set_user_state(user_id, None)

    # ── Broadcast ──────────────────────────────
    elif state == "admin_broadcast_msg":
        set_user_state(user_id, None)
        conn = get_db()
        users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.close()

        total   = len(users)
        success = 0
        failed  = 0

        status_msg = bot.send_message(
            message.chat.id,
            f"📢 <b>Broadcasting...</b>\n0 / {total} sent"
        )

        for i, u in enumerate(users):
            try:
                bot.send_message(u["user_id"], f"📢 <b>Announcement</b>\n\n{text}")
                success += 1
            except Exception:
                failed += 1

            # Update status every 10 messages
            if (i + 1) % 10 == 0:
                try:
                    bot.edit_message_text(
                        f"📢 <b>Broadcasting...</b>\n{i+1} / {total} sent",
                        message.chat.id, status_msg.message_id
                    )
                except Exception:
                    pass
            time.sleep(0.05)  # Rate limiting

        bot.edit_message_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"✅ Sent: {success}\n❌ Failed: {failed}\n📊 Total: {total}",
            message.chat.id, status_msg.message_id
        )
        log_admin_action(user_id, "BROADCAST", details=f"sent={success}, failed={failed}")

    # ── Settings ───────────────────────────────
    elif state == "admin_set_min_withdraw":
        try:
            val = float(text)
            set_setting("min_withdraw", str(val))
            bot.send_message(message.chat.id, f"✅ Min withdraw set to <code>{val:.2f} BDT</code>.")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid number.")
        set_user_state(user_id, None)

    elif state == "admin_set_task_reward":
        try:
            val = float(text)
            set_setting("task_reward", str(val))
            bot.send_message(message.chat.id, f"✅ Task reward set to <code>{val:.2f} BDT</code>.")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid number.")
        set_user_state(user_id, None)

    elif state == "admin_set_referral_bonus":
        try:
            val = float(text)
            set_setting("referral_bonus", str(val))
            bot.send_message(message.chat.id, f"✅ Referral bonus set to <code>{val:.2f} BDT</code>.")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid number.")
        set_user_state(user_id, None)

    elif state == "admin_set_daily_bonus":
        try:
            val = float(text)
            set_setting("daily_bonus", str(val))
            bot.send_message(message.chat.id, f"✅ Daily bonus set to <code>{val:.2f} BDT</code>.")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid number.")
        set_user_state(user_id, None)

    elif state == "admin_set_task_cooldown":
        try:
            val = int(text)
            set_setting("task_cooldown", str(val))
            bot.send_message(message.chat.id, f"✅ Task cooldown set to <code>{val}s</code>.")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid number.")
        set_user_state(user_id, None)

    elif state == "admin_add_channel":
        ch_link = text.strip()
        ch_name = ch_link.lstrip("@")
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO channels (channel_link, channel_name) VALUES (?, ?)",
            (ch_link, ch_name)
        )
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"✅ Channel <b>{ch_link}</b> added to force-join list.")
        log_admin_action(user_id, "ADD_CHANNEL", details=ch_link)
        set_user_state(user_id, None)

    # ── Add Task ───────────────────────────────
    elif state == "admin_add_task_title":
        set_user_state(user_id, f"admin_add_task_reward|{text}")
        bot.send_message(message.chat.id, f"💰 Enter the <b>reward</b> for '{text}' (BDT):")

    elif state.startswith("admin_add_task_reward|"):
        title = state.split("|", 1)[1]
        try:
            reward = float(text)
            conn = get_db()
            conn.execute(
                "INSERT INTO tasks (title, description, reward, password) VALUES (?, ?, ?, ?)",
                (title, f"Complete task: {title}", reward, get_setting("task_password", DEFAULT_TASK_PASSWORD))
            )
            conn.commit()
            conn.close()
            bot.send_message(message.chat.id, f"✅ Task '<b>{title}</b>' added with <code>{reward:.2f} BDT</code> reward.")
            log_admin_action(user_id, "ADD_TASK", details=f"title={title}, reward={reward}")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid reward amount.")
        set_user_state(user_id, None)

    # ── Delete Task ────────────────────────────
    elif state == "admin_del_task_id":
        try:
            task_id = int(text)
            conn = get_db()
            conn.execute("UPDATE tasks SET is_active = 0 WHERE task_id = ?", (task_id,))
            conn.commit()
            conn.close()
            bot.send_message(message.chat.id, f"✅ Task <code>#{task_id}</code> has been <b>deactivated</b>.")
            log_admin_action(user_id, "DELETE_TASK", details=f"task_id={task_id}")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid Task ID.")
        set_user_state(user_id, None)

    # ── Edit Task ──────────────────────────────
    elif state == "admin_edit_task_id":
        try:
            task_id = int(text)
            conn = get_db()
            task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            conn.close()
            if not task:
                bot.send_message(message.chat.id, "❌ Task not found.")
                set_user_state(user_id, None)
                return
            set_user_state(user_id, f"admin_edit_task_reward|{task_id}")
            bot.send_message(
                message.chat.id,
                f"✏️ Editing Task <code>#{task_id}</code>: <b>{task['title']}</b>\n\n"
                f"Enter new <b>reward</b> (current: {task['reward']:.2f} BDT):"
            )
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid Task ID.")
            set_user_state(user_id, None)

    elif state.startswith("admin_edit_task_reward|"):
        task_id = int(state.split("|")[1])
        try:
            reward = float(text)
            conn = get_db()
            conn.execute("UPDATE tasks SET reward = ? WHERE task_id = ?", (reward, task_id))
            conn.commit()
            conn.close()
            bot.send_message(message.chat.id, f"✅ Task <code>#{task_id}</code> reward updated to <code>{reward:.2f} BDT</code>.")
            log_admin_action(user_id, "EDIT_TASK", details=f"task_id={task_id}, new_reward={reward}")
        except ValueError:
            bot.send_message(message.chat.id, "⚠️ Invalid reward.")
        set_user_state(user_id, None)

    else:
        # Unknown state — reset
        set_user_state(user_id, None)

# ─────────────────────────────────────────────
# WITHDRAW STATE ROUTER
# ─────────────────────────────────────────────

# The main handle_text router already calls these by state;
# we just need to distinguish the multi-step withdraw flow.

_original_handle_text = handle_text.__wrapped__ if hasattr(handle_text, "__wrapped__") else handle_text

def _extended_state_router(message: types.Message):
    """Patch to route withdraw multi-step states."""
    user_id = message.from_user.id
    state   = get_user_state(user_id)

    if state and state.startswith("awaiting_withdraw_amount|"):
        return process_withdraw_amount(message)
    if state == "awaiting_withdraw_wallet":
        return process_withdraw_wallet(message)

# Note: We handle these inside handle_text via the state variable —
# see process_withdraw_wallet (sets state to "awaiting_withdraw_amount|wallet")
# and process_withdraw_amount reads that state correctly.

# ─────────────────────────────────────────────
# ANTI-SPAM MIDDLEWARE
# ─────────────────────────────────────────────

_last_message_time: dict = {}
SPAM_INTERVAL = 0.5  # seconds

@bot.middleware_handler(update_types=["message"])
def anti_spam_middleware(bot_instance, message: types.Message):
    user_id = message.from_user.id
    now     = time.time()
    last    = _last_message_time.get(user_id, 0)
    if now - last < SPAM_INTERVAL:
        bot.send_message(message.chat.id, "⏳ <b>Slow down!</b> Please don't spam.")
        return False  # Cancel further processing
    _last_message_time[user_id] = now

# ─────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────

@bot.message_handler(func=lambda m: True)
def fallback_handler(message: types.Message):
    """Catch-all for unhandled messages."""
    bot.send_message(
        message.chat.id,
        "🤔 I didn't understand that. Please use the menu buttons.",
        reply_markup=main_keyboard()
    )

# ─────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────

def main():
    """Main entry point."""
    logger.info("Initializing database...")
    init_db()

    logger.info("Starting Facebook Task Bot...")
    try:
        bot_info = bot.get_me()
        logger.info(f"Bot running: @{bot_info.username} (ID: {bot_info.id})")
    except Exception as e:
        logger.error(f"Failed to connect to Telegram: {e}")
        return

    # Notify admins on startup
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"🤖 <b>Bot Started!</b>\n\n"
                f"✅ Facebook Task Bot is now online.\n"
                f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
            )
        except Exception:
            pass

    logger.info("Polling started. Press Ctrl+C to stop.")
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30,
        logger_level=logging.WARNING
    )

if __name__ == "__main__":
    main()
