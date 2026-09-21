# =============================================================================
# Module: Misc
# Path: utilitybot/modules/misc/handlers.py
# Description: Message and callback handlers for the Misc module. Provides routing
#              and command execution.
# Scope: private | channel | group
# =============================================================================

from aiogram import Router, F, Bot, types
from aiogram.types import Message
from aiogram.filters import Command
from ...utils.permissions import owner_only
from ...utils.helpers import reply_to_owner, get_target_user
from ...utils import admin_cache
from ...utils.help_registry import HelpRegistry
from ...database.mongodb import db
from ...utils.formatter import format_message
from ...utils.logger import log
from ...utils.settings_layout import SettingsRegistry
from ...utils.permissions import is_admin
from ... import config
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
    "- /setrules <code>&lt;url&gt;</code>: Set rules URL.\n"
    "- /id: Get Chat/User IDs.\n"
    "- /info: Get detailed User/Chat info.\n"
    "- /markdownhelp: Show supported formatting syntax.\n\n",
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
    "- /ping: Check bot response time.\n\n",
    supported_chat_types=["dev"]
)

# --- Settings Schema ---
# Moved from Admin module
SETTINGS_SCHEMA = {
    "name": "General",
    "db_collection": "settings",
    "category": "Other",
    "order": 99,
    "icon": "⚙️",
    "fields": {
        "rules_url": {
            "type": "input",
            "label": "Rules URL",
            "description": "The URL for the chat's rules.",
            "icon": "🔗"
        }
    }
}
SettingsRegistry.register_module("general", SETTINGS_SCHEMA)


# --- Commands Moved from Admin Module ---

