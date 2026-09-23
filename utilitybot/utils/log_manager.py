# =============================================================================
# Module: Log Manager
# Path: utilitybot/utils/log_manager.py
# Description: Utility functions and helpers for operations related to Log Manager.
# =============================================================================

import asyncio
import html
import logging
from datetime import datetime, timezone
from aiogram import Bot, types
from .. import config
from ..database.mongodb import db
from . import settings_cache

class LogManager:
    _bot: Bot = None
    _dev_channel_id: int = config.DEV_LOG_CHANNEL

    @classmethod
    def set_bot(cls, bot: Bot):
        cls._bot = bot

    @classmethod
    async def _send(cls, chat_id: int, text: str):
        if not cls._bot or not chat_id:
            return
        try:
            await cls._bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            # Fallback to console if telegram send fails
            logging.error(f"LogManager failed to send to {chat_id}: {e}")

    @classmethod
    def _format_base(cls, level: str, module: str, message: str, context: str = None) -> str:
        icon_map = {
            "INFO": "ℹ️",
            "WARN": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
            "DEBUG": "🐛",
            "VERIFY": "✅",
            "ACTION": "📝"
        }
        icon = icon_map.get(level.upper(), "📝")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        text = f"<b>{icon} {level.upper()}: {module}</b>\n"
        text += f"<code>{timestamp} UTC</code>\n\n"
        text += f"{message}\n"

        if context:
            text += f"\n<b>Context:</b> {context}"

        return text

    @classmethod
    def _resolve_log_target(cls, obj) -> tuple:
        """
        Resolves ID and Name from User, Chat, or dict.
        Returns: (id, name, link_html)
        """
        if isinstance(obj, (types.User, types.Chat)):
            name = obj.full_name if hasattr(obj, "full_name") else obj.title
            return obj.id, html.escape(name or "Unknown"), f"<a href='tg://user?id={obj.id}'>{html.escape(name or 'Unknown')}</a>"
        elif isinstance(obj, dict):
            # Try to find standard fields
            id_val = obj.get("id") or obj.get("user_id") or obj.get("chat_id")
            name_val = obj.get("full_name") or obj.get("first_name") or obj.get("title") or obj.get("name") or "Unknown"
            return id_val, html.escape(str(name_val)), f"<a href='tg://user?id={id_val}'>{html.escape(str(name_val))}</a>"
        elif isinstance(obj, int):
            return obj, str(obj), f"<code>{obj}</code>"
        return None, "Unknown", "Unknown"

    @classmethod
    def _format_kv_log(cls, module: str, timestamp: str, action_name: str, actor, target = None, reason: str = None, duration: str = None, group_info: str = None, actor_role: str = None, **kwargs) -> str:
        """
        Formats log in Key-Value style for Moderation/Admin logs.
        [MODERATION]
        Time=...
        """
        header_tag = "MODERATION" if module.lower() == "admin" else module.upper()
        text = f"<b>[{header_tag}]</b>\n"
        text += f"<b>Time</b>={timestamp}\n"

        # Action
        text += f"<b>Action</b>={action_name.upper()}\n"

        # Actor
        if actor:
            a_id, a_name, a_link = cls._resolve_log_target(actor)
            actor_val = f"{a_link}"
            if actor_role:
                actor_val += f" ({actor_role})"
            actor_val += f" | <code>{a_id}</code>"
            text += f"<b>Actor</b>={actor_val}\n"

        # Target
        if target:
            t_id, t_name, t_link = cls._resolve_log_target(target)
            target_val = f"{t_link} | <code>{t_id}</code>"
            text += f"<b>Target</b>={target_val}\n"

        # Reason
        if reason:
            text += f"<b>Reason</b>=\"{html.escape(reason)}\"\n"

        # Duration
        if duration:
            text += f"<b>Duration</b>={duration}\n"

        # Group
        if group_info:
            text += f"<b>Group</b>={group_info}\n"

        # Extra kwargs
        for k, v in kwargs.items():
            text += f"<b>{k.title()}</b>={html.escape(str(v))}\n"

        return text

    @classmethod
    async def log_dev(cls, level: str, module: str, message: str, **kwargs):
        """
        Logs to the Global Dev Channel.
        """
        if not cls._dev_channel_id:
            return

        # Filter: Only log WARN, ERROR, CRITICAL, VERIFY to Telegram to reduce noise
        if level.upper() not in ["WARN", "ERROR", "CRITICAL", "VERIFY", "STARTUP"]:
            return

        # Format meta fields
        meta_text = ""
        for k, v in kwargs.items():
            meta_text += f"\n<b>{k.title()}:</b> {html.escape(str(v))}"

        text = cls._format_base(level, module, message)
        if meta_text:
            text += f"\n{meta_text}"

        await cls._send(cls._dev_channel_id, text)

    @classmethod
    async def log_group(cls, chat_id: int, level: str, module: str, message: str, actor = None, target = None, **kwargs):
        """
        Logs to the configured Group Log Channel.
        Supports KV formatting for Admin/Moderation actions.
        Accepts generic types for actor/target.
        """
        settings = await db.get_settings(chat_id) or {}
        log_channel = settings.get("log_channel_id")

        if not log_channel:
            return

        # Global on/off toggle for this chat
        if not settings.get("log_enabled", True):
            return

        # Determine if we should use KV format (Admin module actions)
        # We assume if 'actor' is present and module is Admin, we use KV.
        if module.lower() == "admin" and actor:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Fetch Group Info for Clickable Name
            group_title = "Group"
            group_link = ""
            if cls._bot:
                try:
                    chat = settings_cache.get_chat(chat_id)
                    if chat is None:
                        chat = await cls._bot.get_chat(chat_id)
                        settings_cache.set_chat(chat_id, chat)
                    group_title = html.escape(chat.title or "Group")
                    if chat.username:
                        group_link = f"https://t.me/{chat.username}"
                    elif chat.invite_link:
                        group_link = chat.invite_link
                except Exception:
                    pass

            if group_link:
                group_val = f"<a href='{group_link}'>{group_title}</a> | <code>{chat_id}</code>"
            else:
                group_val = f"{group_title} | <code>{chat_id}</code>"

            # Extract specific fields from kwargs if they exist (passed from handler)
            reason = kwargs.pop("reason", None)
            duration = kwargs.pop("duration", None)
            actor_role = kwargs.pop("actor_role", None)

            # The 'message' arg in log_group is usually the action description.
            # For KV, we treat it as the 'Action' name if it's short, or just pass it?
            # Existing calls: f"Banned user {target.id}" -> This is a full sentence.
            # We want Action="MUTE".
            # The caller should pass a cleaner action name if possible, or we parse it.
            # Current caller: LogManager.log_group(..., "Banned user ...", ...)
            # We can extract the action verb.

            action_name = message.split()[0].upper() # "Banned" -> "BANNED"
            # Normalize verbs
            if "BAN" in action_name: action_name = "BAN"
            elif "KICK" in action_name: action_name = "KICK"
            elif "MUTE" in action_name: action_name = "MUTE"
            elif "UNMUTE" in action_name: action_name = "UNMUTE"

            text = cls._format_kv_log(
                module, timestamp, action_name, actor, target,
                reason, duration, group_val, actor_role, **kwargs
            )

        else:
            # Fallback to standard base format for non-moderation group logs
            context_parts = []
            context_parts.append(f"Group: <code>{chat_id}</code>")

            if actor:
                _, _, a_link = cls._resolve_log_target(actor)
                context_parts.append(f"Actor: {a_link}")

            if target:
                _, _, t_link = cls._resolve_log_target(target)
                context_parts.append(f"Target: {t_link}")

            for k, v in kwargs.items():
                 context_parts.append(f"{k.title()}: {html.escape(str(v))}")

            context_str = "\n".join(context_parts)
            text = cls._format_base(level, module, message, context_str)

        await cls._send(log_channel, text)

    @classmethod
    async def log_channel(cls, channel_id: int, level: str, module: str, message: str, user = None, **kwargs):
        """
        Logs to the configured Channel Log Channel.
        """
        settings = await db.get_settings(channel_id) or {}
        log_channel_id = settings.get("log_channel_id")

        if not log_channel_id:
            return

        # Global on/off toggle for this chat
        if not settings.get("log_enabled", True):
            return

        context_parts = []
        try:
            if cls._bot:
                chat = settings_cache.get_chat(channel_id)
                if chat is None:
                    chat = await cls._bot.get_chat(channel_id)
                    settings_cache.set_chat(channel_id, chat)
                title = html.escape(chat.title)
                username = f"@{chat.username}" if chat.username else ""
                context_parts.append(f"Channel: <b>{title}</b> {username} (<code>{channel_id}</code>)")
            else:
                context_parts.append(f"Channel: <code>{channel_id}</code>")
        except Exception:
            context_parts.append(f"Channel: <code>{channel_id}</code>")

        if user:
             _, _, u_link = cls._resolve_log_target(user)
             context_parts.append(f"User: {u_link}")

        for k, v in kwargs.items():
             context_parts.append(f"{k.title()}: {html.escape(str(v))}")

        context_str = "\n".join(context_parts)
        text = cls._format_base(level, module, message, context_str)

        await cls._send(log_channel_id, text)
