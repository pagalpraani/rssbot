# =============================================================================
# Module: Watermark Settings
# Path: modules/watermark/handlers.py
# Description: Registers the Watermark module to SettingsRegistry and HelpRegistry,
#              and handles /setlogo, /setfooternote, /setpdfname PM commands.
# Scope: channel | group | supergroup
# =============================================================================

import config
from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command
from utils.settings_layout import SettingsRegistry
from utils.help_registry import HelpRegistry
from database.mongodb import db
from utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="watermark_router")

HelpRegistry.register(
    "Watermark",
    "watermark",
    "<b>Watermark</b>\n\n"
    "<i>Apply logos, PDF footers, custom filenames, and thumbnails to media in channels, groups, and supergroups.</i>\n\n"
    "<b>Commands (run in PM, pass the target chat as first argument):</b>\n"
    "- /setlogo <code>&lt;@chat&gt;</code> (reply to PNG): Set logo for watermarks.\n"
    "- /setfooternote <code>&lt;@chat&gt; &lt;link&gt;</code>: Set footer link appended to PDFs.\n"
    "- /setpdfname <code>&lt;@chat&gt; &lt;suffix&gt;</code>: Set custom suffix for renamed PDFs.\n"
    "- /feedwatermark <code>&lt;@chat&gt; &lt;feed_url&gt; &lt;on|off&gt;</code>: Toggle watermark for a specific RSS feed.\n\n"
    "<b>Configuration (via /settings Dashboard):</b>\n"
    "- <b>Watermark:</b> Globally enable or disable the watermark overlay.\n"
    "- <b>Watermark Transparency:</b> Set opacity percentage (1–100).\n"
    "- <b>Logo:</b> Upload an uncompressed PNG, a photo, or paste a Telegram File ID.\n"
    "- <b>PDF Footernote:</b> Enable and set a link appended to the bottom of PDFs.\n"
    "- <b>PDF Name Suffix:</b> Enable and set a custom suffix appended to renamed PDFs.\n"
    "- <b>PDF Thumbnail:</b> Generate a visual thumbnail from the first page of PDFs.\n\n"
    "<b>Notes:</b>\n"
    "- Works in channels, groups, and supergroups.\n"
    "- Watermark settings apply to RSS feed posts.\n"
    "- Logo must be a PNG document (not a compressed photo) for best results.",
    supported_chat_types=["channel", "group", "supergroup"]
)

SETTINGS_SCHEMA = {
    "name": "Watermark",
    "db_collection": "settings",
    "category": "Content",
    "order": 24,
    "icon": "💧",
    "supported_chat_types": ["channel", "group", "supergroup"],
    "fields": {
        "watermark_enabled": {
            "type": "bool",
            "label": "Watermark",
            "text_on": "On",
            "text_off": "Off",
            "description": "Globally enable or disable watermarks."
        },
        "footer_note_enabled": {
            "type": "bool",
            "label": "PDF Footernote",
            "text_on": "On",
            "text_off": "Off",
            "description": "Enable appending a footer link to PDFs."
        },
        "custom_name_enabled": {
            "type": "bool",
            "label": "PDF Name Suffix",
            "text_on": "On",
            "text_off": "Off",
            "description": "Enable custom PDF renaming."
        },
        "thumbnail_enabled": {
            "type": "bool",
            "label": "PDF Thumbnail",
            "text_on": "On",
            "text_off": "Off",
            "description": "Generate a visual thumbnail from the first page of PDFs."
        },
        "watermark_opacity": {
            "type": "input",
            "label": "Watermark Transparency",
            "description": "Enter a transparency percentage (1-100).",
            "validator": lambda x: (x.isdigit() and 1 <= int(x) <= 100, "Percentage must be an integer between 1 and 100.")
        },
        "logo_id": {
            "type": "input",
            "label": "Logo",
            "description": "Upload an uncompressed PNG document, a Photo, or paste a valid Telegram File ID.",
            "allow_clear": True
        },
        "footer_note": {
            "type": "input",
            "label": "PDF Footernote",
            "description": "The custom string (supports links) appended to the PDF.",
            "allow_clear": True
        },
        "custom_name": {
            "type": "input",
            "label": "PDF Name Suffix",
            "description": "Appended to renamed PDFs (e.g., _Suffix.pdf).",
            "allow_clear": True
        }
    }
}

