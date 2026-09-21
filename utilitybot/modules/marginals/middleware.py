# =============================================================================
# Module: Marginals
# Path: utilitybot/modules/marginals/middleware.py
# Description: Middleware implementation for the Marginals module. Intercepts and
#              processes updates before handlers.
# =============================================================================

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from aiogram.exceptions import TelegramRetryAfter
from typing import Callable, Dict, Any, Awaitable
from ...utils.formatter import unparse
from ...utils.content_pipeline import ContentPipeline
import html
import re
import asyncio
import logging
from collections import defaultdict

log = logging.getLogger(__name__)

class MarginalsMiddleware(BaseMiddleware):
    def __init__(self):
        self._channel_media_groups = {}
        # Per-chat lock to prevent Telegram API flood limits when editing many messages
        self._chat_locks = {}
        self._lock_ref_counts = defaultdict(int)
        super().__init__()

    def _get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()
        self._lock_ref_counts[chat_id] += 1
        return self._chat_locks[chat_id]

    def _release_chat_lock(self, chat_id: int):
        self._lock_ref_counts[chat_id] -= 1
        if self._lock_ref_counts[chat_id] <= 0:
            self._chat_locks.pop(chat_id, None)
            self._lock_ref_counts.pop(chat_id, None)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:

        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event
        bot = data.get("bot")

        # Ignore commands
        if message.text and message.text.startswith(('/', '!')):
            return await handler(event, data)

        # Fire-and-forget: marginals are cosmetic, must not block the handler chain
        asyncio.create_task(self.process_marginals(message, bot, data))

        return await handler(event, data)

    async def process_marginals(self, message: Message, bot, data=None):
        # Skip forwarded messages — Telegram prevents editing them
        if message.forward_origin:
            return

        # Media groups: collect all parts then process the captioned one
        if message.media_group_id:
            mg_id = message.media_group_id

            if mg_id not in self._channel_media_groups:
                self._channel_media_groups[mg_id] = []
                asyncio.create_task(
                    self._process_channel_album(mg_id, message.chat.id, bot, data)
                )

            self._channel_media_groups[mg_id].append(message)
            return

        await self._apply_marginals_to_message(message, bot, data)

    async def _process_channel_album(self, mg_id: str, chat_id: int, bot, data=None):
        try:
            await asyncio.sleep(4.0)

            msgs = self._channel_media_groups.pop(mg_id, [])
            if not msgs:
                return

            msgs.sort(key=lambda x: x.message_id)

            target_msg = next((m for m in msgs if m.caption), msgs[0])

            if target_msg.forward_origin:
                return

            await self._apply_marginals_to_message(target_msg, bot, data)
        except Exception:
            # Guarantee dict cleanup even if processing fails mid-way
            self._channel_media_groups.pop(mg_id, None)

    # Compiled once at class level — not per-message
    _FLAGS_RE = re.compile(r'{(nomarginals|noheader|nofooter|nogap)}')

    async def _apply_marginals_to_message(self, message: Message, bot, data=None):
        chat_id = message.chat.id
        lock = self._get_chat_lock(chat_id)

        try:
            if message.sticker or message.forward_origin:
                return

            original_text = message.text or message.caption or ""

            # Idempotency guard
            if ContentPipeline.PIPELINE_MARKER in original_text:
                return

            entities = message.entities or message.caption_entities or []

            # Use replacement-patched HTML if available from ReplacementsMiddleware
            if data and data.get("has_replacement"):
                body_html = data.get("patched_html", "")
            else:
                body_html = (
                    unparse(original_text, entities)
                    if entities else html.escape(original_text)
                )

            # Extract inline flags
            body_flags = set(self._FLAGS_RE.findall(body_html))
            for flag in body_flags:
                body_html = body_html.replace(f"{{{flag}}}", "")

            # {nomarginals} — strip any existing marginals then save clean text
            if "nomarginals" in body_flags:
                async with lock:
                    await self._safe_edit(message, body_html, None, is_html=True)
                return

            # Pass per-message flag overrides into the pipeline
            flag_overrides = {
                "skip_header": "noheader" in body_flags,
                "skip_footer": "nofooter" in body_flags,
            }

            final_text, new_markup = await ContentPipeline.process_marginals(
                body_html, message.chat.id, bot, flag_overrides=flag_overrides
            )

            # Nothing changed — no marginals configured
            if final_text == body_html and not new_markup:
                return

            # Apply edit with serialization lock and retry logic
            async with lock:
                await self._safe_edit(message, final_text, new_markup)

        except Exception as e:
            # Silent fail — middleware must not break the message chain
            pass
        finally:
            self._release_chat_lock(chat_id)

    async def _safe_edit(self, message: Message, text: str, markup, is_html: bool = True):
        """Helper to safely edit a message with retries exclusively for flood limits."""
        max_retries = 3

        is_media = (
            message.caption is not None
            or message.photo or message.video or message.audio
            or message.voice or message.document or message.animation
        )

        for attempt in range(max_retries):
            try:
                if is_media:
                    await message.edit_caption(
                        caption=text,
                        reply_markup=markup,
                        parse_mode="HTML" if is_html else None
                    )
                else:
                    await message.edit_text(
                        text=text,
                        reply_markup=markup,
                        parse_mode="HTML" if is_html else None
                    )
                return # Success
            except TelegramRetryAfter as e:
                log.warning(f"Flood limit hit editing msg {message.message_id}. Retrying in {e.retry_after}s.")
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                # Non-transient errors (like MessageNotModified) should immediately fail to avoid holding lock
                raise e
