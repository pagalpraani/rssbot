# =============================================================================
# Module: Replacements
# Path: modules/replacements/handlers.py
# Description: Message and callback handlers for the Replacements module. Provides
#              routing and command execution.
# =============================================================================

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from database.mongodb import db
from utils.settings_layout import SettingsRegistry
from utils.help_registry import HelpRegistry
from utils.formatter import unparse
from utils.content_pipeline import ContentPipeline
from utils.permissions import owner_only
import html
import unicodedata
import shlex

router = Router()

HelpRegistry.register(
    "Replacements",
    "replacements",
    "<b>Replacements</b>\n\n"
    "<i>Automatically replace specific keywords in channel posts with another text.</i>\n\n"
    "<b>Commands:</b>\n"
    "- /replace <code>[-i] [-w] &lt;source&gt; &lt;destination&gt;</code>: Add replacement.\n"
    "  Flags:\n"
    "  <code>-i</code>: Case-insensitive.\n"
    "  <code>-w</code>: Whole Word only.\n"
    "- /stop <code>&lt;source&gt;</code>: Stop a replacement.\n"
    "- /replacements: List all replacements.\n"
    "- /stopall: Stop all replacements.\n",
    supported_chat_types=["channel"]
)

SETTINGS_SCHEMA = {
    "name": "Replacements",
    "db_collection": "settings",
    "category": "Content",
    "order": 21,
    "icon": "🔀",
    "supported_chat_types": ["channel"],
    "fields": {
        "replacements_active": {
            "type": "bool",
            "label": "Status",
            "text_on": "Active",
            "text_off": "Inactive"
        },
        "replacements_map": {
            "type": "list_input",
            "label": "Active Replacements",
            "description": "View active replacements (read-only in this view).",
            "hidden": True
        }
    }
}

SettingsRegistry.register_module("replacements", SETTINGS_SCHEMA)

# --- DB Helpers ---

async def get_replacements(chat_id):
    settings = await db.get_settings(chat_id) or {}
    return settings.get("replacements_map", [])

async def add_replacement_db(chat_id, src, dest, ignore_case=False, whole_word=False, apply_on=None):
    if apply_on is None:
        apply_on = ["text", "caption", "media_group"]

    repls = await get_replacements(chat_id)
    # Remove existing if any for this src (normalized check)
    repls = [r for r in repls if r['src'].lower() != src.lower()]

    repls.append({
        "src": src,
        "dest": dest,
        "ignore_case": ignore_case,
        "whole_word": whole_word,
        "apply_on": apply_on
    })

    await db.update_settings(chat_id, {"replacements_map": repls, "replacements_active": True})
    ContentPipeline.invalidate_replacement_cache(chat_id)

async def remove_replacement(chat_id, src):
    repls = await get_replacements(chat_id)
    new_repls = [r for r in repls if r['src'].lower() != src.lower()]
    await db.update_settings(chat_id, {"replacements_map": new_repls})
    ContentPipeline.invalidate_replacement_cache(chat_id)
    return len(new_repls) < len(repls)

# --- Configuration ---

