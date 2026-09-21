# =============================================================================
# Module: Core Settings Dashboard
# Path: utilitybot/modules/core/settings.py
# Description: Generic /settings dashboard that renders and edits every module
#              registered in SettingsRegistry (blocklist, marginals, replacements,
#              watermark, logging, general). RSS has its own dedicated /rss
#              dashboard (modules/rss/dashboard.py) and is not driven from here.
# =============================================================================

import re
from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ...database.mongodb import db
from ...utils.settings_layout import SettingsRegistry, SettingsCallback, LayoutBuilder
from ...utils.permissions import is_admin
from ...utils import admin_cache
from ...utils.logger import get_logger

log = get_logger(__name__)
router = Router()


class SettingsInputState(StatesGroup):
    waiting_value = State()


# --- Entry points ---------------------------------------------------------

@router.message(Command("settings", prefix="!/"), F.chat.type.in_({"group", "supergroup", "channel"}))
async def settings_in_chat(message: Message, bot: Bot):
    """Run /settings directly inside the chat you want to configure."""
    if not message.from_user or not await is_admin(bot, message.chat.id, message.from_user.id):
        return

    # Link this chat to the caller so it also shows up in their PM selector.
    await db.add_managed_group(
        message.from_user.id, message.chat.id,
        message.chat.title or str(message.chat.id), message.chat.type,
    )

    kb = LayoutBuilder.build_module_selector(message.chat, page=0)
    await message.reply(
        f"⚙️ <b>{message.chat.title or 'This chat'}</b>\nSelect a module to configure:",
        parse_mode="HTML", reply_markup=kb,
    )


@router.message(Command("settings", prefix="!/"), F.chat.type == "private")
async def settings_in_pm(message: Message):
    """Run /settings in PM to pick from chats you've configured before."""
    groups = await db.get_managed_groups(message.from_user.id)
    kb = LayoutBuilder.build_group_selector(groups, page=0, user_id=message.from_user.id)
    await message.answer(
        "⚙️ <b>Settings</b>\n\nSelect a chat to configure:",
        parse_mode="HTML", reply_markup=kb,
    )


# --- Helpers ----------------------------------------------------------------

async def _chat_title_type(bot: Bot, chat_id: int):
    try:
        chat = await bot.get_chat(chat_id)
        return chat.title or str(chat_id), chat.type
    except Exception:
        return str(chat_id), "group"


async def _render_dashboard(chat_id: int, mod: str, bot: Bot):
    settings = await db.get_settings(chat_id)
    title, chat_type = await _chat_title_type(bot, chat_id)
    return LayoutBuilder.build_dashboard(chat_id, mod, settings, chat_title=title, chat_type=chat_type)


# --- Callback router ---------------------------------------------------------

