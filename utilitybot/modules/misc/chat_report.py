# =============================================================================
# Module: Misc — Chat Report
# Path: utilitybot/modules/misc/chat_report.py
# Description: /chatreport — dumps every active chat with its subscribed RSS
#              feeds to a downloadable .txt file.
# =============================================================================

import asyncio
import os
import tempfile

import aiofiles
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile

from ...database.mongodb import db
from ...utils.permissions import owner_only
from ...utils.logger import log

router = Router()


def _feed_display_name(feed: dict) -> str:
    # Same fallback chain used throughout the RSS dashboard, kept consistent here.
    return feed.get("custom_title") or feed.get("feed_title") or feed.get("feed_url", "Unknown Feed")


async def _build_report_text() -> tuple[str, int, int]:
    """
    Fetches every active (RSS-linked) chat and its feeds, and renders the
    plain-text report. Returns (text, chat_count, feed_count).
    """
    chats = await db.get_all_bot_chats()

    # Fetch each chat's feeds concurrently — this is the "don't block on the
    # DB" part: every get_rss_feeds_by_chat() call is awaited in parallel via
    # asyncio.gather rather than one at a time in a loop.
    feed_lists = await asyncio.gather(
        *[db.get_rss_feeds_by_chat(chat["chat_id"]) for chat in chats]
    )

    lines = []
    total_feeds = 0

    for chat, feeds in zip(chats, feed_lists):
        chat_name = chat.get("title") or str(chat["chat_id"])
        chat_id = chat["chat_id"]
        invite_link = chat.get("link")

        lines.append(f"• {chat_name}")
        lines.append(f"• {chat_id}")
        lines.append(f"• {invite_link}" if invite_link else "• Invite Link: N/A")
        lines.append(f"• Total Subscribed Feed = {len(feeds)}")

        for feed in feeds:
            name = _feed_display_name(feed)
            url = feed.get("feed_url", "")
            status = "On" if feed.get("status", "Activated") == "Activated" else "Off"
            lines.append(f"     • {name} - {url} - [{status}]")
            total_feeds += 1

        lines.append("")  # blank line between chats

    text = "\n".join(lines).rstrip() + "\n"
    return text, len(chats), total_feeds


@router.message(Command("chatreport", prefix="!/"), F.chat.type == "private")
@owner_only
async def chat_report_command(message: Message, bot: Bot):
    status_msg = await message.reply("⏳ Generating report, please wait...")

    tmp_path = None
    try:
        text, chat_count, feed_count = await _build_report_text()

        # Async file write so we never block the event loop, even though this
        # report is plain text and typically small — matters once chat/feed
        # counts get large.
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="chat_report_")
        os.close(fd)
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(text)

        await status_msg.edit_text(
            f"✅ Report generated: {chat_count} chat(s), {feed_count} feed(s)."
        )
        await message.answer_document(
            FSInputFile(tmp_path, filename="chat_report.txt"),
            caption=f"📊 {chat_count} chat(s), {feed_count} feed(s)."
        )

    except Exception as e:
        log.exception(f"chat_report_command failed: {e}")
        await status_msg.edit_text(f"❌ Failed to generate report: {e}")

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
