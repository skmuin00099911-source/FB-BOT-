"""
Telegram Task Bot
=================
A professional Telegram bot where users complete simple social-media tasks
(currently Facebook tasks) to earn coins, check their balance/profile,
contact support, and request withdrawals.

Everything lives in this single file by design (main.py) so the project
stays easy to deploy on platforms like Railway.

Persistence: a local SQLite database (bot_database.db) is used to store
user data across restarts. SQLite ships with Python's standard library,
so no extra files or services are required.

Author: Generated for production use.
"""

import os
import logging
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")  # without the "@"
DB_PATH = os.getenv("DB_PATH", "bot_database.db")
MINIMUM_WITHDRAW = int(os.getenv("MINIMUM_WITHDRAW", "100"))

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Please set it before starting the bot (see README.md)."
    )

# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Reduce noisy library logs while keeping our own bot logs visible.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("task_bot")

# --------------------------------------------------------------------------- #
#  Task Catalogue
# --------------------------------------------------------------------------- #
# Adding a new task type in the future is as simple as appending a new
# dictionary to this list (or building a new list and merging it in).
# Each task has: id, category, title, description, reward.

TASKS = [
    {
        "id": 1,
        "category": "facebook",
        "emoji": "1️⃣",
        "title": "Like a Facebook Page",
        "description": "Visit the page and tap the Like button.",
        "reward": 5,
    },
    {
        "id": 2,
        "category": "facebook",
        "emoji": "2️⃣",
        "title": "Follow a Facebook Profile",
        "description": "Open the profile and tap Follow.",
        "reward": 7,
    },
    {
        "id": 3,
        "category": "facebook",
        "emoji": "3️⃣",
        "title": "React to a Facebook Post",
        "description": "Open the post and leave a reaction.",
        "reward": 4,
    },
    {
        "id": 4,
        "category": "facebook",
        "emoji": "4️⃣",
        "title": "Join a Facebook Group",
        "description": "Open the group and tap Join.",
        "reward": 8,
    },
]


def get_task_by_id(task_id: int):
    """Helper to fetch a task definition by its id."""
    return next((t for t in TASKS if t["id"] == task_id), None)


# --------------------------------------------------------------------------- #
#  Database Layer (SQLite)
# --------------------------------------------------------------------------- #

@contextmanager
def get_db():
    """Context manager that yields a SQLite connection and always closes it."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create the users table if it doesn't already exist."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                joined_date TEXT,
                coins INTEGER DEFAULT 0,
                completed_tasks INTEGER DEFAULT 0,
                pending_withdraw INTEGER DEFAULT 0
            )
            """
        )
    logger.info("Database ready at %s", DB_PATH)


def get_or_create_user(user_id: int, full_name: str, username: str) -> sqlite3.Row:
    """Fetch a user's row, creating a fresh record on first contact."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if row is None:
            joined_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            conn.execute(
                """
                INSERT INTO users (user_id, full_name, username, joined_date)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, full_name, username, joined_date),
            )
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        else:
            # Keep name/username fresh in case the user changed them.
            conn.execute(
                "UPDATE users SET full_name = ?, username = ? WHERE user_id = ?",
                (full_name, username, user_id),
            )
        return row


def add_coins(user_id: int, amount: int):
    """Credit coins to a user and bump their completed task counter."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE users
            SET coins = coins + ?, completed_tasks = completed_tasks + 1
            WHERE user_id = ?
            """,
            (amount, user_id),
        )


def request_withdraw(user_id: int, amount: int):
    """Move coins from balance into pending withdrawal."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE users
            SET coins = coins - ?, pending_withdraw = pending_withdraw + ?
            WHERE user_id = ?
            """,
            (amount, amount, user_id),
        )


# --------------------------------------------------------------------------- #
#  Keyboards
# --------------------------------------------------------------------------- #

MAIN_MENU_BUTTONS = [
    ["📋 Tasks", "💰 Balance"],
    ["👤 Profile", "📩 Support"],
    ["📤 Withdraw"],
]


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """The persistent reply keyboard shown after /start."""
    return ReplyKeyboardMarkup(
        MAIN_MENU_BUTTONS,
        resize_keyboard=True,
        is_persistent=True,
    )


