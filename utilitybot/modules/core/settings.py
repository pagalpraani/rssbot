# =============================================================================
# Module: Core
# Path: utilitybot/modules/core/settings.py
# Description: Provides core logic and data structures for settings.py.
# Scope: private | channel | group
# =============================================================================

from aiogram import Router, F, Bot, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from ...utils import settings_cache
from ...database.mongodb import db
from ...utils.settings_layout import SettingsRegistry, LayoutBuilder, SettingsCallback
from ...utils.permissions import owner_only
from ...utils.keyboards import get_cancel_kb
from ...utils.logger import get_logger
from ... import config
import html
import re

log = get_logger(__name__)
from datetime import datetime, timedelta, timezone

router = Router()

class SettingsState(StatesGroup):
    waiting_for_input = State()
    waiting_for_add_chat_id = State()

# --- Helper Functions ---
async def get_collection_data(chat_id: int, collection_name: str) -> dict:
    """Fetch settings data based on the collection name."""
    if chat_id == 0:
        # Developer/Global Settings
        return await db.db.settings.find_one({"_id": "bot_config"}) or {}

    return await db.get_settings(chat_id) or {}

async def update_collection_data(chat_id: int, collection_name: str, data: dict):
    """Update settings data based on the collection name."""
    if chat_id == 0:
        # Developer/Global Settings
        await db.db.settings.update_one({"_id": "bot_config"}, {"$set": data}, upsert=True)
        return

    await db.update_settings(chat_id, data)

@router.message(Command("settings", prefix="!/"), F.chat.type == "private")
@owner_only
async def settings_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != config.OWNER_ID:
        return

    # Delete previous settings message if it exists, to avoid duplicates
    data = await state.get_data()
    prev_msg_id = data.get("settings_msg_id")
    if prev_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prev_msg_id)
        except Exception:
            pass

    groups = await db.get_managed_groups(message.from_user.id)
    markup = LayoutBuilder.build_group_selector(groups, user_id=message.from_user.id)

    sent = await message.answer(
        "⚙️ <b>Global Settings Manager</b>\nSelect a chat to configure:",
        reply_markup=markup,
        parse_mode="HTML"
    )
    await state.update_data(settings_msg_id=sent.message_id)

    # Delete the command message to keep the chat clean
    try:
        await message.delete()
    except Exception:
        pass


@router.message(Command("settings", prefix="!/"), F.chat.type.in_({"group", "supergroup"}))
async def settings_handler_in_chat(message: types.Message, bot: Bot):
    """Run /settings directly inside a chat to jump straight to its module list."""
    if message.from_user.id != config.OWNER_ID:
        return
    await _open_module_selector_in_chat(message)

@router.channel_post(Command("settings", prefix="!/"))
async def settings_handler_in_channel(message: types.Message, bot: Bot):
    # Channel posts arrive as a separate update type from regular messages —
    # without this, /settings silently did nothing when run inside a channel.
    if not message.from_user or message.from_user.id != config.OWNER_ID:
        return
    await _open_module_selector_in_chat(message)

async def _open_module_selector_in_chat(message: types.Message):
    await db.add_managed_group(config.OWNER_ID, message.chat.id, message.chat.title or str(message.chat.id), message.chat.type)

    markup = LayoutBuilder.build_module_selector(message.chat, page=0)
    icon = "📢" if message.chat.type == "channel" else "💬"
    await message.reply(
        f"{icon} <b>{html.escape(message.chat.title or str(message.chat.id))}</b>\nSelect a module to configure:",
        reply_markup=markup,
        parse_mode="HTML"
    )

