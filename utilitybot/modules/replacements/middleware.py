# =============================================================================
# Module: Replacements
# Path: utilitybot/modules/replacements/middleware.py
# Description: Middleware implementation for the Replacements module. Intercepts and
#              processes updates before handlers.
# =============================================================================

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from typing import Callable, Dict, Any, Awaitable
from ...utils.formatter import unparse
from ...utils.content_pipeline import ContentPipeline
from ...database.mongodb import db
import html

class ReplacementsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:

        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event

        # Check for Pipeline Marker (Idempotency)
        text_check = message.text or message.caption or ""
        if ContentPipeline.PIPELINE_MARKER in text_check:
            return await handler(event, data)

        # Skip commands: Check startswith AND entities
        if text_check.startswith(('/', '!')):
             return await handler(event, data)

        entities = message.entities or message.caption_entities or []
        for entity in entities:
             if entity.type == "bot_command":
                  # If there is ANY bot command in the message, skip replacement?
                  # User feedback was "Correct command detection... entities does NOT include caption commands".
                  # We merged lists above.
                  # If a command is present, usually we shouldn't replace it or the text around it if it's meant to be processed.
                  # Safe bet: skip.
                  return await handler(event, data)

        if not text_check:
            return await handler(event, data)

        # Optimization: Check if active before heavy unparsing
        settings = await db.get_settings(message.chat.id) or {}
        if not settings.get("replacements_active", True):
             return await handler(event, data)

        # Prepare HTML text to preserve style
        if entities:
            html_text = unparse(text_check, entities)
        else:
            html_text = html.escape(text_check)

        # Determine Media Type
        # Simplify to "text", "photo", "video", etc.
        # If it's just text, message.content_type is 'text'.
        # If it's media, it will be 'photo', 'video', etc.
        media_type = message.content_type
        is_album = bool(message.media_group_id)

        # Use Pipeline Logic
        new_text = await ContentPipeline.process_replacements(
            html_text,
            message.chat.id,
            media_type=media_type,
            is_album=is_album
        )

        if new_text != html_text:
            # Do NOT edit the message directly to avoid conflicts with Marginals.
            # Store the patched HTML in data for downstream middleware (Marginals).
            data["patched_html"] = new_text
            data["has_replacement"] = True

        return await handler(event, data)
