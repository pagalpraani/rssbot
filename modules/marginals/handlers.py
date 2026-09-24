# =============================================================================
# Module: Marginals
# Path: modules/marginals/handlers.py
# Description: Message and callback handlers for the Marginals module. Provides
#              routing and command execution.
# =============================================================================

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup
from database.mongodb import db
from utils.settings_layout import SettingsRegistry
from utils.help_registry import HelpRegistry
from utils.formatter import format_message, unparse
from utils.logger import get_logger
from utils.permissions import owner_only
import html
import re
import asyncio

log = get_logger(__name__)

router = Router()

HelpRegistry.register(
    "Marginals",
    "marginals",
    "<b>Marginals</b>\n\n"
    "<i>Automatically add Headers and Footers to channel posts.</i>\n\n"
    "<b>Commands:</b>\n"
    "- /setheader <code>&lt;text|reply|empty&gt;</code>: Set header.\n"
    "- /setfooter <code>&lt;text|reply|empty&gt;</code>: Set footer.\n"
    "- /marginals <code>&lt;on|off&gt;</code>: Toggle marginals.\n",
    supported_chat_types=["channel"]
)

SETTINGS_SCHEMA = {
    "name": "Marginals",
    "db_collection": "settings",
    "category": "Content",
    "order": 22,
    "icon": "📄",
    "supported_chat_types": ["channel"],
    "fields": {
        "marginals_enabled": {
            "type": "bool",
            "label": "Status",
            "text_on": "Enabled",
            "text_off": "Disabled"
        },
        "header_content": {
            "type": "input",
            "label": "Header",
            "description": "Text to appear at the top."
        },
        "footer_content": {
            "type": "input",
            "label": "Footer",
            "description": "Text to appear at the bottom."
        }
    }
}

SettingsRegistry.register_module("marginals", SETTINGS_SCHEMA)

# --- Configuration ---

@router.channel_post(Command("setheader", prefix="!/"))
@owner_only
async def set_header(message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    content = ""

    if len(args) > 1:
        if args[1].lower() == "empty":
            content = ""
        else:
            content = args[1]
    elif message.reply_to_message and message.reply_to_message.text:
        content = message.reply_to_message.text
    else:
        content = ""

    await db.update_settings(message.chat.id, {"header_content": content, "marginals_enabled": True})
    if content:
        await message.answer(f"✅ Header set to:\n{html.escape(content)}", parse_mode="HTML")
    else:
        await message.answer("✅ Header removed.", parse_mode="HTML")

@router.channel_post(Command("setfooter", prefix="!/"))
@owner_only
async def set_footer(message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    content = ""

    if len(args) > 1:
        if args[1].lower() == "empty":
            content = ""
        else:
            content = args[1]
    elif message.reply_to_message and message.reply_to_message.text:
        content = message.reply_to_message.text
    else:
        content = ""

    await db.update_settings(message.chat.id, {"footer_content": content, "marginals_enabled": True})
    if content:
        await message.answer(f"✅ Footer set to:\n{html.escape(content)}", parse_mode="HTML")
    else:
        await message.answer("✅ Footer removed.", parse_mode="HTML")

@router.channel_post(Command("marginals", prefix="!/"))
@owner_only
async def toggle_marginals(message: Message):
    text = message.text or message.caption or ""
    args = text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off", "yes", "no"]:
        await message.answer("Usage: <code>/marginals &lt;on/off&gt;</code>", parse_mode="HTML")
        return

    state = args[1].lower() in ["on", "yes"]
    await db.update_settings(message.chat.id, {"marginals_enabled": state})
    await message.answer(f"Marginals {'enabled' if state else 'disabled'}.", parse_mode="HTML")
