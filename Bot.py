
import os
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# BOT_TOKEN Render Environment Variables mein set karo
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Channel aur Mini App details
CHANNEL_ID = -1004299852013
CHANNEL_LINK = "https://t.me/AnikotoTv"
MINI_APP_LINK = "https://t.me/AnikotoTv_bot/AnikotoTv"


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Verify Membership", callback_data="verify")],
    ])


def app_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍥 Open AnikotoTV", url=MINI_APP_LINK)],
    ])


async def check_membership(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> bool:
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id,
        )
        return member.status in ("member", "administrator", "creator")
    except Exception:
        logger.exception(
            "Membership check failed. Check CHANNEL_ID and make the bot an administrator in the channel."
        )
        return False


async def start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    message = update.effective_message

    if user is None or message is None:
        return

    if await check_membership(context, user.id):
        await message.reply_text(
            "🎌 Welcome to AnikotoTV!\n\n"
            "✅ Channel membership verified.\n"
            "Ab apna Anime Mini App kholo!",
            reply_markup=app_keyboard(),
        )
    else:
        await message.reply_text(
            "🌸 Welcome to AnikotoTV!\n\n"
            "Mini App use karne ke liye pehle hamara channel join karo.\n\n"
            "Join karne ke baad Verify dabao.",
            reply_markup=join_keyboard(),
        )


async def verify(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    if await check_membership(context, query.from_user.id):
        await query.edit_message_text(
            "✅ Membership verified!\n\n"
            "🍥 Ab AnikotoTV Mini App open kar sakte ho.",
            reply_markup=app_keyboard(),
        )
    else:
        await query.edit_message_text(
            "❌ Membership confirm nahi hui.\n\n"
            "Pehle channel join karo, phir Verify dabao.",
            reply_markup=join_keyboard(),
        )


# Render ke liye chhota HTTP server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"AnikotoTV bot is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server() -> None:
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health server listening on port %s", port)
    Thread(target=server.serve_forever, daemon=True).start()


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN missing. Render ke Environment section mein BOT_TOKEN set karo."
        )

    start_health_server()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(verify, pattern=r"^verify$")
    )

    logger.info("AnikotoTV membership verification bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
  