def tasks_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline buttons shown under the task list."""
    rows = [[InlineKeyboardButton(f"✅ Start: {t['title']}", callback_data=f"task_start_{t['id']}")] for t in TASKS]
    rows.append(
        [
            InlineKeyboardButton("🔄 Refresh Tasks", callback_data="tasks_refresh"),
            InlineKeyboardButton("⬅️ Back", callback_data="go_back"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def support_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👨‍💻 Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}")]]
    )


def withdraw_inline_keyboard(eligible: bool) -> InlineKeyboardMarkup | None:
    if eligible:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("📤 Request Withdraw", callback_data="withdraw_request")]]
        )
    return None


# --------------------------------------------------------------------------- #
#  Message Builders
# --------------------------------------------------------------------------- #

def build_tasks_message() -> str:
    lines = ["📋 *Available Facebook Tasks*\n"]
    for t in TASKS:
        lines.append(f"{t['emoji']} *{t['title']}*")
        lines.append(f"Reward: `{t['reward']} Coins`\n")
    return "\n".join(lines)


def build_balance_message(user_row: sqlite3.Row) -> str:
    return (
        "💰 *Your Balance*\n\n"
        f"Coins: `{user_row['coins']}`\n"
        f"Completed Tasks: `{user_row['completed_tasks']}`\n"
        f"Pending Withdraw: `{user_row['pending_withdraw']}`"
    )


def build_profile_message(user_row: sqlite3.Row, update: Update) -> str:
    tg_user = update.effective_user
    username = f"@{tg_user.username}" if tg_user.username else "Not set"
    return (
        "👤 *User Profile*\n\n"
        f"Name: `{tg_user.full_name}`\n"
        f"Username: {username}\n"
        f"Telegram ID: `{tg_user.id}`\n"
        f"Balance: `{user_row['coins']} Coins`\n"
        f"Joined: `{user_row['joined_date']}`\n"
        f"Total Completed Tasks: `{user_row['completed_tasks']}`"
    )


def build_withdraw_message(user_row: sqlite3.Row) -> tuple[str, bool]:
    eligible = user_row["coins"] >= MINIMUM_WITHDRAW
    status = "✅ Eligible" if eligible else "❌ Not Eligible"
    message = (
        "📤 *Withdraw*\n\n"
        f"Minimum Withdraw:\n`{MINIMUM_WITHDRAW} Coins`\n\n"
        f"Current Balance:\n`{user_row['coins']} Coins`\n\n"
        f"Status:\n{status}"
    )
    return message, eligible


SUPPORT_MESSAGE = (
    "📩 *Support Center*\n\n"
    "If you need help, contact our admin using the button below. "
    "We usually reply within a few hours. 🙏"
)

WELCOME_MESSAGE = (
    "👋 *Welcome to Task Rewards Bot!* 🎉\n\n"
    "Complete simple social media tasks and earn *coins* 💰 that you can "
    "withdraw once you reach the minimum threshold.\n\n"
    "✨ *How it works:*\n"
    "📋 Browse available tasks\n"
    "✅ Complete a task\n"
    "💰 Earn coins instantly\n"
    "📤 Withdraw once eligible\n\n"
    "Use the menu below to get started 👇"
)

# --------------------------------------------------------------------------- #
#  Command Handlers
# --------------------------------------------------------------------------- #

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command: greet the user and show the main menu."""
    try:
        user = update.effective_user
        get_or_create_user(user.id, user.full_name, user.username or "")

        await update.message.reply_text(
            WELCOME_MESSAGE,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logger.exception("Error in /start handler")
        await update.message.reply_text("⚠️ Something went wrong. Please try again.")


# --------------------------------------------------------------------------- #
#  Reply Keyboard Handlers (Text Messages)
# --------------------------------------------------------------------------- #

async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the task list with inline action buttons."""
    try:
        await update.message.reply_text(
            build_tasks_message(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=tasks_inline_keyboard(),
        )
    except Exception:
        logger.exception("Error showing tasks menu")
        await update.message.reply_text("⚠️ Couldn't load tasks right now. Please try again.")


async def balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the user's current balance."""
    try:
        user = update.effective_user
        row = get_or_create_user(user.id, user.full_name, user.username or "")
        await update.message.reply_text(
            build_balance_message(row),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Error showing balance")
        await update.message.reply_text("⚠️ Couldn't load your balance. Please try again.")


async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the user's profile information."""
    try:
        user = update.effective_user
        row = get_or_create_user(user.id, user.full_name, user.username or "")
        await update.message.reply_text(
            build_profile_message(row, update),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Error showing profile")
        await update.message.reply_text("⚠️ Couldn't load your profile. Please try again.")


async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the support center message with an admin contact button."""
    try:
        await update.message.reply_text(
            SUPPORT_MESSAGE,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=support_inline_keyboard(),
        )
    except Exception:
        logger.exception("Error showing support menu")
        await update.message.reply_text("⚠️ Couldn't load support info. Please try again.")


async def withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show withdrawal eligibility and, if eligible, a request button."""
    try:
        user = update.effective_user
        row = get_or_create_user(user.id, user.full_name, user.username or "")
        message, eligible = build_withdraw_message(row)
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=withdraw_inline_keyboard(eligible),
        )
    except Exception:
        logger.exception("Error showing withdraw menu")
        await update.message.reply_text("⚠️ Couldn't load withdraw info. Please try again.")


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for any text that doesn't match a known menu button."""
    await update.message.reply_text(
        "🤔 I didn't understand that. Please use the menu buttons below.",
        reply_markup=main_menu_keyboard(),
    )


# --------------------------------------------------------------------------- #
#  Inline Button Handlers (Callback Queries)
# --------------------------------------------------------------------------- #

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central dispatcher for all inline keyboard button presses."""
    query = update.callback_query
    try:
        await query.answer()  # Acknowledge the tap immediately (fast UX).
        data = query.data
        user = update.effective_user
        row = get_or_create_user(user.id, user.full_name, user.username or "")

        if data == "tasks_refresh":
            await query.edit_message_text(
                build_tasks_message(),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=tasks_inline_keyboard(),
            )

        elif data == "go_back":
            await query.edit_message_text(
                "🏠 *Main Menu*\nUse the buttons below to navigate.",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("task_start_"):
            task_id = int(data.replace("task_start_", ""))
            task = get_task_by_id(task_id)
            if task is None:
                await query.answer("⚠️ Task not found.", show_alert=True)
                return

            add_coins(user.id, task["reward"])
            await query.edit_message_text(
                "✅ *Task Completed!*\n\n"
                f"You completed: *{task['title']}*\n"
                f"Reward earned: `+{task['reward']} Coins` 🎉\n\n"
                "Check your 💰 Balance to see your updated total.",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data == "withdraw_request":
            fresh_row = get_or_create_user(user.id, user.full_name, user.username or "")
            if fresh_row["coins"] < MINIMUM_WITHDRAW:
                await query.answer("❌ You no longer meet the minimum balance.", show_alert=True)
                return

            request_withdraw(user.id, MINIMUM_WITHDRAW)
            await query.edit_message_text(
                "📤 *Withdrawal Requested!*\n\n"
                f"Amount: `{MINIMUM_WITHDRAW} Coins`\n"
                "Status: `Pending Review`\n\n"
                "Our team will process your request shortly. 🙏",
                parse_mode=ParseMode.MARKDOWN,
            )

        else:
            await query.answer("⚠️ Unknown action.", show_alert=True)

    except Exception:
        logger.exception("Error handling callback query")
        try:
            await query.answer("⚠️ Something went wrong. Please try again.", show_alert=True)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Global Error Handler
# --------------------------------------------------------------------------- #

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log all uncaught exceptions so the bot never crashes silently."""
    logger.error("Unhandled exception while processing update: %s", update, exc_info=context.error)


# --------------------------------------------------------------------------- #
#  Application Bootstrap
# --------------------------------------------------------------------------- #

def build_application() -> Application:
    """Create and configure the Telegram Application with all handlers."""
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", start_command))

    # Reply keyboard text buttons (exact-match routing)
    application.add_handler(MessageHandler(filters.Text(["📋 Tasks"]), tasks_menu))
    application.add_handler(MessageHandler(filters.Text(["💰 Balance"]), balance_menu))
    application.add_handler(MessageHandler(filters.Text(["👤 Profile"]), profile_menu))
    application.add_handler(MessageHandler(filters.Text(["📩 Support"]), support_menu))
    application.add_handler(MessageHandler(filters.Text(["📤 Withdraw"]), withdraw_menu))

    # Catch-all for any other text message
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))

    # Inline keyboard callbacks
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Global error handler
    application.add_error_handler(error_handler)

    return application


def main():
    """Entry point: initialise the database and start polling."""
    init_db()
    application = build_application()
    logger.info("Bot is starting (polling mode)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