@router.message(Command("setrules", prefix="!/"), F.chat.type.in_({"group", "supergroup"}))
@owner_only
async def set_rules_command(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await db.update_settings(message.chat.id, {"rules_url": None})
        await reply_to_owner(message, "✅ Rules URL cleared.")
    else:
        rules_url = args[1]
        await db.update_settings(message.chat.id, {"rules_url": rules_url})
        await reply_to_owner(message, f"✅ Rules URL set to: {html.escape(rules_url)}")

@router.message(Command("echo", "say", prefix="!/"), F.chat.type.in_({"private", "group", "supergroup"}))
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

# --- Markdown Help ---
@router.message(Command("markdownhelp"), F.chat.type == "private")
@owner_only
async def markdown_help_command(message: Message):
    text = (
        "<b>📝 Markdown Formatting Guide</b>\n\n"
        "<b>Supported Styles:</b>\n"
        "• <code>*bold*</code> → <b>bold</b>\n"
        "• <code>_italic_</code> → <i>italic</i>\n"
        "• <code>__underline__</code> → <u>underline</u>\n"
        "• <code>~strike~</code> → <s>strike</s>\n"
        "• <code>||spoiler||</code> → <tg-spoiler>spoiler</tg-spoiler>\n"
        "• <code>`code`</code> → <code>code</code>\n"
        "• <code>```language\\ncode```</code> → Pre-formatted code block\n"
        "• <code>[text](url)</code> → <a href='https://example.com'>text</a>\n"
        "• <code>> quote</code> → Single line quote\n\n"
        "<b>Special Syntax:</b>\n"
        "• <b>Multiline Quotes:</b>\n"
        "  <code>**&gt; Title\n&gt; Line 1\n&gt; Last Line</code>\n"
        "  <i>Creates a collapsible quote block.</i>\n\n"
        "• <b>Buttons & Styles:</b>\n"
        "  <code>[Label](buttonurl://link.com)</code>\n"
        "  <code>[Note](buttonurl://#notename)</code>\n"
        "  Add <code>:same</code> at the end to keep on the same row.\n"
        "  <b>Colors:</b> Use <code>#primary</code> (blue), <code>#success</code> (green), or <code>#danger</code> (red).\n"
        "  <i>Example:</i> <code>[Join](buttonurl#primary://t.me/c)</code>\n\n"
        "<b>Variables:</b>\n"
        "• <code>{first}</code>, <code>{last}</code>, <code>{fullname}</code>\n"
        "• <code>{username}</code>, <code>{mention}</code>, <code>{id}</code>\n"
        "• <code>{chatname}</code>, <code>{count}</code>, <code>{date}</code>\n"
        "• <code>{channels}</code> (Force Subscribe list)\n"
        "• <code>%%%</code> (Random Content Separator)\n\n"
        "<b>Flags:</b>\n"
        "• <code>{preview}</code>, <code>{preview:top}</code>, <code>{nonotif}</code>\n"
        "• <code>{protect}</code>, <code>{mediaspoiler}</code>, <code>{pin}</code>\n"
        "• <code>{noheader}</code>, <code>{nofooter}</code>, <code>{nomarginals}</code>\n"
        "• <code>{rules}</code>, <code>{rules:same}</code>"
    )
    await reply_to_owner(message, text, parse_mode="HTML")

# --- 1. Optimized /id Command ---
@router.message(Command("id"), F.chat.type.in_({"private", "group", "supergroup"}))
@owner_only
async def id_command(message: Message):
    chat_id = message.chat.id

    if message.reply_to_message:
        text = (
            f"Chat ID: <code>{chat_id}</code>\n"
            f"Replied ID: <code>{message.reply_to_message.from_user.id}</code>"
        )
    else:
        user_id = message.from_user.id
        text = (
            f"Chat ID: <code>{chat_id}</code>\n"
            f"My ID: <code>{user_id}</code>"
        )

    await reply_to_owner(message, text, parse_mode="HTML")

# --- 2. Consolidated /info Command ---
@router.message(Command("info"), F.chat.type.in_({"private", "group", "supergroup"}))
async def info_command(message: Message, bot: Bot):
    chat = message.chat
    target_user = message.reply_to_message.from_user if message.reply_to_message else message.from_user

    # Allow args resolution
    args = message.text.split()
    if len(args) > 1:
        resolved = await get_target_user(bot, message, args)
        if resolved: target_user = resolved

    # /info exposes chat + member details, so require the caller to be an
    # admin of the chat (or the bot owner) to prevent abuse.
    if chat.type != "private":
        is_authorized = await is_admin(bot, message.chat.id, message.from_user.id)
        if not is_authorized:
            return

    # --- Section 1: Chat Info ---
    try:
        count = await bot.get_chat_member_count(chat.id)
    except Exception:
        count = "Unknown"

    chat_text = (
        f"<b>📂 Chat Info</b>\n"
        f"<b>Title:</b> {html.escape(chat.title or 'Private Chat')}\n"
        f"<b>ID:</b> <code>{chat.id}</code>\n"
        f"<b>Type:</b> {chat.type.capitalize()}\n"
        f"<b>Members:</b> {count}"
    )

    # --- Section 2: User Info ---
    username_text = f"@{target_user.username}" if target_user.username else "N/A"
    user_text = (
        f"\n\n<b>👤 User Info</b>\n"
        f"<b>Name:</b> <a href='tg://user?id={target_user.id}'>{html.escape(target_user.full_name)}</a>\n"
        f"<b>ID:</b> <code>{target_user.id}</code>\n"
        f"<b>Username:</b> {username_text}"
    )

    if chat.type != 'private':
        try:
            member = await bot.get_chat_member(chat.id, target_user.id)
            status = member.status
            is_anon = member.is_anonymous if hasattr(member, 'is_anonymous') else False

            user_text += f"\n<b>Status:</b> {status.capitalize()}"
            if is_anon:
                user_text += f"\n<b>Anonymous:</b> Yes"

            # Show Bot Role
            role = await db.get_user_role(message.chat.id, target_user.id)
            if role:
                user_text += f"\n<b>Bot Role:</b> {role}"

        except Exception:
            pass

    await reply_to_owner(message, chat_text + user_text, parse_mode="HTML")


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

    # 2. Fetch DB Data
    verified_data = await db.get_verified_user(target_user.id)
    is_deleted = await db.is_user_deleted(target_user.id)

    # 3. Build Report
    username_txt = f"@{target_user.username}" if target_user.username else "N/A"

    text = (
        f"<b>👤 Detailed User Info</b>\n\n"
        f"<b>Full Name:</b> {html.escape(target_user.full_name)}\n"
        f"<b>ID:</b> <code>{target_user.id}</code>\n"
        f"<b>Username:</b> {username_txt}\n"
    )

    if verified_data:
        # Verification Info
        phone = verified_data.get("phone_number", "N/A")
        verified_at = verified_data.get("verified_at")
        verified_by = verified_data.get("verified_by")

        date_str = verified_at.strftime("%Y-%m-%d %H:%M:%S") if verified_at else "Unknown"

        text += (
            f"\n<b>📱 Mobile:</b> <code>{phone}</code>\n"
            f"<b>✅ Verified:</b> Yes\n"
            f"<b>📅 Date:</b> {date_str}\n"
        )
        if verified_by:
             text += f"<b>👮 By:</b> {verified_by}\n"
    else:
        text += "\n<b>❌ Verified:</b> No\n"

    # Analytics / Tracking Info (Owner Only)
    if message.from_user.id == config.OWNER_ID:
        global_user = await db.get_global_user_info(target_user.id)
        if global_user:
            from datetime import datetime, timedelta
            first_seen = global_user.get("first_seen", "Unknown")
            if isinstance(first_seen, datetime):
                ist_time = first_seen + timedelta(hours=5, minutes=30)
                first_seen_str = ist_time.strftime("%Y-%m-%d %H:%M:%S IST")
            else:
                first_seen_str = str(first_seen)

            text += "\n<b>📊 Analytics:</b>\n"
            interaction_type = global_user.get('interaction_type', 'private')
            text += f"<b>Source:</b> {interaction_type}\n"
            if interaction_type != "private":
                chat_id = global_user.get('first_seen_chat_id', 'Unknown')
                chat_title = global_user.get('first_seen_chat_title', 'Unknown')
                text += f"<b>Source Chat:</b> <code>{chat_id}</code>, {chat_title}\n"
            text += f"<b>First Interaction:</b> {first_seen_str}\n"

    # GDPR Status
    if is_deleted:
        text += "\n<b>🚫 Status:</b> Soft Deleted (GDPR)"
    else:
         text += "\n<b>✅ Status:</b> Active"

    await reply_to_owner(message, text, parse_mode="HTML")
