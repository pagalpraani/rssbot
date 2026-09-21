# =============================================================================
# Module: Settings Layout
# Path: utilitybot/utils/settings_layout.py
# Description: Utility functions and helpers for operations related to Settings
#              Layout.
# Scope: channel | group
# =============================================================================

import html
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Chat
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from .. import config

class SettingsRegistry:
    _modules: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register_module(cls, key: str, schema: Dict[str, Any]):
        cls._modules[key] = schema

    @classmethod
    def get_module(cls, key: str) -> Optional[Dict[str, Any]]:
        return cls._modules.get(key)

    @classmethod
    def get_all_modules(cls) -> Dict[str, Dict[str, Any]]:
        return cls._modules

class SettingsCallback(CallbackData, prefix="set", sep=":"):
    level: str
    chat_id: int = 0
    mod: str = ""
    field: str = ""
    page: int = 0

class LayoutBuilder:
    @staticmethod
    def chunk_list(data: List[Any], chunk_size: int):
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    @staticmethod
    def build_group_selector(groups: List[Dict], page: int = 0, user_id: int = 0) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        chunk_size = 10
        # Sort: channels first, then groups, each alphabetically by title
        groups = sorted(groups, key=lambda g: (0 if g.get('chat_type') == 'channel' else 1, g.get('chat_title', '').lower()))
        chunks = list(LayoutBuilder.chunk_list(groups, chunk_size))

        if not chunks and not groups:
             builder.button(text="⚠️ No chats linked yet.", callback_data="ignore")
             builder.adjust(1)
             builder.row(InlineKeyboardButton(text="➕ Add New Chat", callback_data="settings_add_chat"))
             if user_id == config.OWNER_ID:
                 builder.row(InlineKeyboardButton(text="👨‍💻 Developer", callback_data=SettingsCallback(level="dev_home").pack()))
             return builder.as_markup()

        current_chunk = chunks[page] if chunks and page < len(chunks) else []

        for group in current_chunk:
            icon = "👥"
            chat_type = group.get('chat_type')
            if chat_type == "channel":
                icon = "📢"
            elif chat_type == "private":
                icon = "👤"

            title = group['chat_title']
            if len(title) > 30:
                title = title[:27] + "..."

            builder.button(
                text=f"{icon} {title}",
                callback_data=SettingsCallback(level="mods", chat_id=group['chat_id']).pack()
            )
        builder.adjust(1) # 1-Column for Group Selector

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◁ Prev Page", callback_data=SettingsCallback(level="home", page=page-1).pack()))

        if len(chunks) > 1:
            nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{len(chunks)} ({len(groups)})", callback_data="ignore"))

        if page < len(chunks) - 1:
            nav_buttons.append(InlineKeyboardButton(text="Next Page ▷", callback_data=SettingsCallback(level="home", page=page+1).pack()))

        if nav_buttons:
            builder.row(*nav_buttons)

        builder.row(
            InlineKeyboardButton(text="➕ Add New Chat", callback_data="settings_add_chat"),
            InlineKeyboardButton(text="🔄 Refresh", callback_data=SettingsCallback(level="refresh", page=page).pack())
        )

        if user_id == config.OWNER_ID:
            builder.row(InlineKeyboardButton(text="👨‍💻 Developer", callback_data=SettingsCallback(level="dev_home").pack()))

        return builder.as_markup()

    @staticmethod
    def build_dev_module_selector(page: int = 0) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        modules = SettingsRegistry.get_all_modules()

        available_modules = []
        for key, schema in modules.items():
            if schema.get("category") == "Developer":
                schema_copy = schema.copy()
                schema_copy["key"] = key
                available_modules.append(schema_copy)

        if not available_modules:
            builder.button(text="⚠️ No developer modules found.", callback_data="ignore")
            builder.row(InlineKeyboardButton(text="◁ Back", callback_data=SettingsCallback(level="home").pack()))
            return builder.as_markup()

        # Sort alphabetically
        available_modules.sort(key=lambda x: x["name"])

        chunk_size = 8
        chunks = list(LayoutBuilder.chunk_list(available_modules, chunk_size))
        current_chunk = chunks[page] if chunks and page < len(chunks) else []

        for schema in current_chunk:
            icon = schema.get("icon", "🔧")
            text = f"{icon} {schema['name']}" if icon else schema['name']
            # Use chat_id=0 for global/developer settings
            builder.button(
                text=text,
                callback_data=SettingsCallback(level="dash", chat_id=0, mod=schema['key']).pack()
            )

        builder.adjust(2)

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◁ Prev Page", callback_data=SettingsCallback(level="dev_home", page=page-1).pack()))

        if len(chunks) > 1:
            nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{len(chunks)} ({len(available_modules)})", callback_data="ignore"))

        if page < len(chunks) - 1:
            nav_buttons.append(InlineKeyboardButton(text="Next Page ▷", callback_data=SettingsCallback(level="dev_home", page=page+1).pack()))

        if nav_buttons:
            builder.row(*nav_buttons)

        builder.row(InlineKeyboardButton(text="◁ Back", callback_data=SettingsCallback(level="home").pack()))
        return builder.as_markup()

    @staticmethod
    def build_module_selector(chat: Chat, page: int = 0) -> InlineKeyboardMarkup:
        chat_id = chat.id
        chat_type = chat.type

        builder = InlineKeyboardBuilder()
        modules = SettingsRegistry.get_all_modules()

        available_modules = []
        for key, schema in modules.items():
            # Skip Developer modules in standard chat selector
            if schema.get("category") == "Developer":
                continue

            supported_types = schema.get("supported_chat_types", ["group", "supergroup"])
            if chat_type in supported_types:
                schema_copy = schema.copy()
                schema_copy["key"] = key
                schema_copy["order"] = schema.get("order", 999)
                available_modules.append(schema_copy)

        if not available_modules:
            builder.button(text="⚠️ No modules available for this chat type.", callback_data="ignore")
            builder.row(InlineKeyboardButton(text="◁ Back", callback_data=SettingsCallback(level="home").pack()))
            return builder.as_markup()

        # Sort alphabetically by name
        available_modules.sort(key=lambda x: x["name"])

        # 2-column list with pagination
        chunk_size = 8
        chunks = list(LayoutBuilder.chunk_list(available_modules, chunk_size))
        current_chunk = chunks[page] if chunks and page < len(chunks) else []

        for schema in current_chunk:
            builder.button(
                text=schema['name'],
                callback_data=SettingsCallback(level="dash", chat_id=chat_id, mod=schema['key']).pack()
            )

        builder.adjust(2) # 2-Column Grid

        chat_url = None
        if chat.username:
            chat_url = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            chat_url = chat.invite_link

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◁ Prev Page", callback_data=SettingsCallback(level="mods", chat_id=chat_id, page=page-1).pack()))

        if len(chunks) > 1:
            nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{len(chunks)} ({len(available_modules)})", callback_data="ignore"))

        if page < len(chunks) - 1:
            nav_buttons.append(InlineKeyboardButton(text="Next Page ▷", callback_data=SettingsCallback(level="mods", chat_id=chat_id, page=page+1).pack()))

        if nav_buttons:
            builder.row(*nav_buttons)

        # Open Chat + Refresh Cache on one row
        cache_btn = InlineKeyboardButton(text="🔄 Cache", callback_data=SettingsCallback(level="admincache", chat_id=chat_id).pack())
        if chat_url:
            builder.row(
                InlineKeyboardButton(text="🔗 Open Chat", url=chat_url),
                cache_btn
            )
        else:
            builder.row(cache_btn)

        # Unlink + Back on one row
        builder.row(
            InlineKeyboardButton(text="🗑️ Unlink Chat", callback_data=SettingsCallback(level="remove_confirm", chat_id=chat_id).pack()),
            InlineKeyboardButton(text="◁ Back", callback_data=SettingsCallback(level="home").pack())
        )

        return builder.as_markup()

    @staticmethod
    def build_dashboard(chat_id: int, module_key: str, settings: Dict[str, Any], chat_title: str = "Chat", chat_type: str = "group") -> Tuple[str, InlineKeyboardMarkup]:
        schema = SettingsRegistry.get_module(module_key)

        back_level = "dev_home" if chat_id == 0 else "mods"

        if not schema:
            return "Module not found", InlineKeyboardBuilder().button(text="◁ Back", callback_data=SettingsCallback(level="home").pack()).as_markup()

        builder = InlineKeyboardBuilder()
        fields = schema.get("fields", {})

        # Filter fields based on chat_type
        filtered_fields = {}
        for k, v in fields.items():
            req_types = v.get("required_chat_types")
            if not req_types or chat_type in req_types:
                filtered_fields[k] = v

        status_lines = [
            f"⚙️ <b>{schema['name']} Settings</b>",
            f"📌 Chat: <b>{html.escape(chat_title)}</b>",
            "────────────────────"
        ]

        for field_key, field_config in filtered_fields.items():
            value = settings.get(field_key)
            if value is None and "default" in field_config:
                value = field_config["default"]
            label = field_config.get("label", field_key)
            field_type = field_config.get("type")

            display_val = str(value)

            if value is None:
                display_val = "Not Set"
            elif field_type == "bool":
                if value:
                    label_text = field_config.get("text_on", "Enabled")
                    display_val = f'<b>{label_text}</b>'
                else:
                    label_text = field_config.get("text_off", "Disabled")
                    display_val = f'<b>{label_text}</b>'  
            elif field_type == "list_input":
                if isinstance(value, list) and value:
                     full_str = ", ".join(map(str, value))
                     if len(full_str) > 100:
                         display_val = full_str[:97] + "..."
                     else:
                         display_val = full_str
                else:
                     display_val = "Empty"
            elif field_type == "input":
                # Show a short preview of the stored text; strip newlines for single-line display
                preview = str(value).replace("\n", " ").strip()
                if len(preview) > 50:
                    preview = preview[:47] + "…"
                display_val = f"<code>{html.escape(preview)}</code>"
            elif field_type == "select":
                 display_val = str(value).title()
            elif field_type == "composite_time":
                start = settings.get("start", "N/A")
                end = settings.get("end", "N/A")
                display_val = f"{start} - {end}"
            elif field_type == "duration":
                paused_until_iso = settings.get("paused_until")
                display_val = "▶️ Active"

                if paused_until_iso:
                    try:
                        paused_until = datetime.fromisoformat(paused_until_iso)
                        if paused_until.tzinfo is None:
                             paused_until = paused_until.replace(tzinfo=timezone.utc)
                        else:
                             paused_until = paused_until.astimezone(timezone.utc)

                        now_utc = datetime.now(timezone.utc)

                        if paused_until > now_utc:
                            tz_name = settings.get("timezone", "Asia/Kolkata")
                            try:
                                target_tz = ZoneInfo(tz_name)
                                paused_until_local = paused_until.astimezone(target_tz)
                                remaining = paused_until - now_utc
                                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                                minutes, _ = divmod(remainder, 60)
                                offset = paused_until_local.strftime('%z')
                                offset_formatted = f"{offset[:3]}:{offset[3:]}"
                                display_val = (f"⏸️ Until {paused_until_local.strftime('%H:%M')} ({offset_formatted})\n"
                                               f"   ({hours}h {minutes}m left)")
                            except (ZoneInfoNotFoundError, KeyError):
                                display_val = f"⏸️ Until {paused_until.strftime('%H:%M')} UTC (Invalid TZ)"
                        else:
                            display_val = "▶️ Active"
                    except (ValueError, TypeError):
                        pass

            if not field_config.get("hidden", False):
                status_lines.append(f"<b>{label}:</b> {display_val}")

        # Build rows: pair bool + its next input/list/composite if adjacent
        field_list = list(filtered_fields.items())
        all_buttons = []
        for fk, fc in field_list:
            v = settings.get(fk)
            if v is None and "default" in fc:
                v = fc["default"]
            ft = fc.get("type")
            label = fc.get("label", fk)

            btn_text = f"Change {label}"
            cb = SettingsCallback(level="edit", chat_id=chat_id, mod=module_key, field=fk).pack()

            if ft == "bool":
                btn_text = f"{label}: 🟢 Enabled" if v else f"{label}: 🔴 Disabled"
            elif ft == "select":
                btn_text = f"Switch {label}"
            elif ft == "input":
                btn_text = f"{fc.get('icon', '✏️')} Set {label}"
                cb = SettingsCallback(level="input", chat_id=chat_id, mod=module_key, field=fk).pack()
            elif ft == "composite_time":
                start = settings.get("start", "00:00")
                end = settings.get("end", "00:00")
                btn_text = f"Set Time: {start} {end}"
                cb = SettingsCallback(level="input", chat_id=chat_id, mod=module_key, field=fk).pack()
            elif ft == "list_input":
                btn_text = f"{fc.get('icon', '📋')} Manage {label}"
                cb = SettingsCallback(level="input", chat_id=chat_id, mod=module_key, field=fk).pack()
            elif ft == "duration":
                btn_text = f"⏸️ {label}"
                cb = SettingsCallback(level="input", chat_id=chat_id, mod=module_key, field=fk).pack()

            all_buttons.append(InlineKeyboardButton(text=btn_text, callback_data=cb))

        # Build rows: pair bool + its next input/list/composite if adjacent
        keyboard_rows = []
        i = 0
        while i < len(all_buttons):
            fk, fc = field_list[i]
            ft = fc.get("type")
            if ft == "bool" and i + 1 < len(all_buttons):
                next_fk, next_fc = field_list[i + 1]
                next_ft = next_fc.get("type")
                if next_ft in ("input", "list_input", "composite_time"):
                    keyboard_rows.append([all_buttons[i], all_buttons[i + 1]])
                    i += 2
                    continue
            keyboard_rows.append([all_buttons[i]])
            i += 1

        keyboard_rows.append([InlineKeyboardButton(
            text="◁ Back",
            callback_data=SettingsCallback(level=back_level, chat_id=chat_id).pack()
        )])

        return "\n".join(status_lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    @staticmethod
    def build_cancel_keyboard(
        chat_id: int,
        module_key: str,
        allow_clear: bool = False,
        clear_label: str = "🗑️ Remove / Clear",
    ) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()

        # If input can be cleared/reset (e.g. Log Channel, Welcome Text)
        if allow_clear:
            builder.button(text=clear_label, callback_data=SettingsCallback(level="clear", chat_id=chat_id, mod=module_key).pack())
            builder.adjust(1)  # Clear button in its own row

        builder.button(text="❌ Cancel", callback_data=SettingsCallback(level="dash", chat_id=chat_id, mod=module_key).pack())
        return builder.as_markup()
