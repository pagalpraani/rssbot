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
@router.message(Command("logchannel", prefix="!/"), F.chat.type.in_({"group", "supergroup"}))
@owner_only
async def logchannel_command(message: Message, bot: Bot):
    await _set_log_channel(message, bot)

@router.channel_post(Command("logchannel", prefix="!/"))
@owner_only
async def logchannel_command_channel(message: Message, bot: Bot):
    await _set_log_channel(message, bot)

async def _set_log_channel(message: Message, bot: Bot):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("Usage: <code>/logchannel &lt;channel_id|off&gt;</code>", parse_mode="HTML")
        return

    val = args[1].lower()
    if val == "off":
        await db.update_settings(message.chat.id, {"log_channel_id": None})
        await message.reply("✅ Logging disabled.", parse_mode="HTML")
        return

    try:
        channel_id = int(val)
        # Verify access
        try:
            chat = await bot.get_chat(channel_id)
            await db.update_settings(message.chat.id, {"log_channel_id": channel_id})
            await message.reply(f"✅ Log channel set to: <b>{html.escape(chat.title)}</b> (<code>{channel_id}</code>)", parse_mode="HTML")
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
        "log_channel_id": {
            "type": "input",
            "label": "Log Channel",
            "icon": "🛡️",
            "description": "Channel ID where logs for this chat will be sent (e.g. -100...)."
        },
        "log_enabled": {
            "type": "bool",
            "label": "Logging",
            "description": "Turn logging on or off for this chat.",
            "default": True
        }
    }
}

SettingsRegistry.register_module("logging", SETTINGS_SCHEMA)
