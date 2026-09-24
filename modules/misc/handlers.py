# =============================================================================
# Module: Misc
# Path: modules/misc/handlers.py
# Description: Message and callback handlers for the Misc module. Provides routing
#              and command execution.
# Scope: private | channel | group
# =============================================================================

from aiogram import Router, F, Bot, types
from aiogram.types import Message
from aiogram.filters import Command
from utils.permissions import owner_only
from utils.helpers import reply_to_owner, get_target_user
from utils import admin_cache
from utils.help_registry import HelpRegistry
from database.mongodb import db
from utils.formatter import format_message
from utils.logger import log
import html
import time

router = Router()

# Register Help (General)
HelpRegistry.register(
    "Misc",
    "misc",
    "<b>Misc</b>\n\n"
    "<i>Useful utility commands.</i>\n\n"
    "<b>Commands:</b>\n"
    "- /id: Get this chat's ID (or your user ID in PM).\n\n",
    supported_chat_types=["group", "supergroup", "channel"]
)

# Register Help (Dev)
HelpRegistry.register(
    "Misc",
    "dev_misc",
    "<b>Misc</b>\n\n"
    "<i>Restricted utility commands for bot owner.</i>\n\n"
    "<b>Commands:</b>\n"
    "- /echo <code>&lt;text&gt;</code>, /say: Echo message.\n"
    "- /broadcast <code>&lt;text&gt;</code>: Broadcast to managed groups.\n"
    "- /admincache: Clear admin cache.\n"
    "- /privacycheck: Check permission visibility.\n"
    "- /userinfo: Get stored user data.\n"
    "- /chatreport: Export all active chats + subscribed feeds as a .txt file.\n"
    "- /ping: Check bot response time.\n\n",
    supported_chat_types=["dev"]
)
@owner_only
async def echo_command(message: types.Message, bot: Bot):
    text_to_send = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if not text_to_send: return
    formatted = await format_message(text_to_send, message.from_user, message.chat, bot=bot)
    await bot.send_message(message.chat.id, formatted.text, reply_markup=formatted.reply_markup, parse_mode="HTML", **formatted.api_flags)
    try: await message.delete()
    except Exception: pass

@router.message(Command("broadcast", prefix="!/"), F.chat.type == "private")
@owner_only
async def broadcast_command(message: types.Message, bot: Bot):
    text_to_broadcast = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if not text_to_broadcast:
        await message.reply("Usage: <code>/broadcast &lt;text&gt;</code>", parse_mode="HTML")
        return
    managed_groups = await db.get_managed_groups(message.from_user.id)
    if not managed_groups:
        await message.reply("You are not managing any groups.", parse_mode="HTML")
        return
    successful, failed = 0, 0
    for group in managed_groups:
        try:
            chat_obj = await bot.get_chat(group['chat_id'])
            formatted = await format_message(text_to_broadcast, message.from_user, chat_obj, bot=bot)
            await bot.send_message(group['chat_id'], formatted.text, reply_markup=formatted.reply_markup, parse_mode="HTML", **formatted.api_flags)
            successful += 1
        except Exception as e:
            failed += 1
            log.error(f"Failed broadcast to {group['chat_id']}: {e}")
    await message.reply(f"📢 Broadcast complete. ✅ Sent: <b>{successful}</b>, ❌ Failed: <b>{failed}</b>.", parse_mode="HTML")


# --- Existing Misc Commands ---

# --- Privacy Check ---
@router.message(Command("privacycheck"), F.chat.type.in_({"private", "group", "supergroup"}))
@owner_only
async def privacy_check_command(message: Message, bot: Bot):
    bot_user = await bot.get_me()
    if bot_user.can_read_all_group_messages:
        reply_text = "✅ <b>Privacy mode is OFF.</b>\nI can see all messages in groups."
    else:
        reply_text = (
            "❌ <b>Privacy mode is ON.</b>\n\n"
            "I can only see commands and direct mentions. Features like Night Mode won't work.\n\n"
            "<b>To fix:</b> Go to @BotFather > /mybots > Settings > Group Privacy > Turn off."
        )
    await reply_to_owner(message, reply_text, parse_mode="HTML")

# --- 1. Optimized /id Command ---
@router.message(Command("id"), F.chat.type.in_({"private", "group", "supergroup"}))
@owner_only
async def id_command(message: Message):
    if message.chat.type == "private":
        text = f"👤 <b>Your User ID:</b> <code>{message.from_user.id}</code>"
    else:
        text = f"👥 <b>Chat ID:</b> <code>{message.chat.id}</code>"
    await reply_to_owner(message, text, parse_mode="HTML")

@router.channel_post(Command("id"))
@owner_only
async def id_command_channel(message: Message):
    # Channel posts arrive as a separate update type from regular messages —
    # this handler is what makes /id actually work when posted in a channel.
    text = f"📢 <b>Channel ID:</b> <code>{message.chat.id}</code>"
    await reply_to_owner(message, text, parse_mode="HTML")

# --- Ping ---
@router.message(Command("ping"), F.chat.type == "private")
@owner_only
async def ping_command(message: Message, bot: Bot):
    start = time.monotonic()
    sent = await message.reply("🏓 Pong!")
    rtt = (time.monotonic() - start) * 1000  # ms

    # Second pass: edit with the measured RTT
    await sent.edit_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"<b>Response time:</b> <code>{rtt:.1f} ms</code>",
        parse_mode="HTML"
    )

# --- Admin Cache ---
@router.message(Command("admincache"), F.chat.type.in_({"private", "group", "supergroup"}))
@owner_only
async def admincache_command(message: Message):
    admin_cache.clear_chat_cache(message.chat.id)
    await reply_to_owner(message, f"Admin cache for {html.escape(message.chat.title)} has been cleared.")

# --- User Info Command ---
@router.message(Command("userinfo"), F.chat.type == "private")
@owner_only
async def userinfo_command(message: Message, bot: Bot):
    target_user = None

    # 1. Resolve Target
    # Check for arguments first
    args = message.text.split()
    if len(args) > 1:
        # Try resolving via helper (checks mentions, IDs, Usernames)
        target_user = await get_target_user(bot, message, args)

        # If helper failed but we have an ID, try direct lookup (Bot might know them from elsewhere)
        if not target_user and args[1].isdigit():
            try:
                chat = await bot.get_chat(int(args[1]))
                # Convert Chat to User-like object if it's a private user
                if chat.type == "private":
                    target_user = types.User(
                        id=chat.id,
                        is_bot=False,
                        first_name=chat.first_name,
                        last_name=chat.last_name,
                        username=chat.username
                    )
            except Exception:
                pass

    # Check for reply if no args resolved
    if not target_user and message.reply_to_message:
        target_user = message.reply_to_message.from_user

    if not target_user:
        await reply_to_owner(message, "⚠️ <b>Usage:</b> <code>/userinfo &lt;ID/Username&gt;</code> or reply to a message.")
        return

    # 2. Build Report
    username_txt = f"@{target_user.username}" if target_user.username else "N/A"

    text = (
        f"<b>👤 User Info</b>\n\n"
        f"<b>Full Name:</b> {html.escape(target_user.full_name)}\n"
        f"<b>ID:</b> <code>{target_user.id}</code>\n"
        f"<b>Username:</b> {username_txt}"
    )

    await reply_to_owner(message, text, parse_mode="HTML")
