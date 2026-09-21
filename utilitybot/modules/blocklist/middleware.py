# =============================================================================
# Module: Blocklist
# Path: utilitybot/modules/blocklist/middleware.py
# Description: Middleware implementation for the Blocklist module. Intercepts and
#              processes updates before handlers.
# =============================================================================

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from typing import Callable, Dict, Any, Awaitable
from ...database.mongodb import db
from ...utils.logger import get_logger
from .utils import check_text_blocking

log = get_logger(__name__)

class BlocklistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:

        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event

        # Check if the message is a command entity
        # We allow commands to pass through so other handlers can process them
        if message.entities:
            for entity in message.entities:
                if entity.type == "bot_command" and entity.offset == 0:
                    return await handler(event, data)

        text = message.text or message.caption or ""
        if not text:
            return await handler(event, data)

        settings = await db.get_settings(message.chat.id) or {}

        blocked_keyword = check_text_blocking(text, settings, chat_id=message.chat.id)

        if blocked_keyword:
            try:
                await message.delete()
                log.info(f"Deleted message {message.message_id} in chat {message.chat.id} due to blocked keyword: {blocked_keyword}")
                # Stop propagation (do not call handler)
                return
            except Exception as e:
                log.error(f"Failed to delete message {message.message_id} in chat {message.chat.id}: {e}")
                # If deletion fails, we probably shouldn't propagate either
                return

        # If no block, proceed
        return await handler(event, data)