# --- Navigation Handler ---
@router.callback_query(SettingsCallback.filter(F.level.in_({"home", "dev_home", "mods", "dash", "refresh", "remove_confirm", "remove_exec", "admincache"})))
async def settings_nav_handler(callback: types.CallbackQuery, callback_data: SettingsCallback, bot: Bot, state: FSMContext):
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    # Auto-clear input state if navigating
    current_state = await state.get_state()
    if current_state == SettingsState.waiting_for_input:
        await state.clear()

    action = callback_data.level
    chat_id = callback_data.chat_id
    page = callback_data.page
    module_key = callback_data.mod

    if action == "home":
        groups = await db.get_managed_groups(callback.from_user.id)
        markup = LayoutBuilder.build_group_selector(groups, page, user_id=callback.from_user.id)

        try:
             await callback.message.edit_text(
                "⚙️ <b>Global Settings Manager</b>\nSelect a chat to configure:",
                reply_markup=markup,
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        finally:
            try:
                await callback.answer()
            except Exception:
                pass

    elif action == "dev_home":
        if callback.from_user.id != config.OWNER_ID:
            await callback.answer("Access denied.", show_alert=True)
            return

        markup = LayoutBuilder.build_dev_module_selector(page)
        try:
            await callback.message.edit_text(
                "👨‍💻 <b>Developer Tools</b>\nSelect a module:",
                reply_markup=markup,
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        finally:
            try:
                await callback.answer()
            except Exception:
                pass

    elif action == "mods":
        if not await db.is_group_managed_by_user(callback.from_user.id, chat_id):
            await callback.answer("Access denied.", show_alert=True)
            return

        try:
            chat = settings_cache.get_chat(chat_id)
            if chat is None:
                chat = await bot.get_chat(chat_id)
                settings_cache.set_chat(chat_id, chat)
            markup = LayoutBuilder.build_module_selector(chat, page)

            icon = "📢" if chat.type == "channel" else "💬"
            chat_link = f"@{chat.username}" if chat.username else "Private Chat"
            if not chat.username and chat.invite_link:
                 chat_link = chat.invite_link

            header_text = (
                f"{icon} <b>{html.escape(chat.title)}</b>\n"
                f"{chat_link}\n"
                f"ID: <code>{chat.id}</code>\n\n"
                f"Select a module:"
            )

            try:
                await callback.message.edit_text(header_text, reply_markup=markup, parse_mode="HTML")
            except TelegramBadRequest:
                pass
            finally:
                try:
                    await callback.answer()
                except Exception:
                    pass
        except TelegramNetworkError:
            # Network timeout — callback is likely already expired, silently drop
            try:
                await callback.answer("⏳ Request timed out. Please try again.", show_alert=True)
            except Exception:
                pass
        except Exception as e:
            try:
                await callback.answer(f"Chat not found: {str(e)}", show_alert=True)
            except Exception:
                pass

    elif action == "dash":
        if chat_id == 0:
            if callback.from_user.id != config.OWNER_ID:
                await callback.answer("Access denied.", show_alert=True)
                return
        elif not await db.is_group_managed_by_user(callback.from_user.id, chat_id):
            await callback.answer("Access denied.", show_alert=True)
            return

        schema = SettingsRegistry.get_module(module_key)
        if not schema:
            await callback.answer("Module not found.", show_alert=True)
            return

        collection = schema.get("db_collection", "settings")
        settings_data = await get_collection_data(chat_id, collection)

        if chat_id == 0:
            chat_title = "Global / Developer"
            chat_type = "private"
        else:
            try:
                chat = settings_cache.get_chat(chat_id)
                if chat is None:
                    chat = await bot.get_chat(chat_id)
                    settings_cache.set_chat(chat_id, chat)
                chat_title = chat.title
                chat_type = chat.type
            except TelegramNetworkError:
                try:
                    await callback.answer("⏳ Request timed out. Please try again.", show_alert=True)
                except Exception:
                    pass
                return
            except Exception as e:
                try:
                    await callback.answer(f"Chat not found: {str(e)}", show_alert=True)
                except Exception:
                    pass
                return

        text, markup = LayoutBuilder.build_dashboard(chat_id, module_key, settings_data, chat_title, chat_type)
        try:
            await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest:
            pass
        finally:
            try:
                await callback.answer()
            except Exception:
                pass

    elif action == "refresh":
         groups = await db.get_managed_groups(callback.from_user.id)
         # Invalidate chat cache for all managed groups so fresh names are fetched
         for group in groups:
             settings_cache.invalidate_chat(group['chat_id'])

         markup = LayoutBuilder.build_group_selector(groups, page, user_id=callback.from_user.id)
         try:
            await callback.message.edit_text("⚙️ <b>Global Settings Manager</b>\nSelect a chat to configure:", reply_markup=markup, parse_mode="HTML")
         except TelegramBadRequest:
             pass
         finally:
            try:
                await callback.answer()
            except Exception:
                pass

    elif action == "remove_confirm":
        chat_obj = settings_cache.get_chat(chat_id)
        if chat_obj is None:
            try:
                chat_obj = await bot.get_chat(chat_id)
            except Exception:
                pass
        title = html.escape(chat_obj.title) if chat_obj else str(chat_id)
        icon = "📢" if (chat_obj and chat_obj.type == "channel") else "👥"

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text="✅ Yes, Unlink",
                callback_data=SettingsCallback(level="remove_exec", chat_id=chat_id).pack()
            )],
            [types.InlineKeyboardButton(
                text="❌ Cancel",
                callback_data=SettingsCallback(level="mods", chat_id=chat_id).pack()
            )]
        ])
        await callback.message.edit_text(
            f"⚠️ <b>Unlink Chat?</b>\n\n"
            f"{icon} <b>{title}</b> will be removed from your Settings dashboard.\n\n"
            f"All module settings for this chat will remain intact in the database.\n"
            f"You can re-add it anytime using ➕ Add New Chat.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        try:
            await callback.answer()
        except Exception:
            pass

    elif action == "remove_exec":
        chat_obj = settings_cache.get_chat(chat_id)
        if chat_obj is None:
            try:
                chat_obj = await bot.get_chat(chat_id)
            except Exception:
                pass
        title = html.escape(chat_obj.title) if chat_obj else str(chat_id)
        icon = "📢" if (chat_obj and chat_obj.type == "channel") else "👥"

        if await db.remove_managed_group(callback.from_user.id, chat_id):
            settings_cache.invalidate_chat(chat_id)
            log.info(f"Chat {chat_id} unlinked from Settings by user {callback.from_user.id}")
            await callback.message.edit_text(
                f"✅ <b>Chat Unlinked</b>\n\n"
                f"{icon} <b>{title}</b> has been removed from your Settings dashboard.\n\n"
                f"You can re-add it anytime using ➕ Add New Chat.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                    types.InlineKeyboardButton(
                        text="◁ Back to Dashboard",
                        callback_data=SettingsCallback(level="home").pack()
                    )
                ]]),
                parse_mode="HTML"
            )
        else:
            await callback.answer("Failed to unlink.", show_alert=True)
        try:
            await callback.answer()
        except Exception:
            pass

    elif action == "admincache":
        from ...utils.admin_cache import clear_chat_cache
        clear_chat_cache(chat_id)
        await callback.answer("Admin cache cleared.", show_alert=True)

