
import os
import asyncio
import logging
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import TelegramError
from motor.motor_asyncio import AsyncIOMotorClient


# ==================== CONFIG ====================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004299852013"))
CHANNEL_LINK = os.getenv(
    "CHANNEL_LINK", "https://t.me/AnikotoTv"
).strip()

MINI_APP_LINK = os.getenv(
    "MINI_APP_LINK",
    "https://t.me/AnikotoTv_bot/AnikotoTv"
).strip()

MONGO_URI = (
    os.getenv("MONGO_URI") or os.getenv("DB_URI", "")
).strip()

DATABASE_NAME = os.getenv("DATABASE_NAME", "anikototv_bot")
AUTO_DELETE_DELAY = 120
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

mongo_client = None
db = None
users_collection = None
settings_collection = None


# ==================== KEYBOARDS ====================

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Verify Membership", callback_data="verify")],
    ])


def app_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍥 Open AnikotoTV", url=MINI_APP_LINK)],
    ])


# ==================== DATABASE ====================

async def initialize_database(application):
    global mongo_client, db
    global users_collection, settings_collection
    global CHANNEL_ID, CHANNEL_LINK

    if not MONGO_URI:
        raise RuntimeError(
            "MONGO_URI ya DB_URI Render/Voroa Environment mein set karo."
        )

    mongo_client = AsyncIOMotorClient(
        MONGO_URI,
        serverSelectionTimeoutMS=10000,
    )

    # Verify MongoDB connection
    await mongo_client.admin.command("ping")

    db = mongo_client[DATABASE_NAME]
    users_collection = db["users"]
    settings_collection = db["settings"]

    await users_collection.create_index("user_id", unique=True)

    # Restore saved channel settings after restart
    saved = await settings_collection.find_one(
        {"_id": "channel_settings"}
    )

    if saved:
        CHANNEL_ID = int(saved.get("channel_id", CHANNEL_ID))
        CHANNEL_LINK = saved.get("channel_link", CHANNEL_LINK)

    logger.info("MongoDB connected; persistent storage is ready.")


async def close_database(application):
    global mongo_client

    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None


async def save_user(user):
    if users_collection is None:
        raise RuntimeError("Database is not initialized")

    await users_collection.update_one(
        {"user_id": user.id},
        {
            "$set": {
                "user_id": user.id,
                "first_name": user.first_name or "",
                "username": user.username or "",
            }
        },
        upsert=True,
    )


# ==================== MEMBERSHIP ====================

async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id,
        )
        return member.status in ("member", "administrator", "creator")
    except TelegramError as exc:
        logger.warning("Membership check failed: %s", exc)
        return False


# ==================== AUTO DELETE ====================

async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, message_id = context.job.data

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramError as exc:
        logger.debug("Auto-delete skipped: %s", exc)


def schedule_delete(context, chat_id, message_id):
    context.job_queue.run_once(
        auto_delete_job,
        when=AUTO_DELETE_DELAY,
        data=(chat_id, message_id),
    )


# ==================== START ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    await save_user(user)

    if await check_membership(context.bot, user.id):
        text = (
            "🎌 Welcome to AnikotoTV!\n\n"
            "✅ Channel membership verified.\n"
            "Ab apna Anime Mini App kholo!"
        )
        markup = app_keyboard()
    else:
        text = (
            "🌸 Welcome to AnikotoTV!\n\n"
            "Mini App use karne ke liye pehle hamara channel join karo.\n\n"
            "Join karne ke baad Verify dabao."
        )
        markup = join_keyboard()

    sent = await message.reply_text(text, reply_markup=markup)
    schedule_delete(context, sent.chat_id, sent.message_id)


# ==================== VERIFY ====================

async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = query.from_user
    await save_user(user)

    if await check_membership(context.bot, user.id):
        text = (
            "✅ Membership verified!\n\n"
            "🍥 Ab AnikotoTV Mini App open kar sakte ho."
        )
        markup = app_keyboard()
    else:
        text = (
            "❌ Membership confirm nahi hui.\n\n"
            "Pehle channel join karo, phir Verify dabao."
        )
        markup = join_keyboard()

    if query.message:
        try:
            await query.edit_message_text(
                text,
                reply_markup=markup,
            )
            schedule_delete(
                context,
                query.message.chat_id,
                query.message.message_id,
            )
        except TelegramError as exc:
            logger.warning("Could not edit verification message: %s", exc)


# ==================== OWNER CHECK ====================

async def is_owner(update):
    user = update.effective_user
    return user is not None and OWNER_ID != 0 and user.id == OWNER_ID


# ==================== SET CHANNEL ====================

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHANNEL_ID, CHANNEL_LINK

    message = update.effective_message

    if not await is_owner(update):
        await message.reply_text("⛔ Sirf Owner is command ko use kar sakta hai.")
        return

    if len(context.args) != 2:
        await message.reply_text(
            "Usage:\n/setchannel -1001234567890 https://t.me/MyChannel"
        )
        return

    try:
        new_id = int(context.args[0])
        new_link = context.args[1].strip()

        if not new_link.startswith(("https://t.me/", "http://t.me/")):
            await message.reply_text("❌ Valid Telegram channel link do.")
            return

        # Check that bot can access the target channel
        await context.bot.get_chat(new_id)

        # Persist settings before changing runtime configuration
        await settings_collection.update_one(
            {"_id": "channel_settings"},
            {
                "$set": {
                    "channel_id": new_id,
                    "channel_link": new_link,
                }
            },
            upsert=True,
        )

        CHANNEL_ID = new_id
        CHANNEL_LINK = new_link

        await message.reply_text(
            f"✅ Channel updated!\n\n"
            f"📌 Channel ID: {CHANNEL_ID}\n"
            f"🔗 Channel Link: {CHANNEL_LINK}"
        )

    except ValueError:
        await message.reply_text("❌ Channel ID numeric honi chahiye.")
    except TelegramError as exc:
        logger.warning("Channel update failed: %s", exc)
        await message.reply_text(
            "❌ Bot channel access nahi kar paaya. ID aur admin permissions check karo."
        )


# ==================== BROADCAST ====================

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not await is_owner(update):
        await message.reply_text("⛔ Sirf Owner broadcast kar sakta hai.")
        return

    if not message.reply_to_message:
        await message.reply_text(
            "Kisi message ka reply karke /broadcast likho."
        )
        return

    total = await users_collection.count_documents({})
    success = 0
    failed = 0

    status = await message.reply_text(
        f"🚀 Broadcast shuru!\nTotal users: {total}"
    )

    cursor = users_collection.find({}, {"user_id": 1, "_id": 0})

    async for record in cursor:
        user_id = record["user_id"]

        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.reply_to_message.chat_id,
                message_id=message.reply_to_message.message_id,
            )
            success += 1

        except TelegramError:
            failed += 1

        # Avoid sending messages too quickly
        await asyncio.sleep(0.05)

    await status.edit_text(
        "✅ Broadcast completed!\n\n"
        f"🎯 Total users: {total}\n"
        f"🟢 Successful: {success}\n"
        f"🔴 Failed/Blocked: {failed}"
    )


# ==================== HEALTH SERVER ====================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"AnikotoTV bot is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server started on port %s", PORT)


# ==================== MAIN ====================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable missing.")

    if OWNER_ID == 0:
        raise RuntimeError("OWNER_ID environment variable missing or invalid.")

    start_health_server()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(initialize_database)
        .post_shutdown(close_database)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("setchannel", set_channel))
    application.add_handler(
        CallbackQueryHandler(verify, pattern=r"^verify$")
    )

    logger.info("AnikotoTV bot is starting...")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
            
