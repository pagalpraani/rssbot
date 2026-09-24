# =============================================================================
# Module: Permissions
# Path: utils/permissions.py
# Description: Utility functions and helpers for operations related to Permissions.
# =============================================================================

from functools import wraps
from aiogram.types import Message
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
import config
from utils import admin_cache
from utils.logger import get_logger

log = get_logger(__name__)

def owner_only(func):
    """
    Decorator to restrict access to the bot owner.
    Supports both Message and CallbackQuery.
    Handles Channel Posts where from_user might be None.
    """
    @wraps(func)
    async def wrapped(event, *args, **kwargs):
        from aiogram.types import Message, CallbackQuery

        user_id = None
        if isinstance(event, Message):
            if event.from_user:
                user_id = event.from_user.id
            elif event.sender_chat:
                # If message is from a channel (anonymous admin or channel post), check if it matches owner logic?
                # Usually channels don't have user IDs.
                # If an owner posts in a channel, Telegram sends it as channel post?
                # If "Sign Messages" is on, `from_user` might be present? No, channel posts come as `channel_post` update type.
                # In aiogram, `message` object is used for both.
                # If `event` is a channel post, `from_user` is None.
                # We can't verify owner ID from a channel post unless the owner is the one triggering it via a user-bot or similar, which is not the case here.
                # However, typically the owner interacts with the bot via PM or Group commands.
                # Channel commands are tricky.
                # If we want to support channel commands for Owner Only, the owner must send them as themselves?
                # Channel admins cannot send messages "as themselves" in the channel easily unless they link a group.
                # But the user asked for this.
                # For safety, if we can't identify the user, we DENY access to owner-only commands.
                pass
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id != config.OWNER_ID:
            if isinstance(event, CallbackQuery):
                await event.answer("Owner only.", show_alert=True)
            return

        return await func(event, *args, **kwargs)
    return wrapped

async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Checks if a user is an administrator or owner in a given chat, using a cache
    to minimize API calls.
    """
    if user_id == config.OWNER_ID:
        return True

    # Try to get admin list from cache
    cached_admin_ids = admin_cache.get_admin_ids(chat_id)
    if cached_admin_ids is not None:
        return user_id in cached_admin_ids

    # If cache is empty or expired, fetch from Telegram API
    try:
        admins = await bot.get_chat_administrators(chat_id)
        admin_ids = {admin.user.id for admin in admins}

        # Add the bot owner to the set of admins implicitly
        admin_ids.add(config.OWNER_ID)

        # Update the cache
        admin_cache.set_admin_ids(chat_id, admin_ids)

        return user_id in admin_ids
    except Exception as e:
        if isinstance(e, TelegramBadRequest) and "member list is inaccessible" in str(e):
            log.debug(f"Could not get admin list for chat {chat_id} (likely channel non-admin): {e}")
        else:
            log.error(f"Failed to get chat administrators for chat {chat_id}: {e}")

        # In case of an error, fall back to a single check to avoid locking out admins
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            return member.status in ['administrator', 'creator']
        except Exception:
            return False
