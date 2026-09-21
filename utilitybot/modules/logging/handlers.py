# =============================================================================
# Module: Logging
# Path: utilitybot/modules/logging/handlers.py
# Description: Message and callback handlers for the Logging module. Provides routing
#              and command execution.
# Scope: channel | group
# =============================================================================

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from ...utils.settings_layout import SettingsRegistry
from ...utils.help_registry import HelpRegistry
from ...utils.permissions import owner_only
from ...database.mongodb import db
import html

router = Router()

# Logging Module Help
HelpRegistry.register(
    "Logging",
    "logging",
    "<b>Logging System</b>\n\n"
    "<i>Configure centralized logging for this chat.</i>\n\n"
    "<b>Commands:</b>\n"
    "- /logchannel <code>&lt;id|off&gt;</code>: Set/Disable log channel.\n",
    supported_chat_types=["group", "supergroup", "channel"]
)

# --- Command Handler ---
@router.message(Command("logchannel", prefix="!/"))
@owner_only
async def logchannel_command(message: Message, bot: Bot):
    chat_type = message.chat.type

    # 1. Determine Permission based on Chat Type
    if chat_type in ["group", "supergroup"]:
        db_key = "log_channel"
        log_type = "Group"

    elif chat_type == "channel":
        db_key = "log_channel_id"
        log_type = "Channel"
    else:
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply("Usage: <code>/logchannel &lt;channel_id|off&gt;</code>", parse_mode="HTML")
        return

    val = args[1].lower()
    if val == "off":
        await db.update_settings(message.chat.id, {db_key: None})
        await message.reply("✅ Logging disabled.", parse_mode="HTML")
        return

    try:
        channel_id = int(val)
        # Verify access
        try:
            chat = await bot.get_chat(channel_id)
            await db.update_settings(message.chat.id, {db_key: channel_id})
            await message.reply(f"✅ {log_type} Log channel set to: <b>{html.escape(chat.title)}</b> (<code>{channel_id}</code>)", parse_mode="HTML")
        except Exception as e:
            await message.reply(f"❌ Could not access channel: {html.escape(str(e))}. Make sure I am admin there.", parse_mode="HTML")
    except ValueError:
        await message.reply("❌ Invalid ID.", parse_mode="HTML")

# Logging Module Settings Schema
SETTINGS_SCHEMA = {
    "name": "Logging",
    "db_collection": "settings",
    "category": "System",
    "order": 5, # High priority
    "icon": "📜",
    "supported_chat_types": ["group", "supergroup", "channel"],
    "fields": {
        # --- GROUP SETTINGS ---
        "log_channel": {
            "type": "input",
            "label": "Group Log Channel",
            "icon": "🛡️",
            "description": "Channel ID for admin logs (e.g. -100...)",
            "required_chat_types": ["group", "supergroup"]
        },
        "log_admin_enabled": {
            "type": "bool",
            "label": "Log Admin Actions",
            "description": "Log bans, mutes, and kicks.",
            "required_chat_types": ["group", "supergroup"],
            "default": True
        },

        # --- CHANNEL SETTINGS ---
        "log_channel_id": {
            "type": "input",
            "label": "Channel Log Channel",
            "icon": "📢",
            "description": "Channel ID for post logs.",
            "required_chat_types": ["channel"]
        },
        "log_posts_enabled": {
            "type": "bool",
            "label": "Log Posts",
            "description": "Log post submissions and distributions.",
            "required_chat_types": ["channel"],
            "default": True
        }
    }
}

SettingsRegistry.register_module("logging", SETTINGS_SCHEMA)
