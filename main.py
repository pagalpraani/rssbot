# =============================================================================
# Module: Main
# Path: main.py
# Description: Main application entry point. Initializes the bot, database, and
#              registers all routers.
# =============================================================================


import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import aiohttp
import uvloop
from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from database.mongodb import db
from utils.rate_limit_middleware import MessageRateLimitMiddleware, CallbackRateLimitMiddleware
from utils.logger import log
import config
import modules
from modules.marginals.middleware import MarginalsMiddleware
from modules.blocklist.middleware import BlocklistMiddleware
from modules.replacements.middleware import ReplacementsMiddleware
from utils import scheduler
from utils.log_manager import LogManager
from utils import settings_cache

# Configuration
WEBHOOK_PATH = "/webhook"
# Ensure you add WEBHOOK_URL to your Render Environment Variables!
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

async def on_startup(bot: Bot):
    """
    Actions to perform on bot startup.
    """
    await db.connect()
    await db.setup_indexes()

    LogManager.set_bot(bot)
    scheduler.set_bot_instance(bot)

    bot_self = await bot.get_me()
    settings_cache.set_bot_self(bot_self)

    scheduler.start_rss_polling()

    if WEBHOOK_URL:
        # Safely construct the full webhook URL
        webhook_endpoint = f"{WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"
        log.info(f"Setting webhook to: {webhook_endpoint}")

        await bot.set_webhook(
            url=webhook_endpoint,
            allowed_updates=["message", "channel_post", "edited_channel_post", "callback_query", "my_chat_member", "chat_member"],
            drop_pending_updates=True # Forces Telegram to clear stuck updates
        )
        log.info("Webhook set successfully with drop_pending_updates=True.")
    else:
        log.error("WEBHOOK_URL environment variable is missing!")

    log.info("Bot started in Webhook mode...")

async def on_shutdown(bot: Bot):
    """
    Actions to perform on bot shutdown.
    """
    from modules.rss.service import close_http_session
    scheduler.stop_rss_polling()
    await close_http_session()
    await db.close()
    log.info("Bot stopped.")

def main():
    """
    Main function to start the bot with Webhooks.
    """
    # Custom session with generous timeouts for audio/file uploads.
    # Default sock_read of 60s was too short for podcast-sized MP3s (5-10 MB)
    # over Telegram's upload endpoint, causing repeated Request timeout errors.
    session = AiohttpSession(
        timeout=aiohttp.ClientTimeout(
            total=300,      # 5 min total — covers any podcast-sized upload
            connect=10,     # fail fast if Telegram is unreachable
            sock_read=300,  # key setting — time to receive response after upload completes
        )
    )
    bot = Bot(token=config.BOT_TOKEN, session=session)
    dp = Dispatcher()

    @dp.error()
    async def global_error_handler(event: ErrorEvent):
        """
        Last-resort safety net: catches any exception a handler didn't
        already deal with, so a bug degrades to a visible error message
        instead of the update silently vanishing.
        """
        log.exception(f"Unhandled exception: {event.exception}")
        try:
            await LogManager.log_dev(
                "ERROR", "Unhandled",
                f"{type(event.exception).__name__}: {event.exception}"
            )
        except Exception:
            pass

        try:
            if event.update.callback_query:
                await event.update.callback_query.answer(
                    "⚠️ Something went wrong. Check the bot logs.", show_alert=True
                )
            elif event.update.message:
                await event.update.message.reply("⚠️ Something went wrong processing that. Check the bot logs.")
        except Exception:
            pass

        return True


    # Global rate limiting — runs before any other middleware or handler
    dp.message.outer_middleware(MessageRateLimitMiddleware())
    dp.callback_query.outer_middleware(CallbackRateLimitMiddleware())

    dp.channel_post.outer_middleware(BlocklistMiddleware())
    dp.channel_post.outer_middleware(ReplacementsMiddleware())
    dp.channel_post.outer_middleware(MarginalsMiddleware())

    for module_name in modules.__all__:
        module = getattr(modules, module_name)
        if hasattr(module, "router"):
            dp.include_router(module.router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    async def health_check(request):
        return web.Response(text="ok", status=200)

    app.router.add_get("/health", health_check)

    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port, loop=uvloop.new_event_loop())

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped manually.")