@router.channel_post(Command("replace", prefix="!/"))
@owner_only
async def add_replace_cmd(message: Message):
    msg_text = message.text or message.caption or ""
    # Strip command prefix safely
    if " " not in msg_text:
         await message.answer(
             "❌ <b>Invalid Syntax</b>\n\n"
             "Usage: <code>/replace [-i] [-w] &lt;source&gt; &lt;destination&gt;</code>\n\n"
             "<i>Tip: Use quotes if your text contains spaces.</i>\n"
             "Example: <code>/replace \"hello world\" \"hi earth\"</code>",
             parse_mode="HTML"
         )
         return

    raw_args = msg_text.split(" ", 1)[1]

    try:
        args = shlex.split(raw_args)
    except ValueError:
        await message.answer(
            "❌ <b>Parsing Error</b>\n\n"
            "Could not read your arguments. Please check for unmatched quotes.\n"
            "Example: <code>/replace \"bad phrase\" \"good phrase\"</code>",
            parse_mode="HTML"
        )
        return

    if not args:
        await message.answer(
             "❌ <b>Invalid Syntax</b>\n\n"
             "Usage: <code>/replace [-i] [-w] &lt;source&gt; &lt;destination&gt;</code>\n\n"
             "<i>Tip: Use quotes if your text contains spaces.</i>\n"
             "Example: <code>/replace \"hello world\" \"hi earth\"</code>",
             parse_mode="HTML"
         )
        return

    # Parse Flags
    ignore_case = False
    whole_word = False

    # Simple flag consumption
    while args and args[0].startswith('-'):
        flag = args.pop(0)
        if flag == '-i':
            ignore_case = True
        elif flag == '-w':
            whole_word = True
        else:
            await message.answer(f"Unknown flag: <code>{flag}</code>", parse_mode="HTML")
            return

    if len(args) != 2:
        await message.answer(
             "❌ <b>Invalid Syntax</b>\n\n"
             "Usage: <code>/replace [-i] [-w] &lt;source&gt; &lt;destination&gt;</code>\n\n"
             "<i>Tip: Use quotes if your text contains spaces.</i>\n"
             "Example: <code>/replace \"hello world\" \"hi earth\"</code>",
             parse_mode="HTML"
         )
        return

    src, dest = args

    # 1. Normalization
    src = src.strip()
    dest = dest.strip()

    if not src:
        await message.answer("❌ Source text cannot be empty.", parse_mode="HTML")
        return

    src = unicodedata.normalize("NFC", src)
    dest = unicodedata.normalize("NFC", dest)

    # 2. Validation
    # Infinite loop check: simple A -> A
    if src.lower() == dest.lower():
         await message.answer("❌ Destination must be different from source.", parse_mode="HTML")
         return

    # SECURITY: ReDoS Prevention
    # Check regex complexity implicitly via length
    # A source keyword > 50 chars is suspicious for regex abuse if we treat it as regex later.
    if len(src) > 50:
        await message.answer("❌ Source text too long (max 50 chars).", parse_mode="HTML")
        return

    # Check for catastrophic backtracking patterns if src is treated as regex
    # Since we escape it with re.escape(), standard text is safe.
    # But if future logic allows regex input, this would be critical.
    # Currently we re.escape() in ContentPipeline, so user input is treated as literal.
    # However, to be extra safe against memory exhaustion:
    if len(dest) > 4096:
        await message.answer("❌ Destination text too long.", parse_mode="HTML")
        return

    # 3. Add to DB
    await add_replacement_db(message.chat.id, src, dest, ignore_case=ignore_case, whole_word=whole_word)

    flags_list = []
    if ignore_case:
        flags_list.append("Case-Insensitive")
    if whole_word:
        flags_list.append("Whole-Word")

    flags_str = f" ({', '.join(flags_list)})" if flags_list else ""

    await message.answer(f"✅ Replaced <code>{html.escape(src)}</code> → <code>{html.escape(dest)}</code>{flags_str}.", parse_mode="HTML")

@router.channel_post(Command("stop", prefix="!/"))
@owner_only
async def stop_replace_cmd(message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ <b>Invalid Syntax</b>\n\n"
            "Usage: <code>/stop &lt;source&gt;</code>\n\n"
            "Example: <code>/stop \"hello world\"</code>",
            parse_mode="HTML"
        )
        return

    src = args[1]
    if await remove_replacement(message.chat.id, src):
        await message.answer(f"✅ Stopped replacement for <code>{html.escape(src)}</code>.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Replacement for <code>{html.escape(src)}</code> not found.", parse_mode="HTML")

@router.channel_post(Command("replacements", prefix="!/"))
@owner_only
async def list_replace_cmd(message: Message):
    repls = await get_replacements(message.chat.id)
    if not repls:
        await message.answer("No active replacements.", parse_mode="HTML")
        return

    lines = []
    for r in repls:
        src = r['src']
        dest = r['dest']
        ignore_case = r.get('ignore_case', True) # Legacy default check
        whole_word = r.get('whole_word', False)

        flags = ""
        if ignore_case:
            flags += " [-i]"
        if whole_word:
            flags += " [-w]"

        lines.append(f"• <code>{html.escape(src)}</code> ➡️ <code>{html.escape(dest)}</code>{flags}")

    await message.answer("<b>Active Replacements:</b>\n" + "\n".join(lines), parse_mode="HTML")

@router.channel_post(Command("stopall", prefix="!/"))
@owner_only
async def stop_all_replace_cmd(message: Message):
    await db.update_settings(message.chat.id, {"replacements_map": []})
    ContentPipeline.invalidate_replacement_cache(message.chat.id)
    await message.answer("✅ All replacements stopped.", parse_mode="HTML")
