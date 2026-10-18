
import os
import asyncio
import logging
import random
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================ CONFIGURATION ================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004299852013"))
CHANNEL_LINK = os.getenv(
    "CHANNEL_LINK", "https://t.me/AnikotoTv"
).strip()

MINI_APP_LINK = os.getenv(
    "MINI_APP_LINK",
    "https://t.me/AnikotoTv_bot/AnikotoTv",
).strip()

MONGO_URI = (
    os.getenv("MONGO_URI") or os.getenv("DB_URI", "")
).strip()

DATABASE_NAME = os.getenv("DATABASE_NAME", "anikototv_bot")
PORT = int(os.getenv("PORT", "10000"))
AUTO_DELETE_DELAY = 120

# Optional fallback photo IDs or direct image URLs
PICS = [
    item.strip()
    for item in os.getenv("PICS", "").split(",")
    if item.strip()
]

PHOTO_KEY = "start_photo"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

mongo_client = None
users_collection = None
settings_collection = None


# ================ KEYBOARDS ================

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton(
            "✅ Verify Membership", callback_data="verify"
        )],
    ])


def app_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🍥 Open AnikotoTV", url=MINI_APP_LINK
        )],
    ])


# ================ DATABASE ================

async def initialize_database(application):
    global mongo_client, users_collection, settings_collection
    global CHANNEL_ID, CHANNEL_LINK

    if not MONGO_URI:
        raise RuntimeError(
            "Set MONGO_URI or DB_URI in your hosting environment."
        )

    mongo_client = AsyncIOMotorClient(
        MONGO_URI,
        serverSelectionTimeoutMS=15000,
    )

    await mongo_client.admin.command("ping")

    db = mongo_client[DATABASE_NAME]
    users_collection = db["users"]
    settings_collection = db["settings"]

    await users_collection.create_index("user_id", unique=True)

    saved = await settings_collection.find_one(
        {"_id": "channel_settings"}
    )

    if saved:
        CHANNEL_ID = int(saved.get("channel_id", CHANNEL_ID))
        CHANNEL_LINK = saved.get("channel_link", CHANNEL_LINK)

    logger.info("MongoDB connected successfully.")


async def close_database(application):
    global mongo_client

    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None


async def save_user(user):
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


# ================ MEMBERSHIP CHECK ================

async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id,
        )

        if member.status in ("member", "administrator", "creator"):
            return True

        if member.status == "restricted":
            return bool(getattr(member, "is_member", False))

        return False

    except TelegramError as exc:
        logger.warning("Membership check failed: %s", exc)
        return False


# ================ AUTO DELETE ================

async def auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, message_id = context.job.data

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramError as exc:
        logger.warning(
            "Could not delete message %s in chat %s: %s",
            message_id, chat_id, exc,
        )


def schedule_delete(context, chat_id, message_id):
    if context.job_queue is None:
        logger.warning("JobQueue unavailable; auto-delete not scheduled.")
        return

    context.job_queue.run_once(
        auto_delete_job,
        when=AUTO_DELETE_DELAY,
        data=(chat_id, message_id),
    )


# ================ START COMMAND ================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    await save_user(user)
    is_member = await check_membership(context.bot, user.id)

    if is_member:
        text = (
            "🎌 Welcome to AnikotoTV!\n\n"
            "✅ Your channel membership is verified.\n\n"
            "Tap below to open our Anime Mini App!"
        )
        markup = app_keyboard()
    else:
        text = (
            "🌸 Welcome to AnikotoTV!\n\n"
            "To access the AnikotoTV Mini App, please join "
            "our channel first.\n\n"
            "After joining, tap 'Verify Membership' below."
        )
        markup = join_keyboard()

    try:
        saved_photo = await settings_collection.find_one(
            {"_id": PHOTO_KEY}
        )

        if saved_photo and saved_photo.get("file_id"):
            photo = saved_photo["file_id"]
        elif PICS:
            photo = random.choice(PICS)
        else:
            photo = None

        if photo:
            sent = await message.reply_photo(
                photo=photo,
                caption=text,
                reply_markup=markup,
            )
        else:
            sent = await message.reply_text(
                text,
                reply_markup=markup,
            )

        schedule_delete(context, sent.chat_id, sent.message_id)
        schedule_delete(context, message.chat_id, message.message_id)

    except TelegramError:
        logger.exception("Failed to send start response")


# ================ VERIFY BUTTON ================

