# =============================================================================
# Module: Blocklist
# Path: utilitybot/modules/blocklist/handlers.py
# Description: Message and callback handlers for the Blocklist module. Provides
#              routing and command execution.
# =============================================================================

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from ...database.mongodb import db
from ...utils.settings_layout import SettingsRegistry
from ...utils.help_registry import HelpRegistry
from ...utils.logger import get_logger
from ...utils.permissions import owner_only
import html
import re
from .utils import invalidate_blocklist_cache

log = get_logger(__name__)

router = Router()

HelpRegistry.register(
    "Blocklist",
    "blocklist",
    "<b>Blocklist</b>\n\n"
    "<i>Automatically delete channel posts containing specific keywords.</i>\n\n"
    "<b>Commands:</b>\n"
    "- /block <code>&lt;keyword&gt;</code>: Add keyword.\n"
    "- /unblock <code>&lt;keyword&gt;</code>: Remove keyword.\n"
    "- /blocklist: List all blocked keywords.\n"
    "- /unblockall: Clear the blocklist.\n",
    supported_chat_types=["channel"]
)

SETTINGS_SCHEMA = {
    "name": "Blocklist",
    "db_collection": "settings",
    "category": "Security",
    "order": 10,
    "icon": "🚫",
    "supported_chat_types": ["channel"],
    "fields": {
        "blocklist_enabled": {
            "type": "bool",
            "label": "Status",
            "text_on": "Enabled",
            "text_off": "Disabled"
        },
        "blocklist": {
            "type": "list_input",
            "label": "Blocked Keywords",
            "description": "Posts containing these will be deleted."
        },
        "blocklist_mode": {
            "type": "select",
            "label": "Match Mode",
            "options": ["whole_word", "partial"],
            "description": "Select 'whole_word' for exact matches or 'partial' for partial string matches."
        }
    }
}

SettingsRegistry.register_module("blocklist", SETTINGS_SCHEMA)

# --- Configuration ---

@router.channel_post(Command("block", prefix="!/"))
@owner_only
async def block_keyword(message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Usage: <code>/block &lt;keyword&gt;</code>", parse_mode="HTML")
        return

    keyword = args[1].strip().lower()

    # Atomic update: add to set
    await db.db.settings.update_one(
        {"_id": message.chat.id},
        {"$addToSet": {"blocklist": keyword}},
        upsert=True
    )
    invalidate_blocklist_cache(message.chat.id)
    await message.answer(f"Added <code>{html.escape(keyword)}</code> to blocklist.", parse_mode="HTML")

@router.channel_post(Command("unblock", prefix="!/"))
@owner_only
async def unblock_keyword(message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Usage: <code>/unblock &lt;keyword&gt;</code>", parse_mode="HTML")
        return

    keyword = args[1].strip().lower()

    # Atomic update: remove from set
    result = await db.db.settings.update_one(
        {"_id": message.chat.id},
        {"$pull": {"blocklist": keyword}}
    )

    if result.modified_count > 0:
        invalidate_blocklist_cache(message.chat.id)
        await message.answer(f"Removed <code>{html.escape(keyword)}</code> from blocklist.", parse_mode="HTML")
    else:
        await message.answer(f"<code>{html.escape(keyword)}</code> was not found in the blocklist.", parse_mode="HTML")

@router.channel_post(Command("blocklist", prefix="!/"))
@owner_only
async def list_blocked(message: Message):
    settings = await db.get_settings(message.chat.id) or {}
    blocklist = settings.get("blocklist", [])
    if not blocklist:
        await message.answer("Blocklist is empty.", parse_mode="HTML")
    else:
        # Format as list
        text = "<b>Blocked Keywords:</b>\n" + "\n".join([f"• <code>{html.escape(k)}</code>" for k in blocklist])
        await message.answer(text, parse_mode="HTML")

@router.channel_post(Command("unblockall", prefix="!/"))
@owner_only
async def clear_blocklist(message: Message):
    await db.update_settings(message.chat.id, {"blocklist": []})
    invalidate_blocklist_cache(message.chat.id)
    await message.answer("Blocklist cleared.", parse_mode="HTML")
