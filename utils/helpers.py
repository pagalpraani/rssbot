# =============================================================================
# Module: Helpers
# Path: utils/helpers.py
# Description: Utility functions and helpers for operations related to Helpers.
# Scope: private | channel
# =============================================================================

from aiogram.types import Message, User
from typing import Optional
import html
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from utils.logger import get_logger

log = get_logger(__name__)

async def reply_to_owner(
    message: Message,
    text: str,
    parse_mode: str = "HTML",
    **kwargs
):
    """
    Sends a reply to the owner in the chat where the command was issued.
    If 'command' cleaning is enabled, logs the bot's reply for deletion.
    """
    try:
        await message.reply(text=text, parse_mode=parse_mode, **kwargs)
    except TelegramAPIError:
        # Fallback for safety, though it should rarely be needed now.
        try:
            await message.bot.send_message(
                chat_id=message.from_user.id,
                text=f"⚠️ <b>Reply Failed in {html.escape(message.chat.title)}</b>\n{text}",
                parse_mode=parse_mode,
                **kwargs
            )
        except Exception:
            pass

async def get_target_user(bot: Bot, message: Message, args: list) -> Optional[User]:
    """
    Resolves a target user from a message.
    Priority:
    1. Reply to a message.
    2. Mention (text_mention or @username) in the command arguments.
    3. User ID in the command arguments.
    4. Username string in the command arguments (via get_chat).
    """
    # 1. Check for reply
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    # 2. Check for text mentions in entities
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                return entity.user
            # Note: 'mention' entities (e.g. @username) don't carry the User object.
            # We have to extract the text and resolve it.

    # If no args provided, we can't proceed
    if len(args) < 2:
        return None

    # The argument usually follows the command (e.g., /cmd arg1)
    # args[0] is command, args[1] is the potential user identifier
    # However, some commands might have subcommands (e.g. /whitelist add <user>).
    # So we assume the last argument is the user, or the caller handles parsing args.
    # For simplicity, let's assume the caller passes the specific argument string or we check the last arg.
    # Actually, let's just check args passed to this function.
    # If the caller passes the split args list, we look at args[1] or args[-1].
    # Let's refine: the caller should pass the specific identifier string if possible,
    # or we try to find it in args.

    # Let's iterate through args to find something that looks like an ID or Username
    for arg in args[1:]:
        # 3. Check for User ID
        if arg.isdigit():
            try:
                # Try to get chat member to resolve the user object if possible (works if user in chat)
                # Or get_chat (works if bot met user)
                try:
                    chat_member = await bot.get_chat_member(message.chat.id, int(arg))
                    return chat_member.user
                except TelegramAPIError:
                    # Fallback: try get_chat
                    chat = await bot.get_chat(int(arg))
                    # Chat object might not have all User fields, but it's compatible for ID/Name
                    # We can cast Chat to User if it's a private chat type?
                    # No, better to return a dummy User object if strictly needed, but we need User object.
                    # Actually, bot.get_chat returns a Chat object.
                    # If type is 'private', it represents a user.
                    if chat.type == 'private':
                        # Construct a User-like object or return the Chat object?
                        # The calling code expects a User object (id, full_name, username).
                        # Chat object has these fields too.
                        # Let's construct a User object to be safe.
                        return User(id=chat.id, is_bot=False, first_name=chat.first_name, last_name=chat.last_name, username=chat.username)
            except Exception:
                pass

        # 4. Check for Username string
        if arg.startswith("@"):
            try:
                chat = await bot.get_chat(arg)
                if chat.type == 'private':
                     return User(id=chat.id, is_bot=False, first_name=chat.first_name, last_name=chat.last_name, username=chat.username)
            except Exception:
                pass

    return None