# --- Logic Handler (Edit) ---
@router.callback_query(SettingsCallback.filter(F.level == "edit"))
async def settings_edit_handler(callback: types.CallbackQuery, callback_data: SettingsCallback, bot: Bot):
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    chat_id = callback_data.chat_id
    module_key = callback_data.mod
    field_key = callback_data.field

    if chat_id == 0:
        if callback.from_user.id != config.OWNER_ID:
            await callback.answer("Access denied.", show_alert=True)
            return
    elif not await db.is_group_managed_by_user(callback.from_user.id, chat_id):
        await callback.answer("Access denied.", show_alert=True)
        return

    schema = SettingsRegistry.get_module(module_key)
    field_config = schema["fields"].get(field_key)
    if not field_config:
        return

    collection = schema.get("db_collection", "settings")
    settings_data = await get_collection_data(chat_id, collection)

    if field_config["type"] == "bool":
            current_val = settings_data.get(field_key, False)
            await update_collection_data(chat_id, collection, {field_key: not current_val})

    elif field_config["type"] == "select":
            current_val = settings_data.get(field_key)
            options = field_config.get("options", [])

            try:
                idx = options.index(current_val)
                new_idx = (idx + 1) % len(options)
                new_val = options[new_idx]
            except ValueError:
                if options:
                    new_val = options[0]
                else:
                    new_val = None

            if new_val is not None:
                await update_collection_data(chat_id, collection, {field_key: new_val})

    # Refresh dashboard
    settings_data = await get_collection_data(chat_id, collection)

    if chat_id == 0:
        chat_title = "Global / Developer"
        chat_type = "private"
    else:
        chat = await bot.get_chat(chat_id)
        chat_title = chat.title
        chat_type = chat.type

    text, markup = LayoutBuilder.build_dashboard(chat_id, module_key, settings_data, chat_title, chat_type)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