SettingsRegistry.register_module("watermark", SETTINGS_SCHEMA)


# =============================================================================
# Watermark PM Commands
# Run in PM, pass target @chat as first argument.
# Bot must be admin of the target chat.
# =============================================================================

async def _resolve_chat_as_admin(message: Message, bot: Bot, args: list, min_args: int, usage: str):
    """
    Shared helper: parse args, verify caller is admin of the target chat.
    Returns the Chat object, or None if validation failed (reply already sent).
    """
    if len(args) < min_args:
        await message.reply(f"Usage: {usage}")
        return None
    try:
        chat = await bot.get_chat(args[1])
        member = await bot.get_chat_member(chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("You must be an admin of this chat to configure this.")
            return None
        return chat
    except Exception as e:
        await message.reply(f"Error: {e}")
        return None


@router.message(Command("setlogo"))
async def set_logo_cmd(message: Message, bot: Bot):
    """
    /setlogo @chat — reply to an uncompressed PNG document to set the watermark logo.
    Works for channels, groups, and supergroups.
    """
    if message.from_user.id != config.OWNER_ID:
        return
    args = message.text.split()
    if (
        not message.reply_to_message
        or not message.reply_to_message.document
        or getattr(message.reply_to_message.document, "mime_type", "") != "image/png"
    ):
        await message.reply("You must reply to an uncompressed PNG document.\n\nUsage: /setlogo @chat (reply to PNG)")
        return

    chat = await _resolve_chat_as_admin(message, bot, args, 2, "/setlogo @chat (reply to PNG)")
    if not chat:
        return

    logo_id = message.reply_to_message.document.file_id
    try:
        settings = await db.get_settings(chat.id) or {}
        settings["logo_id"] = logo_id
        await db.update_settings(chat.id, settings)
        await message.reply(f"✅ Logo set successfully for <b>{chat.title}</b>.", parse_mode="HTML")
        log.info(f"Logo updated for chat {chat.id} by user {message.from_user.id}")
    except Exception as e:
        log.error(f"setlogo failed for chat {chat.id}: {e}")
        await message.reply(f"Error saving logo: {e}")


@router.message(Command("setfooternote"))
async def set_footer_cmd(message: Message, bot: Bot):
    if message.from_user.id != config.OWNER_ID:
        return
    """
    /setfooternote @chat <link> — set the footer link appended to PDFs.
    Works for channels, groups, and supergroups.
    """
    args = message.text.split()
    chat = await _resolve_chat_as_admin(message, bot, args, 3, "/setfooternote @chat https://t.me/yourlink")
    if not chat:
        return

    link = " ".join(args[2:])
    try:
        settings = await db.get_settings(chat.id) or {}
        settings["footer_note"] = link
        await db.update_settings(chat.id, settings)
        await message.reply(f"✅ Footer note set to <code>{link}</code> for <b>{chat.title}</b>.", parse_mode="HTML")
        log.info(f"Footer note updated for chat {chat.id} by user {message.from_user.id}")
    except Exception as e:
        log.error(f"setfooternote failed for chat {chat.id}: {e}")
        await message.reply(f"Error saving footer note: {e}")


@router.message(Command("setpdfname"))
async def set_pdf_name_cmd(message: Message, bot: Bot):
    if message.from_user.id != config.OWNER_ID:
        return
    """
    /setpdfname @chat <suffix> — set a custom suffix appended to renamed PDFs.
    Works for channels, groups, and supergroups.
    """
    args = message.text.split()
    chat = await _resolve_chat_as_admin(message, bot, args, 3, "/setpdfname @chat CustomSuffix")
    if not chat:
        return

    name = " ".join(args[2:])
    try:
        settings = await db.get_settings(chat.id) or {}
        settings["custom_name"] = name
        await db.update_settings(chat.id, settings)
        await message.reply(f"✅ PDF name suffix set to <code>{name}</code> for <b>{chat.title}</b>.", parse_mode="HTML")
        log.info(f"PDF name suffix updated for chat {chat.id} by user {message.from_user.id}")
    except Exception as e:
        log.error(f"setpdfname failed for chat {chat.id}: {e}")
        await message.reply(f"Error saving PDF name: {e}")
