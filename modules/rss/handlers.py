# =============================================================================
# Module: RSS Handlers
# Path: modules/rss/handlers.py
# Description: Handles RSS configuration commands and channel subscription operations.
# =============================================================================

import feedparser
import urllib.parse
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.filters import Command
from database.mongodb import db
from utils.logger import log
from utils.help_registry import HelpRegistry

router = Router(name="rss_router")

import asyncio

HelpRegistry.register(
    "RSS",
    "rss",
    "<b>RSS</b>\n\n"
    "<i>Automatically forward feed articles to your channel, group, or supergroup as posts.</i>\n\n"
    "<b>Commands (run in PM, pass the target chat as first argument):</b>\n"
    "- /addrss <code>&lt;@chat&gt; &lt;feed_url&gt;</code>: Subscribe a chat to an RSS feed.\n"
    "- /removerss <code>&lt;@chat&gt; &lt;feed_url&gt;</code>: Remove an RSS feed from a chat.\n"
    "- /feedwatermark <code>&lt;@chat&gt; &lt;feed_url&gt; &lt;on|off&gt;</code>: Toggle watermark on a specific feed.\n"
    "- /feeddownload <code>&lt;@chat&gt; &lt;feed_url&gt; &lt;on|off&gt;</code>: Toggle media downloading for a feed.\n"
    "- /rss: Open the RSS dashboard (Owner only, PM).\n\n"
    "<b>Notes:</b>\n"
    "- Works in channels, groups, and supergroups.\n"
    "- You must be an admin of the target chat to configure its feeds.\n"
    "- Feeds are checked at a configurable interval (default: 10 minutes).\n"
    "- Use the ➕ Add New Chat button in /rss to register new chats seamlessly.\n"
    "- Logo, footer, and PDF name settings are managed via the Watermark module.",
    supported_chat_types=["channel", "group", "supergroup"]
)

async def _mark_all_existing_items(chat_id: int, url: str):
    import asyncio
    import feedparser
    from database.mongodb import db
    loop = asyncio.get_running_loop()
    try:
        parsed = await asyncio.wait_for(
            loop.run_in_executor(None, feedparser.parse, url),
            timeout=25.0
        )
    except asyncio.TimeoutError:
        log.warning(f"_mark_all_existing_items: feed fetch timed out for {url}")
        return
    item_ids = []
    for entry in parsed.entries:
        item_id = entry.get('id') or entry.get('link') or ''
        if item_id:
            item_ids.append(item_id)

    if not item_ids:
        return

    # Bulk-mark all items in one DB operation instead of N round-trips
    from datetime import datetime, timezone
    from pymongo import UpdateOne
    ops = [
        UpdateOne(
            {'chat_id': chat_id, 'feed_url': url, 'item_id': iid},
            {'$setOnInsert': {'chat_id': chat_id, 'feed_url': url,
                              'item_id': iid, 'processed_at': datetime.now(timezone.utc)}},
            upsert=True
        )
        for iid in item_ids
    ]
    try:
        await db.db.rss_processed.bulk_write(ops, ordered=False)
    except Exception as e:
        log.warning(f'_mark_all_existing_items bulk_write error: {e}')

async def is_valid_feed_url(url: str) -> bool:
    try:
        result = urllib.parse.urlparse(url)
        if not all([result.scheme in ('http', 'https'), result.netloc]):
            return False
        loop = asyncio.get_running_loop()
        try:
            parsed = await asyncio.wait_for(
                loop.run_in_executor(None, feedparser.parse, url),
                timeout=25.0
            )
        except asyncio.TimeoutError:
            log.warning(f"is_valid_feed_url: timed out fetching {url}")
            return False
        # bozo=True only means a minor XML issue; reject only if there are
        # zero entries AND a hard parse error (e.g. not XML at all).
        # A completely empty but valid feed might have 0 entries and bozo=False.
        if not parsed.entries and parsed.bozo and getattr(parsed, 'bozo_exception', None):
            bozo_exc = parsed.get('bozo_exception')
            log.warning(f"is_valid_feed_url: rejected {url} — {bozo_exc}")
            return False
        return True
    except Exception:
        return False


@router.message(Command("addrss"))
async def add_rss_cmd(message: Message, bot: Bot):
    """
    /addrss @chat https://example.com/rss
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply("Usage: /addrss @chat https://example.com/rss")
        return

    channel = args[1]
    url = args[2]

    try:
        chat = await bot.get_chat(channel)

        # Verify user is admin of the target chat
        member = await bot.get_chat_member(chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("You must be an admin of this chat to configure this.")
            return

        if not await is_valid_feed_url(url):
            await message.reply("Invalid RSS Feed URL. Make sure it starts with http/https and is a valid XML/Atom feed.")
            return

        await db.add_rss_feed(chat.id, url)
        await _mark_all_existing_items(chat.id, url)
        await message.reply(f"Successfully added RSS feed {url} to {chat.title}.")
    except Exception as e:
        await message.reply(f"Error: {e}")

@router.message(Command("removerss"))
async def remove_rss_cmd(message: Message, bot: Bot):
    """
    /removerss @chat https://example.com/rss
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply("Usage: /removerss @chat https://example.com/rss")
        return

    channel = args[1]
    url = args[2]

    try:
        chat = await bot.get_chat(channel)

        member = await bot.get_chat_member(chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("You must be an admin of this chat to configure this.")
            return

        await db.remove_rss_feed(chat.id, url)
        await message.reply(f"Successfully removed RSS feed {url} from {chat.title}.")
    except Exception as e:
        await message.reply(f"Error: {e}")

@router.message(Command("feedwatermark"))
async def feed_watermark_cmd(message: Message, bot: Bot):
    """
    /feedwatermark @chat https://example.com/rss on/off
    """
    args = message.text.split()
    if len(args) < 4:
        await message.reply("Usage: /feedwatermark @chat https://example.com/rss on/off")
        return

    channel = args[1]
    url = args[2]
    state_str = args[3].lower()

    if state_str in ("on", "yes", "true", "1"):
        enabled = True
    elif state_str in ("off", "no", "false", "0"):
        enabled = False
    else:
        await message.reply("Invalid state. Use on, off, yes, or no.")
        return

    try:
        chat = await bot.get_chat(channel)

        member = await bot.get_chat_member(chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("You must be an admin of this chat to configure this.")
            return

        await db.update_feed_watermark(chat.id, url, enabled)
        await message.reply(f"Watermark for feed {url} in {chat.title} set to {enabled}.")
    except Exception as e:
        await message.reply(f"Error: {e}")

@router.message(Command("feeddownload"))
async def feed_download_cmd(message: Message, bot: Bot):
    """
    /feeddownload @chat https://example.com/rss on/off
    """
    args = message.text.split()
    if len(args) < 4:
        await message.reply("Usage: /feeddownload @chat https://example.com/rss on/off")
        return

    channel = args[1]
    url = args[2]
    state_str = args[3].lower()

    if state_str in ("on", "yes", "true", "1"):
        enabled = True
    elif state_str in ("off", "no", "false", "0"):
        enabled = False
    else:
        await message.reply("Invalid state. Use on, off, yes, or no.")
        return

    try:
        chat = await bot.get_chat(channel)

        member = await bot.get_chat_member(chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("You must be an admin of this chat to configure this.")
            return

        await db.update_feed_download(chat.id, url, enabled)
        await message.reply(f"Media downloading for feed {url} in {chat.title} set to {enabled}.")
    except Exception as e:
        await message.reply(f"Error: {e}")