# --- Input Request Handler ---
@router.callback_query(SettingsCallback.filter(F.level == "input"))
async def settings_input_prompt_handler(callback: types.CallbackQuery, callback_data: SettingsCallback, state: FSMContext):
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    chat_id = callback_data.chat_id
    module_key = callback_data.mod
    field_key = callback_data.field

    if chat_id == 0:
        if callback.from_user.id != config.OWNER_ID:
            await callback.answer("Access denied.", show_alert=True)
            return
    elif not await db.is_group_managed_by_user(callback.from_user.id, chat_id):
        await callback.answer("Access denied.", show_alert=True)
        return

    schema = SettingsRegistry.get_module(module_key)
    field_config = schema["fields"].get(field_key)

    collection = schema.get("db_collection", "settings")
    settings_data = await get_collection_data(chat_id, collection)

    val = settings_data.get(field_key)
    current_val_str = str(val) if val is not None else "Not Set"

    await state.update_data(chat_id=chat_id, module_key=module_key, field_key=field_key)
    await state.set_state(SettingsState.waiting_for_input)

    prompt = f"Please enter the new value for <b>{field_config['label']}</b>.\n\nCurrent value: <code>{html.escape(current_val_str)}</code>\n\n"
    if field_config.get('description'):
        prompt += f"<i>{field_config['description']}</i>\n"

    if field_config['type'] == 'select':
            prompt += f"Options: {', '.join(field_config['options'])}"

    prompt += "\n\n<i>👇 Click below to cancel or type the value.</i>"

    # Check if this field should have a "Remove/Reset" button.
    # Fields declare this in their schema via allow_clear=True (preferred),
    # or fall back to the legacy hardcoded set for older modules.
    allow_clear = False
    clear_label = "🗑️ Remove / Clear"
    if field_config.get("allow_clear"):
        allow_clear = True
        clear_label = field_config.get("clear_label", "🗑️ Remove / Clear")
    elif field_config["type"] == "input":
        legacy_removable = {"log_channel", "log_channel_id", "approval_group_id", "pdf_footer_text"}
        if field_key in legacy_removable:
            allow_clear = True

    # Use the new inline cancel keyboard with optional clear capability
    markup = LayoutBuilder.build_cancel_keyboard(chat_id, module_key, allow_clear=allow_clear, clear_label=clear_label)

    try:
        await callback.message.edit_text(prompt, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

# --- Clear/Remove Handler ---
@router.callback_query(SettingsCallback.filter(F.level == "clear"))
async def settings_clear_handler(callback: types.CallbackQuery, callback_data: SettingsCallback, state: FSMContext, bot: Bot):
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    # Only called from input prompt state
    data = await state.get_data()
    chat_id = data.get('chat_id')
    module_key = data.get('module_key')
    field_key = data.get('field_key')

    if not chat_id or not module_key or not field_key:
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return

    schema = SettingsRegistry.get_module(module_key)
    collection = schema.get("db_collection", "settings")
    field_config = schema["fields"].get(field_key, {})

    # If the field declares an on_clear payload, apply that (supports multi-field atomic reset).
    # Otherwise fall back to simply nulling the field.
    on_clear = field_config.get("on_clear")
    if on_clear:
        await update_collection_data(chat_id, collection, on_clear)
    else:
        await update_collection_data(chat_id, collection, {field_key: None})

    await state.clear()
    clear_label_done = field_config.get("clear_label", "🗑️ Remove / Clear")
    await callback.answer(f"{clear_label_done} done.", show_alert=True)

    # Return to dashboard
    settings_data = await get_collection_data(chat_id, collection)
    if chat_id == 0:
        chat_title = "Global / Developer"
        chat_type = "private"
    else:
        chat = await bot.get_chat(chat_id)
        chat_title = chat.title
        chat_type = chat.type

    text, markup = LayoutBuilder.build_dashboard(chat_id, module_key, settings_data, chat_title, chat_type)

    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    except Exception:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")

# --- Cancel Input ---
@router.message(SettingsState.waiting_for_input, Command("cancel"))
async def cancel_input_handler(message: types.Message, state: FSMContext):
    if message.from_user.id != config.OWNER_ID:
        return
    data = await state.get_data()
    chat_id = data.get("chat_id")
    module_key = data.get("module_key")

    await state.clear()

    if chat_id and module_key:
        await message.reply("❌ Input cancelled. Please use the menu to navigate.", parse_mode="HTML")
    else:
        await message.reply("❌ Input cancelled.", parse_mode="HTML")

# --- Process Input ---
@router.message(SettingsState.waiting_for_input)
async def process_settings_input(message: types.Message, state: FSMContext, bot: Bot):
    if message.from_user.id != config.OWNER_ID:
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    module_key = data.get('module_key')
    field_key = data.get('field_key')

    if not chat_id or not module_key or not field_key:
        await message.reply("❌ Session expired. Please try again.", parse_mode="HTML")
        await state.clear()
        return

    schema = SettingsRegistry.get_module(module_key)
    field_config = schema["fields"][field_key]
    collection = schema.get("db_collection", "settings")

    if field_key == "logo_id":
        if message.document and getattr(message.document, 'mime_type', '') == 'image/png':
            input_value = message.document.file_id
        elif message.text:
            input_value = message.text
        else:
            await message.reply("❌ Invalid format. Please send an uncompressed PNG document or a File ID.")
            return
    else:
        input_value = message.text

    if not input_value and field_key != "logo_id":
        await message.reply("❌ Invalid input. Please send text.")
        return

    if field_config.get("validator"):
        is_valid, error_msg = field_config["validator"](input_value)
        if not is_valid:
            await message.reply(f"❌ Invalid input: {error_msg}\nPlease try again.", parse_mode="HTML")
            return

    final_value = input_value

    if field_config["type"] == "select":
        if input_value not in field_config["options"]:
            await message.reply(f"❌ Invalid option. Choose from: {', '.join(field_config['options'])}", parse_mode="HTML")
            return

    elif field_config["type"] == "list_input":
        # Robust split by comma or newline
        items = re.split(r'[,\n]', input_value)
        final_list = []
        for item in items:
            item = item.strip()
            if not item: continue
            if item.isdigit():
                final_list.append(int(item))
            else:
                final_list.append(item)
        final_value = final_list

    update_data = {}

    if field_config["type"] == "composite_time":
        parts = input_value.split()
        if len(parts) != 2:
             await message.reply("❌ Format Error: Please provide TWO times separated by space.\nExample: <code>09:00 21:00</code>", parse_mode="HTML")
             return
        start, end = parts
        update_data["start"] = start
        update_data["end"] = end
    elif field_config["type"] == "duration":
        # Check basic format
        if not re.match(r'^(\d+h)?\s*(\d+m)?$', input_value.lower().replace(" ", "")) and not re.match(r'^\d+$', input_value):
             await message.reply("❌ Invalid format. Use <code>2h</code>, <code>30m</code>, or <code>1h 30m</code>.", parse_mode="HTML")
             return

        duration_regex = re.compile(r'((?P<hours>\d+?)\s*h)?\s*((?P<minutes>\d+?)\s*m)?')
        parts = duration_regex.match(input_value.lower())

        time_params = {}
        if parts:
             time_params = {name: int(value) for name, value in parts.groupdict().items() if value}

        if not time_params:
             await message.reply("❌ Time cannot be 0.", parse_mode="HTML")
             return

        delta = timedelta(**time_params)

        now_utc = datetime.now(timezone.utc)
        paused_until = now_utc + delta
        update_data["paused_until"] = paused_until.isoformat()
    else:
        update_data[field_key] = final_value

    if update_data:
        await update_collection_data(chat_id, collection, update_data)

    await state.clear()

    settings_data = await get_collection_data(chat_id, collection)

    if chat_id == 0:
        chat_title = "Global / Developer"
        chat_type = "private"
    else:
        chat = await bot.get_chat(chat_id)
        chat_title = chat.title
        chat_type = chat.type

    text, markup = LayoutBuilder.build_dashboard(chat_id, module_key, settings_data, chat_title, chat_type)
    await message.reply(text, reply_markup=markup, parse_mode="HTML")

# =============================================================================
# Add New Chat — owner picks any chat the bot is already in via button
# =============================================================================

@router.callback_query(F.data == "settings_add_chat")
async def settings_add_chat_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """Show all bot_chats not yet linked, so the owner can pick one to add."""
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    all_chats = await db.get_all_bot_chats()
    managed = await db.get_managed_groups(callback.from_user.id)
    managed_ids = {g["chat_id"] for g in managed}

    available = [c for c in all_chats if c["chat_id"] not in managed_ids]

    if not available:
        await callback.answer("All chats the bot is in are already linked.", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    builder = InlineKeyboardBuilder()
    for chat in available[:20]:  # cap at 20 to keep keyboard manageable
        icon = "📢" if chat.get("type") == "channel" else "👥"
        label = f"{icon} {chat['title']}"
        builder.row(InlineKeyboardButton(
            text=label,
            callback_data=f"settings_link_chat_{chat['chat_id']}"
        ))
    builder.row(InlineKeyboardButton(text="✏️ Enter Chat ID manually", callback_data="settings_add_chat_manual"))
    builder.row(InlineKeyboardButton(text="◁ Back", callback_data=SettingsCallback(level="home").pack()))
    builder.adjust(1)

    await callback.message.edit_text(
        "➕ <b>Add New Chat</b>\n\nSelect a chat the bot is already in, or enter an ID manually:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_link_chat_"))
async def settings_link_chat_cb(callback: types.CallbackQuery, bot: Bot):
    """Link an existing bot_chat to managed_groups."""
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    chat_id = int(callback.data.split("_")[3])
    try:
        chat = await bot.get_chat(chat_id)
        await db.add_managed_group(callback.from_user.id, chat_id, chat.title, chat.type)
        chat_type_label = "Channel" if chat.type == "channel" else "Group"
        await callback.answer(f"✅ {chat_type_label} '{chat.title}' linked!", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Error: {e}", show_alert=True)
        return

    # Return to home
    groups = await db.get_managed_groups(callback.from_user.id)
    markup = LayoutBuilder.build_group_selector(groups, user_id=callback.from_user.id)

    try:
        await callback.message.edit_text(
            "⚙️ <b>Global Settings Manager</b>\nSelect a chat to configure:",
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data == "settings_add_chat_manual")
async def settings_add_chat_manual_cb(callback: types.CallbackQuery, state: FSMContext):
    """Prompt the owner to type a chat ID."""
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return
    await state.set_state(SettingsState.waiting_for_add_chat_id)
    await callback.message.edit_text(
        "✏️ <b>Enter Chat ID</b>\n\nSend the numeric Chat ID (e.g. <code>-1001234567890</code>).",
        parse_mode="HTML",
        reply_markup=get_cancel_kb("settings_cancel_add_chat")
    )
    await callback.answer()


@router.callback_query(F.data == "settings_cancel_add_chat")
async def settings_cancel_add_chat_cb(callback: types.CallbackQuery, state: FSMContext):
    """Cancel the add chat manual input."""
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("❌ Operation cancelled. Use /settings to continue.")
    finally:
        await callback.answer()


@router.message(SettingsState.waiting_for_add_chat_id, Command("cancel"))
async def settings_add_chat_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply("Cancelled. Use /settings to continue.")


@router.message(SettingsState.waiting_for_add_chat_id)
async def settings_add_chat_manual_input(message: types.Message, state: FSMContext, bot: Bot):
    """Process a manually entered chat ID."""
    if message.from_user.id != config.OWNER_ID:
        return
    raw = message.text.strip()
    try:
        chat_id = int(raw)
    except ValueError:
        await message.reply("❌ That doesn't look like a valid Chat ID. Send a number like <code>-1001234567890</code>.", parse_mode="HTML")
        return

    try:
        chat = await bot.get_chat(chat_id)
    except Exception as e:
        await message.reply(f"❌ Could not access that chat. Make sure the bot is a member.\n<code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return

    await db.add_managed_group(message.from_user.id, chat_id, chat.title, chat.type)
    await state.clear()
    chat_type_label = "Channel" if chat.type == "channel" else "Group"
    await message.reply(
        f"✅ {chat_type_label} <b>{html.escape(chat.title)}</b> linked successfully!\n\nUse /settings to manage it.",
        parse_mode="HTML"
    )