@router.callback_query(SettingsCallback.filter())
async def settings_callback(callback: CallbackQuery, callback_data: SettingsCallback, state: FSMContext, bot: Bot):
    level = callback_data.level
    chat_id = callback_data.chat_id
    user_id = callback.from_user.id

    # Any navigation other than "input" cancels a pending text-input flow.
    if level != "input":
        await state.clear()

    # Re-verify the caller is still an admin of the target chat (except for the
    # chat-agnostic "home"/"dev_home" screens where chat_id is 0/unset).
    if chat_id and not await is_admin(bot, chat_id, user_id):
        await callback.answer("You're not an admin of that chat anymore.", show_alert=True)
        return

    if level == "home":
        groups = await db.get_managed_groups(user_id)
        kb = LayoutBuilder.build_group_selector(groups, page=callback_data.page, user_id=user_id)
        await callback.message.edit_text("⚙️ <b>Settings</b>\n\nSelect a chat to configure:", parse_mode="HTML", reply_markup=kb)

    elif level == "refresh":
        groups = await db.get_managed_groups(user_id)
        kb = LayoutBuilder.build_group_selector(groups, page=callback_data.page, user_id=user_id)
        await callback.message.edit_text("⚙️ <b>Settings</b>\n\nSelect a chat to configure:", parse_mode="HTML", reply_markup=kb)
        await callback.answer("Refreshed.")
        return

    elif level == "mods":
        title, chat_type = await _chat_title_type(bot, chat_id)
        chat_obj = await bot.get_chat(chat_id)
        kb = LayoutBuilder.build_module_selector(chat_obj, page=callback_data.page)
        await callback.message.edit_text(f"⚙️ <b>{title}</b>\nSelect a module to configure:", parse_mode="HTML", reply_markup=kb)

    elif level == "dev_home":
        kb = LayoutBuilder.build_dev_module_selector(page=callback_data.page)
        await callback.message.edit_text("👨‍💻 <b>Developer Settings</b>", parse_mode="HTML", reply_markup=kb)

    elif level == "dash":
        text, kb = await _render_dashboard(chat_id, callback_data.mod, bot)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif level == "edit":
        schema = SettingsRegistry.get_module(callback_data.mod)
        field = schema["fields"].get(callback_data.field) if schema else None
        if field:
            settings = await db.get_settings(chat_id)
            if field["type"] == "bool":
                current = settings.get(callback_data.field, field.get("default", False))
                await db.update_settings(chat_id, {callback_data.field: not current})
            elif field["type"] == "select":
                options = field.get("options", [])
                current = settings.get(callback_data.field, field.get("default", options[0] if options else None))
                if options:
                    idx = (options.index(current) + 1) % len(options) if current in options else 0
                    await db.update_settings(chat_id, {callback_data.field: options[idx]})
        text, kb = await _render_dashboard(chat_id, callback_data.mod, bot)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif level == "input":
        schema = SettingsRegistry.get_module(callback_data.mod)
        field = schema["fields"].get(callback_data.field) if schema else None
        if not field:
            await callback.answer("Field not found.", show_alert=True)
            return
        await state.set_state(SettingsInputState.waiting_value)
        await state.update_data(chat_id=chat_id, mod=callback_data.mod, field=callback_data.field)

        label = field.get("label", callback_data.field)
        prompt = f"✏️ Send the new value for <b>{label}</b>."
        desc = field.get("description")
        if desc:
            prompt += f"\n<i>{desc}</i>"
        if field["type"] == "list_input":
            prompt += "\n\nSend items separated by commas or one per line."

        allow_clear = bool(field.get("allow_clear"))
        kb = LayoutBuilder.build_cancel_keyboard(chat_id, callback_data.mod, allow_clear=allow_clear)
        await callback.message.edit_text(prompt, parse_mode="HTML", reply_markup=kb)

    elif level == "clear":
        data = await state.get_data()
        field_key = data.get("field")
        if field_key and data.get("chat_id") == chat_id and data.get("mod") == callback_data.mod:
            await db.update_settings(chat_id, {field_key: None})
        await state.clear()
        text, kb = await _render_dashboard(chat_id, callback_data.mod, bot)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif level == "admincache":
        admin_cache.clear_chat_cache(chat_id)
        await callback.answer("Admin cache cleared.")
        return

    elif level == "remove_confirm":
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yes, Unlink", callback_data=SettingsCallback(level="remove", chat_id=chat_id).pack()),
            InlineKeyboardButton(text="❌ Cancel", callback_data=SettingsCallback(level="mods", chat_id=chat_id).pack()),
        ]])
        await callback.message.edit_text("🗑️ Unlink this chat from your settings list?\n\n(This won't delete any of its stored settings.)", reply_markup=kb)

    elif level == "remove":
        await db.remove_managed_group(user_id, chat_id)
        groups = await db.get_managed_groups(user_id)
        kb = LayoutBuilder.build_group_selector(groups, page=0, user_id=user_id)
        await callback.message.edit_text("⚙️ <b>Settings</b>\n\nSelect a chat to configure:", parse_mode="HTML", reply_markup=kb)

    else:
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "settings_add_chat")
async def add_chat_callback(callback: CallbackQuery):
    await callback.answer(
        "Add me as admin to your channel or group, then run /settings inside it to link it here.",
        show_alert=True,
    )


# --- Text/list input receiver -------------------------------------------------

@router.message(StateFilter(SettingsInputState.waiting_value))
async def settings_input_received(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    chat_id = data.get("chat_id")
    mod = data.get("mod")
    field_key = data.get("field")
    if not (chat_id and mod and field_key):
        await state.clear()
        return

    schema = SettingsRegistry.get_module(mod)
    field = schema["fields"].get(field_key) if schema else None
    if not field:
        await state.clear()
        return

    # Accept an uploaded document/photo file_id for fields that expect one
    # (e.g. watermark logo), otherwise fall back to the message text.
    if message.document:
        raw = message.document.file_id
    elif message.photo:
        raw = message.photo[-1].file_id
    else:
        raw = (message.text or "").strip()

    if field["type"] == "list_input":
        value = [x.strip() for x in re.split(r"[,\n]", raw) if x.strip()]
    else:
        validator = field.get("validator")
        if validator:
            ok, err = validator(raw)
            if not ok:
                await message.reply(f"❌ {err}")
                return
        # Store plain numeric input (e.g. a log channel ID) as an int.
        value = int(raw) if re.fullmatch(r"-?\d+", raw) else raw

    await db.update_settings(chat_id, {field_key: value})
    await state.clear()

    text, kb = await _render_dashboard(chat_id, mod, bot)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)
