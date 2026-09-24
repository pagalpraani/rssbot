# =============================================================================
# Module: Rate Limit Middleware
# Path: utils/rate_limit_middleware.py
# Description: Per-user rate limiting middleware for messages and callbacks.
#              Two separate classes — registered on dp.message and
#              dp.callback_query respectively — matching the pattern used
#              by every other middleware in this project.
# =============================================================================

import time
from collections import defaultdict
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from utils.logger import log

# ── Tunables ──────────────────────────────────────────────────────────────────
_MESSAGE_MIN_INTERVAL  = 0.5   # 500 ms between messages from the same user
_CALLBACK_MIN_INTERVAL = 0.3   # 300 ms between button taps from the same user
_MAX_TRACKED           = 10_000  # evict oldest half when dict exceeds this size
# ─────────────────────────────────────────────────────────────────────────────

_last_message:  dict[int, float] = defaultdict(float)
_last_callback: dict[int, float] = defaultdict(float)


def _sweep(d: dict) -> None:
    """Evict the oldest half of entries to keep memory bounded."""
    if len(d) > _MAX_TRACKED:
        cutoff = sorted(d.values())[len(d) // 2]
        for uid in [k for k, v in d.items() if v <= cutoff]:
            d.pop(uid, None)


class MessageRateLimitMiddleware(BaseMiddleware):
    """
    Registered on dp.message.outer_middleware().
    Drops messages that arrive faster than _MESSAGE_MIN_INTERVAL (500 ms).
    Silent drop — no reply sent — to avoid feedback loops where the
    rate-limit reply itself triggers another rate-limit.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None:
            # Channel posts or anonymous messages — pass through
            return await handler(event, data)

        now = time.monotonic()
        uid = user.id
        if now - _last_message[uid] < _MESSAGE_MIN_INTERVAL:
            log.debug(f"[RateLimit] Dropped message from user {uid}")
            return  # silently drop
        _last_message[uid] = now
        _sweep(_last_message)
        return await handler(event, data)


class CallbackRateLimitMiddleware(BaseMiddleware):
    """
    Registered on dp.callback_query.outer_middleware().
    Drops callback taps faster than _CALLBACK_MIN_INTERVAL (300 ms).
    Calls callback.answer() on drops so Telegram dismisses the loading
    spinner — otherwise the button stays "pressed" on the user's screen.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        uid = user.id
        if now - _last_callback[uid] < _CALLBACK_MIN_INTERVAL:
            log.debug(f"[RateLimit] Dropped callback from user {uid}")
            await event.answer()  # dismiss spinner, do nothing else
            return
        _last_callback[uid] = now
        _sweep(_last_callback)
        return await handler(event, data)