async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not query:
        return

    await query.answer()
    await save_user(query.from_user)

    is_member = await check_membership(
        context.bot, query.from_user.id
    )

    if is_member:
        text = (
            "✅ Membership verified successfully!\n\n"
            "🍥 You can now open the AnikotoTV Mini App."
        )
        markup = app_keyboard()
    else:
        text = (
            "❌ Membership could not be verified.\n\n"
            "Please join our channel first, then tap "
            "'Verify Membership' again."
        )
        markup = join_keyboard()

    if not query.message:
        return

    try:
        if query.message.photo:
            await query.edit_message_caption(
                caption=text, reply_markup=markup
            )
        else:
            await query.edit_message_text(
                text=text, reply_markup=markup
            )

        schedule_delete(
            context,
            query.message.chat_id,
            query.message.message_id,
        )

    except BadRequest as exc:
        logger.warning("Verification edit failed: %s", exc)
    except TelegramError:
        logger.exception("Verification failed")


# ================ OWNER CHECK ================

async def is_owner(update):
    user = update.effective_user
    return user is not None and OWNER_ID != 0 and user.id == OWNER_ID


# ================ SET START PHOTO ================

async def set_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message:
        return

    if not await is_owner(update):
        await message.reply_text(
            "⛔ Only the bot owner can change the start photo."
        )
        return

    if not message.photo:
        await message.reply_text(
            "❌ Please send a photo directly to this bot."
        )
        return

    photo_id = message.photo[-1].file_id

    await settings_collection.update_one(
        {"_id": PHOTO_KEY},
        {"$set": {"file_id": photo_id}},
        upsert=True,
    )

    await message.reply_text(
        "✅ Start photo saved successfully!\n"
        "Ab /start par nayi photo dikhegi."
    )


async def remove_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message:
        return

    if not await is_owner(update):
        await message.reply_text(
            "⛔ Only the bot owner can remove the start photo."
        )
        return

    await settings_collection.delete_one({"_id": PHOTO_KEY})

    if PICS:
        reply = (
            "✅ Custom photo removed. Ab PICS se photo use hogi."
        )
    else:
        reply = "✅ Custom photo removed. Ab /start par text dikhega."

    await message.reply_text(reply)


# ================ SET CHANNEL ================

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHANNEL_ID, CHANNEL_LINK

    message = update.effective_message

    if not message:
        return

    if not await is_owner(update):
        await message.reply_text(
            "⛔ Only the bot owner can use this command."
        )
        return

    if len(context.args) != 2:
        await message.reply_text(
            "Usage:\n"
            "/setchannel -1001234567890 https://t.me/MyChannel"
        )
        return

    try:
        new_id = int(context.args[0])
        new_link = context.args[1].strip()

        if not new_link.startswith(
            ("https://t.me/", "http://t.me/")
        ):
            await message.reply_text(
                "❌ Please provide a valid Telegram channel link."
            )
            return

        await context.bot.get_chat(new_id)

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
            "✅ Channel settings updated successfully!\n\n"
            f"Channel ID: {CHANNEL_ID}\n"
            f"Channel link: {CHANNEL_LINK}"
        )

    except ValueError:
        await message.reply_text(
            "❌ The channel ID must be numeric."
        )
    except TelegramError:
        logger.exception("Channel update failed")
        await message.reply_text(
            "❌ Could not access that channel. "
            "Check the channel ID and bot permissions."
        )


# ================ BROADCAST ================

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message:
        return

    if not await is_owner(update):
        await message.reply_text(
            "⛔ Only the bot owner can broadcast messages."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "Reply to a message and send /broadcast."
        )
        return

    total = await users_collection.count_documents({})
    success = 0
    failed = 0

    status = await message.reply_text(
        f"🚀 Broadcast started!\nTotal users: {total}"
    )

    cursor = users_collection.find(
        {}, {"user_id": 1, "_id": 0}
    )

    async for record in cursor:
        try:
            await context.bot.copy_message(
                chat_id=record["user_id"],
                from_chat_id=message.reply_to_message.chat_id,
                message_id=message.reply_to_message.message_id,
            )
            success += 1
        except TelegramError as exc:
            failed += 1
            logger.debug("Broadcast failed: %s", exc)

        await asyncio.sleep(0.05)

    await status.edit_text(
        "✅ Broadcast completed!\n\n"
        f"🎯 Total users: {total}\n"
        f"🟢 Successful: {success}\n"
        f"🔴 Failed: {failed}"
    )


# ================ HEALTH SERVER ================

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
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server started on port %s", PORT)


# ================ MAIN ================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")

    if OWNER_ID == 0:
        raise RuntimeError("OWNER_ID is missing or invalid.")

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
    application.add_handler(CommandHandler("removephoto", remove_photo))
    application.add_handler(
        CallbackQueryHandler(verify, pattern=r"^verify$")
    )

    # Owner check is inside set_photo; normal users cannot change the photo.
    application.add_handler(MessageHandler(filters.PHOTO, set_photo))

    logger.info("AnikotoTV bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
    
