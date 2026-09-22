# =============================================================================
# Module: RSS Dashboard
# Path: utilitybot/modules/rss/dashboard.py
# Description: Owner-only interactive dashboard managing global RSS feed settings via FSM prompts.
# =============================================================================

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from utilitybot.database.mongodb import db
from utilitybot import config
from utilitybot.utils.logger import log
from utilitybot.utils.keyboards import get_cancel_kb

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from typing import Any, Callable, Awaitable

class _RSSOwnerMiddleware(BaseMiddleware):
    """
    Router-level middleware — rejects every update (callback or message)
    that does not originate from the bot owner.
    Fixes C-1: all 29 previously unprotected RSS dashboard handlers.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from aiogram.types import CallbackQuery, Message
        user = None
        if isinstance(event, CallbackQuery):
            user = event.from_user
        elif isinstance(event, Message):
            user = event.from_user

        if user is None or user.id != config.OWNER_ID:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Owner only.", show_alert=True)
            return  # drop silently for messages

        return await handler(event, data)

router = Router(name="rss_dashboard_router")
# Apply owner gate to every handler on this router
router.callback_query.middleware(_RSSOwnerMiddleware())
router.message.middleware(_RSSOwnerMiddleware())

def _sort_chats(chats: list) -> list:
    """Sort chats: channels first, then groups, each alphabetically by title."""
    return sorted(chats, key=lambda c: (0 if c.get("type") == "channel" else 1, c.get("title", "").lower()))


def get_pagination_keyboard(items: list, page: int, items_per_page: int, callback_prefix: str, chat_id: int = None, button_formatter=None) -> InlineKeyboardMarkup:
    total_pages = (len(items) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = start + items_per_page
    page_items = items[start:end]

    keyboard = []
    for item in page_items:
        if button_formatter:
            keyboard.append([button_formatter(item)])
        else:
            keyboard.append([InlineKeyboardButton(text=item['title'], callback_data=f"{callback_prefix}{item['chat_id']}")])

    nav_row = []
    if total_pages > 1:
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◁ Prev Page", callback_data=f"{callback_prefix}page_{page-1}_{chat_id}"))
        nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages} ({len(items)})", callback_data="rss_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="Next Page ▷", callback_data=f"{callback_prefix}page_{page+1}_{chat_id}"))
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def build_rss_home():
    """Builds the (text, keyboard) for the RSS dashboard's chat-selector home screen."""
    chats = _sort_chats(await db.get_all_bot_chats())
    if not chats:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add New Chat", callback_data="rss_add_new_chat")]
        ])
        return "📡 <b>RSS Dashboard</b>\n\nNo chats linked yet. Add me to a channel or group as admin, then tap below to link it.", kb

    kb = get_pagination_keyboard(
        chats, 0, 10, "rss_chat_",
        button_formatter=lambda i: InlineKeyboardButton(
            text=("📢 " if i.get("type") == "channel" else "👥 ") + i["title"],
            callback_data=f"rss_chat_{i['chat_id']}"
        )
    )
    kb.inline_keyboard.append([InlineKeyboardButton(text="➕ Add New Chat", callback_data="rss_add_new_chat")])
    return "📡 <b>RSS Dashboard</b>\n\nSelect a chat to manage:", kb


@router.message(Command("rss"))
async def rss_dashboard_cmd(message: Message, bot: Bot):
    if message.chat.type != "private" or message.from_user.id != config.OWNER_ID:
        return

    text, kb = await build_rss_home()
    await message.reply(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("rss_chat_page_"))
async def rss_chat_page_cb(callback: CallbackQuery):
    page = int(callback.data.split("_")[3])
    chats = _sort_chats(await db.get_all_bot_chats())
    kb = get_pagination_keyboard(
        chats, page, 10, "rss_chat_",
        button_formatter=lambda i: InlineKeyboardButton(
            text=("📢 " if i.get("type") == "channel" else "👥 ") + i["title"],
            callback_data=f"rss_chat_{i['chat_id']}"
        )
    )
    kb.inline_keyboard.append([InlineKeyboardButton(text="➕ Add New Chat", callback_data="rss_add_new_chat")])
    await callback.message.edit_text("📡 <b>RSS Dashboard</b>\n\nSelect a chat to manage:", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("rss_chat_"))
async def rss_chat_selected_cb(callback: CallbackQuery):
    if callback.data.startswith("rss_chat_page_"): return
    chat_id = int(callback.data.split("_")[2])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 RSS Feeds", callback_data=f"rss_feeds_list_{chat_id}")],
        [InlineKeyboardButton(text="⏱ Default Interval", callback_data=f"rss_interval_set_{chat_id}"),
         InlineKeyboardButton(text="🔗 Pipeline", callback_data=f"rss_pipeline_set_{chat_id}")],
        [InlineKeyboardButton(text="🗑️ Unlink Chat", callback_data=f"rss_unlink_chat_{chat_id}"),
         InlineKeyboardButton(text="◁ Back", callback_data="rss_chat_page_0_None")]
    ])

    chat_obj = await db.db.bot_chats.find_one({"chat_id": chat_id})
    if chat_obj:
        icon = "📢" if chat_obj.get("type") == "channel" else "👥"
        chat_info = f"{icon} {chat_obj['title']} (<code>{chat_id}</code>)"
    else:
        chat_info = str(chat_id)
    text = f"⚙️ <b>Chat Settings Dashboard</b>\n\n<b>Chat:</b> {chat_info}\n\nChoose an option:"
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("rss_feeds_list_"))
async def rss_feeds_list_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[3])
    feeds = await db.get_rss_feeds_by_chat(chat_id)

    # We map them to dicts with index to use in callback
    feed_items = [{"title": f.get('custom_title') or f.get('feed_title') or f['feed_url'], "fid": str(f['_id'])} for f in feeds]

    kb = get_pagination_keyboard(feed_items, 0, 10, "rss_feed_", chat_id, button_formatter=lambda i: InlineKeyboardButton(text=i["title"], callback_data=f"rss_feed_{i['fid']}_{chat_id}_0"))

    # Add 'Add Feed' and 'Back' buttons on same row
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="➕ Add Feed", callback_data=f"rss_add_feed_{chat_id}"),
        InlineKeyboardButton(text="◁ Back", callback_data=f"rss_chat_{chat_id}")
    ])

    await callback.message.edit_text("📡 <b>RSS Feeds</b>\n\nSelect a feed to manage:", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("rss_feed_page_"))
async def rss_feed_page_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    page = int(parts[3])
    chat_id = int(parts[4])
    feeds = await db.get_rss_feeds_by_chat(chat_id)
    feed_items = [{"title": f.get('custom_title') or f.get('feed_title') or f['feed_url'], "fid": str(f['_id'])} for f in feeds]

    kb = get_pagination_keyboard(feed_items, page, 10, "rss_feed_", chat_id, button_formatter=lambda i: InlineKeyboardButton(text=i["title"], callback_data=f"rss_feed_{i['fid']}_{chat_id}_{page}"))
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="➕ Add Feed", callback_data=f"rss_add_feed_{chat_id}"),
        InlineKeyboardButton(text="◁ Back", callback_data=f"rss_chat_{chat_id}")
    ])
    await callback.message.edit_text("📡 <b>RSS Feeds</b>\n\nSelect a feed to manage:", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

from bson.objectid import ObjectId
@router.callback_query(F.data.startswith("rss_feed_"))
async def rss_feed_selected_cb(callback: CallbackQuery):
    if callback.data.startswith("rss_feed_page_"): return
    parts = callback.data.split("_")
    fid = parts[2]
    chat_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0

    feed = await db.db.rss_feeds.find_one({"_id": ObjectId(fid), "chat_id": chat_id})
    if not feed:
        await callback.answer("Feed not found.", show_alert=True)
        return

    status_text = feed.get('status', 'Activated')
    interval_val = feed.get('time_interval', 0)
    interval_str = f"{interval_val}s" if interval_val > 0 else "Default"
    notification = feed.get('notification', 'Normal')
    watermark = "On" if feed.get('watermark_enabled', True) else "Off"
    media_mode = feed.get('media_mode', 'Enable')

    post_title = "On" if feed.get('post_title_enabled', True) else "Off"
    length_val = feed.get('length_limit', 0)
    length_str = "Unlimited" if length_val == 0 else str(length_val)

    source_map = {
        "feed_title_and_link": "Feed title and link",
        "feed_title_and_post_title_link": "Feed title and link displayed as post title",
        "feed_title_no_link": "Feed title and post title, no link",
        "post_title_link": "No feed title, link displayed as post title",
        "hyperlink_at_end": "No feed title, hyperlink at the end",
        "bare_url_at_end": "No feed title, bare URL at the end",
        "disable": "Completely disable"
    }
    raw_source = feed.get('source_format', 'feed_title_and_link')
    source_str = source_map.get(raw_source, raw_source)

    link_preview = "On" if feed.get('link_preview', True) else "Off"
    author = "On" if feed.get('author_enabled', True) else "Off"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Status: {status_text}", callback_data=f"rss_toggle_status_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Interval: {interval_str}", callback_data=f"rss_set_interval_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text=f"Notification: {notification}", callback_data=f"rss_toggle_notif_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Media: {media_mode}", callback_data=f"rss_toggle_media_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text=f"Watermark: {watermark}", callback_data=f"rss_toggle_wm_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Post Title: {post_title}", callback_data=f"rss_toggle_title_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text=f"Length: {length_str}", callback_data=f"rss_set_length_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Source: {source_str}", callback_data=f"rss_toggle_src_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Link Preview: {link_preview}", callback_data=f"rss_toggle_preview_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text=f"Author: {author}", callback_data=f"rss_toggle_author_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="Custom Title", callback_data=f"rss_set_ctitle_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text="Custom Hashtags", callback_data=f"rss_set_chashtag_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="📤 Send Latest Post", callback_data=f"rss_send_latest_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="🗑 Unsubscribe", callback_data=f"rss_del_feed_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text="◁ Back", callback_data=f"rss_feed_page_{page}_{chat_id}")]
    ])

    chat_obj = await db.db.bot_chats.find_one({"chat_id": chat_id})
    if chat_obj:
        icon = "📢" if chat_obj.get("type") == "channel" else "👥"
        chat_info = f"{icon} {chat_obj['title']} (<code>{chat_id}</code>)"
    else:
        chat_info = str(chat_id)
    feed_title = feed.get('feed_title', 'Unknown Title')

    text = (f"⚙️ <b>Feed Control Panel</b>\n\n"
            f"<b>Chat:</b>\n{chat_info}\n\n"
            f"<b>Subscription Info:</b>\n"
            f"<b>Feed Title:</b> {feed_title}\n"
            f"<b>Feed URL:</b> {feed['feed_url']}")

    from aiogram.exceptions import TelegramBadRequest
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

    try:
        await callback.answer()
    except Exception:
        pass

async def _toggle_setting(callback: CallbackQuery, key: str, values: list):
    parts = callback.data.split("_")
    fid = parts[3]
    chat_id = int(parts[4])
    page = parts[5] if len(parts) > 5 else "0"

    feed = await db.db.rss_feeds.find_one({"_id": ObjectId(fid), "chat_id": chat_id})
    if not feed: return

    current = feed.get(key, values[0])
    try:
        next_val = values[(values.index(current) + 1) % len(values)]
    except ValueError:
        next_val = values[0]

    await db.update_feed_settings(chat_id, feed['feed_url'], {key: next_val})

    cb_clone = callback.model_copy(update={"data": f"rss_feed_{fid}_{chat_id}_{page}"})
    await rss_feed_selected_cb(cb_clone)
    try:
        await callback.answer()
    except Exception:
        pass

@router.callback_query(F.data.startswith("rss_toggle_status_"))
async def toggle_status(cb: CallbackQuery): await _toggle_setting(cb, "status", ["Activated", "Deactivated"])
@router.callback_query(F.data.startswith("rss_toggle_media_"))
async def toggle_media(cb: CallbackQuery): await _toggle_setting(cb, "media_mode", ["Enable", "Disable", "Only media"])
@router.callback_query(F.data.startswith("rss_toggle_preview_"))
async def toggle_preview(cb: CallbackQuery): await _toggle_setting(cb, "link_preview", [True, False])
@router.callback_query(F.data.startswith("rss_toggle_title_"))
async def toggle_title(cb: CallbackQuery): await _toggle_setting(cb, "post_title_enabled", [True, False])
@router.callback_query(F.data.startswith("rss_toggle_notif_"))
async def toggle_notif(cb: CallbackQuery): await _toggle_setting(cb, "notification", ["Normal", "Muted"])
@router.callback_query(F.data.startswith("rss_toggle_author_"))
async def toggle_author(cb: CallbackQuery): await _toggle_setting(cb, "author_enabled", [True, False])
@router.callback_query(F.data.startswith("rss_toggle_wm_"))
async def toggle_wm(cb: CallbackQuery): await _toggle_setting(cb, "watermark_enabled", [True, False])

@router.callback_query(F.data.startswith("rss_del_feed_"))
async def del_feed_confirm(cb: CallbackQuery):
    parts = cb.data.split("_")
    fid = parts[3]
    chat_id = int(parts[4])
    page = parts[5] if len(parts) > 5 else "0"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Unsubscribe", callback_data=f"rss_del_conf_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="❌ No, Cancel", callback_data=f"rss_feed_{fid}_{chat_id}_{page}")]
    ])

    await cb.message.edit_text("⚠️ <b>Are you sure you want to completely remove this RSS feed and all its settings?</b>", reply_markup=keyboard, parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("rss_del_conf_"))
async def del_feed_confirmed(cb: CallbackQuery):
    parts = cb.data.split("_")
    fid = parts[3]
    chat_id = int(parts[4])
    page = parts[5] if len(parts) > 5 else "0"

    feed = await db.db.rss_feeds.find_one({"_id": ObjectId(fid), "chat_id": chat_id})
    if feed:
        await db.remove_rss_feed(chat_id, feed['feed_url'])
        await cb.answer("Feed Unsubscribed successfully.", show_alert=True)
    cb_clone = cb.model_copy(update={"data": f"rss_feed_page_{page}_{chat_id}"})
    await rss_feed_page_cb(cb_clone)

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

class RSSStates(StatesGroup):
    waiting_for_feed_url = State()
    waiting_for_interval = State()
    waiting_for_custom_title = State()
    waiting_for_hashtags = State()
    waiting_for_wm_logo = State()
    waiting_for_wm_footer = State()
    waiting_for_wm_name = State()
    waiting_for_length = State()

@router.callback_query(F.data.startswith("rss_add_feed_"))
async def add_feed_prompt(cb: CallbackQuery, state: FSMContext):
    chat_id = int(cb.data.split("_")[3])
    await state.update_data(chat_id=chat_id)
    await state.set_state(RSSStates.waiting_for_feed_url)
    await cb.message.edit_text("Send me the RSS Feed URL you want to add.", reply_markup=get_cancel_kb("rss_cancel_fsm"))
    await cb.answer()

@router.callback_query(F.data == "rss_cancel_fsm")
async def rss_cancel_fsm_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("❌ Operation cancelled.")
    finally:
        await callback.answer()

@router.message(RSSStates.waiting_for_feed_url)
async def process_add_feed(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.reply("Cancelled.")
        return
    from utilitybot.modules.rss.handlers import is_valid_feed_url
    if not await is_valid_feed_url(message.text):
        await message.reply("Invalid RSS Feed URL. Make sure it starts with http/https and is a valid feed.\nTry again:", reply_markup=get_cancel_kb("rss_cancel_fsm"))
        return

    data = await state.get_data()
    chat_id = data['chat_id']
    await db.add_rss_feed(chat_id, message.text)
    from utilitybot.modules.rss.handlers import _mark_all_existing_items
    await _mark_all_existing_items(chat_id, message.text)
    await state.clear()

    # Retrieve the newly added feed to get its ID
    new_feed = await db.db.rss_feeds.find_one({"chat_id": chat_id, "feed_url": message.text})
    if new_feed:
        fid = str(new_feed['_id'])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Feed Settings", callback_data=f"rss_feed_{fid}_{chat_id}_0")],
            [InlineKeyboardButton(text="📋 Back to Feeds", callback_data=f"rss_feeds_list_{chat_id}")]
        ])
        await message.reply(f"✅ Feed {message.text} added successfully!", reply_markup=keyboard)
    else:
        await message.reply(f"✅ Feed {message.text} added successfully! Go back to /rss to manage it.")

@router.callback_query(F.data.startswith("rss_set_interval_"))
async def set_interval_prompt(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split("_")
    await state.update_data(fid=parts[3], chat_id=int(parts[4]))
    page = int(parts[5]) if len(parts) > 5 else 0
    await state.update_data(page=page)
    await state.set_state(RSSStates.waiting_for_interval)

    intervals_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Default", callback_data="rss_interval_val_0")],
        [InlineKeyboardButton(text="2m", callback_data="rss_interval_val_120"),
         InlineKeyboardButton(text="5m", callback_data="rss_interval_val_300"),
         InlineKeyboardButton(text="10m", callback_data="rss_interval_val_600")],
        [InlineKeyboardButton(text="30m", callback_data="rss_interval_val_1800"),
         InlineKeyboardButton(text="1h", callback_data="rss_interval_val_3600"),
         InlineKeyboardButton(text="3h", callback_data="rss_interval_val_10800")]
    ])
    await cb.message.edit_text("Select an interval for this feed:", reply_markup=intervals_kb)
    await cb.answer()

@router.callback_query(F.data.startswith("rss_interval_val_"))
async def set_interval_val(cb: CallbackQuery, state: FSMContext):
    val = int(cb.data.split("_")[3])
    data = await state.get_data()
    if 'fid' in data: # Per feed interval
        feed = await db.db.rss_feeds.find_one({'_id': ObjectId(data['fid']), 'chat_id': data['chat_id']})
        await db.update_feed_settings(data['chat_id'], feed['feed_url'], {"time_interval": val})
        await state.clear()
        cb_clone = cb.model_copy(update={"data": f"rss_feed_{data['fid']}_{data['chat_id']}_{data.get('page', 0)}"})
        await rss_feed_selected_cb(cb_clone)
    else: # Default interval
        await db.update_rss_settings(data['chat_id'], {"default_time_interval": val})
        await state.clear()
        cb_clone = cb.model_copy(update={"data": f"rss_chat_{data['chat_id']}"})
        await rss_chat_selected_cb(cb_clone)
    try:
        await cb.answer()
    except Exception:
        pass

@router.callback_query(F.data.startswith("rss_interval_set_"))
async def set_default_interval_prompt(cb: CallbackQuery, state: FSMContext):
    chat_id = int(cb.data.split("_")[3])
    # Bug fix: clear any leftover per-feed FSM state (e.g. a stale 'fid' key
    # from a previously opened per-feed interval prompt). Without this, the
    # shared set_interval_val handler would find 'fid' in state data and
    # mistakenly update a feed's time_interval instead of the chat default.
    await state.clear()
    await state.update_data(chat_id=chat_id)
    # Re-use interval val panel without 'fid' in state
    intervals_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5m (Default)", callback_data="rss_interval_val_300"),
         InlineKeyboardButton(text="10m", callback_data="rss_interval_val_600")],
        [InlineKeyboardButton(text="30m", callback_data="rss_interval_val_1800"),
         InlineKeyboardButton(text="1h", callback_data="rss_interval_val_3600"),
         InlineKeyboardButton(text="3h", callback_data="rss_interval_val_10800")]
    ])
    await cb.message.edit_text("Select a default interval for this chat:", reply_markup=intervals_kb)
    await cb.answer()

async def _show_feed_panel(message: Message, fid: str, chat_id: int, page: int = 0):
    """Re-render the feed control panel as a new message after an FSM text input.

    Used instead of constructing a fake CallbackQuery (which raises a
    Pydantic ValidationError because required internal fields are absent).
    """
    feed = await db.db.rss_feeds.find_one({"_id": ObjectId(fid), "chat_id": chat_id})
    if not feed:
        await message.answer("Feed not found.")
        return

    status_text = feed.get('status', 'Activated')
    interval_val = feed.get('time_interval', 0)
    interval_str = f"{interval_val}s" if interval_val > 0 else "Default"
    notification = feed.get('notification', 'Normal')
    watermark = "On" if feed.get('watermark_enabled', True) else "Off"
    media_mode = feed.get('media_mode', 'Enable')
    post_title = "On" if feed.get('post_title_enabled', True) else "Off"
    length_val = feed.get('length_limit', 0)
    length_str = "Unlimited" if length_val == 0 else str(length_val)
    source_map = {
        "feed_title_and_link": "Feed title and link",
        "feed_title_and_post_title_link": "Feed title and link displayed as post title",
        "feed_title_no_link": "Feed title and post title, no link",
        "post_title_link": "No feed title, link displayed as post title",
        "hyperlink_at_end": "No feed title, hyperlink at the end",
        "bare_url_at_end": "No feed title, bare URL at the end",
        "disable": "Completely disable"
    }
    raw_source = feed.get('source_format', 'feed_title_and_link')
    source_str = source_map.get(raw_source, raw_source)
    link_preview = "On" if feed.get('link_preview', True) else "Off"
    author = "On" if feed.get('author_enabled', True) else "Off"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Status: {status_text}", callback_data=f"rss_toggle_status_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Time Interval: {interval_str}", callback_data=f"rss_set_interval_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Notification: {notification}", callback_data=f"rss_toggle_notif_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Watermark: {watermark}", callback_data=f"rss_toggle_wm_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Media: {media_mode}", callback_data=f"rss_toggle_media_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Post Title: {post_title}", callback_data=f"rss_toggle_title_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text=f"Length: {length_str}", callback_data=f"rss_set_length_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Source: {source_str}", callback_data=f"rss_toggle_src_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text=f"Link Preview: {link_preview}", callback_data=f"rss_toggle_preview_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text=f"Author: {author}", callback_data=f"rss_toggle_author_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="Custom Title", callback_data=f"rss_set_ctitle_{fid}_{chat_id}_{page}"),
         InlineKeyboardButton(text="Custom Hashtags", callback_data=f"rss_set_chashtag_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="📤 Send Latest Post", callback_data=f"rss_send_latest_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="🗑 Unsubscribe", callback_data=f"rss_del_feed_{fid}_{chat_id}_{page}")],
        [InlineKeyboardButton(text="◁ Back", callback_data=f"rss_feed_page_{page}_{chat_id}")]
    ])

    chat_obj = await db.db.bot_chats.find_one({"chat_id": chat_id})
    if chat_obj:
        icon = "📢" if chat_obj.get("type") == "channel" else "👥"
        chat_info = f"{icon} {chat_obj['title']} (<code>{chat_id}</code>)"
    else:
        chat_info = str(chat_id)
    feed_title = feed.get('feed_title', 'Unknown Title')
    text = (f"⚙️ <b>Feed Control Panel</b>\n\n"
            f"<b>Chat:</b>\n{chat_info}\n\n"
            f"<b>Subscription Info:</b>\n"
            f"<b>Feed Title:</b> {feed_title}\n"
            f"<b>Feed URL:</b> {feed['feed_url']}")

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.startswith("rss_set_ctitle_"))
async def set_ctitle_prompt(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split("_")
    await state.update_data(fid=parts[3], chat_id=int(parts[4]))
    page = int(parts[5]) if len(parts) > 5 else 0
    await state.update_data(page=page)
    await state.set_state(RSSStates.waiting_for_custom_title)
    feed = await db.db.rss_feeds.find_one({'_id': ObjectId(parts[3]), 'chat_id': int(parts[4])})
    current = feed.get('custom_title', 'None')
    await cb.message.edit_text(f"Send the new custom title for this feed (or type /clear to remove it):\n\n<b>Current:</b> {current}", parse_mode="HTML", reply_markup=get_cancel_kb("rss_cancel_fsm"))
    await cb.answer()

@router.message(RSSStates.waiting_for_custom_title)
async def process_ctitle(message: Message, state: FSMContext):
    data = await state.get_data()
    feed = await db.db.rss_feeds.find_one({'_id': ObjectId(data['fid']), 'chat_id': data['chat_id']})
    val = None if message.text == "/clear" else message.text
    await db.update_feed_settings(data['chat_id'], feed['feed_url'], {"custom_title": val})
    await state.clear()
    await message.answer("Custom title updated.")
    await _show_feed_panel(message, data['fid'], data['chat_id'], data.get('page', 0))

@router.callback_query(F.data.startswith("rss_set_chashtag_"))
async def set_chashtag_prompt(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split("_")
    await state.update_data(fid=parts[3], chat_id=int(parts[4]))
    page = int(parts[5]) if len(parts) > 5 else 0
    await state.update_data(page=page)
    await state.set_state(RSSStates.waiting_for_hashtags)
    feed = await db.db.rss_feeds.find_one({'_id': ObjectId(parts[3]), 'chat_id': int(parts[4])})
    current = feed.get('custom_hashtags', 'None')
    await cb.message.edit_text(f"Send custom hashtags to append (or type /clear):\n\n<b>Current:</b> {current}", parse_mode="HTML", reply_markup=get_cancel_kb("rss_cancel_fsm"))
    await cb.answer()

@router.message(RSSStates.waiting_for_hashtags)
async def process_chashtag(message: Message, state: FSMContext):
    data = await state.get_data()
    feed = await db.db.rss_feeds.find_one({'_id': ObjectId(data['fid']), 'chat_id': data['chat_id']})
    val = None if message.text == "/clear" else message.text
    await db.update_feed_settings(data['chat_id'], feed['feed_url'], {"custom_hashtags": val})
    await state.clear()
    await message.answer("Hashtags updated.")
    await _show_feed_panel(message, data['fid'], data['chat_id'], data.get('page', 0))








@router.callback_query(F.data.startswith("rss_toggle_src_"))
async def toggle_src(cb: CallbackQuery): await _toggle_setting(cb, "source_format", [
    "feed_title_and_link", "feed_title_and_post_title_link", "feed_title_no_link",
    "post_title_link", "hyperlink_at_end", "bare_url_at_end", "disable"
])

@router.callback_query(F.data.startswith("rss_set_length_"))
async def set_length_prompt(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split("_")
    await state.update_data(fid=parts[3], chat_id=int(parts[4]))
    page = int(parts[5]) if len(parts) > 5 else 0
    await state.update_data(page=page)
    await state.set_state(RSSStates.waiting_for_length)
    feed = await db.db.rss_feeds.find_one({'_id': ObjectId(parts[3]), 'chat_id': int(parts[4])})
    current = feed.get('length_limit', 0)
    current_str = "Unlimited" if current == 0 else str(current)
    await cb.message.edit_text(f"Send the character limit (0-4096). 0 means unlimited.\n\n<b>Current:</b> {current_str}", parse_mode="HTML", reply_markup=get_cancel_kb("rss_cancel_fsm"))
    await cb.answer()

@router.message(RSSStates.waiting_for_length)
async def process_length(message: Message, state: FSMContext):
    data = await state.get_data()
    feed = await db.db.rss_feeds.find_one({'_id': ObjectId(data['fid']), 'chat_id': data['chat_id']})

    try:
        val = int(message.text)
        if val < 0 or val > 4096:
            raise ValueError()
    except ValueError:
        await message.reply("Invalid limit. Must be integer between 0 and 4096.\nTry again:", reply_markup=get_cancel_kb("rss_cancel_fsm"))
        return

    await db.update_feed_settings(data['chat_id'], feed['feed_url'], {"length_limit": val})
    await state.clear()
    await message.answer("Length limit updated.")
    await _show_feed_panel(message, data['fid'], data['chat_id'], data.get('page', 0))

@router.callback_query(F.data.startswith("rss_send_latest_"))
async def send_latest_prompt(cb: CallbackQuery):
    parts = cb.data.split("_")
    fid = parts[3]
    chat_id = int(parts[4])
    page = parts[5] if len(parts) > 5 else "0"

    feed = await db.db.rss_feeds.find_one({"_id": ObjectId(fid), "chat_id": chat_id})
    if not feed:
        await cb.answer("Feed not found.", show_alert=True)
        return

    # Answer immediately — Telegram requires a response within 10s or it shows an error
    await cb.answer("📤 Sending latest post in background…", show_alert=False)

    from utilitybot.modules.rss.service import send_latest_item
    import asyncio
    bot = cb.bot
    owner_chat = cb.from_user.id

    async def _run_and_notify():
        try:
            await send_latest_item(bot, feed)
            feed_name = feed.get('custom_title') or feed.get('feed_title') or feed['feed_url']
            await bot.send_message(owner_chat, f"✅ Latest post sent for <b>{feed_name}</b>.", parse_mode="HTML")
        except Exception as e:
            log.error(f"Send latest error: {e}")
            await bot.send_message(owner_chat, f"❌ Failed to send latest post: <code>{e}</code>", parse_mode="HTML")

    asyncio.create_task(_run_and_notify())


@router.callback_query(F.data.startswith("rss_unlink_chat_"))
async def unlink_chat_confirm_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[3])
    chat_obj = await db.db.bot_chats.find_one({"chat_id": chat_id})
    title = chat_obj["title"] if chat_obj else str(chat_id)
    icon = "📢" if (chat_obj or {}).get("type") == "channel" else "👥"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Unlink", callback_data=f"rss_unlink_conf_{chat_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"rss_chat_{chat_id}")]
    ])
    await callback.message.edit_text(
        f"⚠️ <b>Unlink Chat?</b>\n\n"
        f"{icon} <b>{title}</b> will be removed from the RSS dashboard.\n\n"
        f"All RSS feeds for this chat will also be deleted. This cannot be undone.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rss_unlink_conf_"))
async def unlink_chat_execute_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[3])
    chat_obj = await db.db.bot_chats.find_one({"chat_id": chat_id})
    title = chat_obj["title"] if chat_obj else str(chat_id)
    icon = "📢" if (chat_obj or {}).get("type") == "channel" else "👥"

    try:
        # Remove all RSS feeds for this chat
        await db.remove_all_rss_feeds_by_chat(chat_id)
        # Soft-unlink: keep the record so it reappears in Add New Chat
        await db.unlink_bot_chat_from_rss(chat_id)
        log.info(f"Chat {chat_id} unlinked from RSS dashboard by {callback.from_user.id}")
    except Exception as e:
        log.error(f"Failed to unlink chat {chat_id}: {e}")
        await callback.answer(f"❌ Error: {e}", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ <b>Chat Unlinked</b>\n\n"
        f"{icon} <b>{title}</b> has been removed from the RSS dashboard.\n"
        f"All associated feeds have been deleted.\n\n"
        f"You can re-add it anytime using ➕ Add New Chat.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◁ Back to Dashboard", callback_data="rss_back_home")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rss_pipeline_set_"))
async def rss_pipeline_set_cb(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[3])
    settings = await db.get_settings(chat_id) or {}

    bl = "🟢 On" if settings.get('rss_use_blocklist', True) else "🔴 Off"
    marg = "🟢 On" if settings.get('rss_use_marginals', True) else "🔴 Off"
    wm = "🟢 On" if settings.get('rss_use_watermark', True) else "🔴 Off"
    repl = "🟢 On" if settings.get('rss_use_replacements', True) else "🔴 Off"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Blocklist: {bl}", callback_data=f"rss_pipe_bl_{chat_id}"),
         InlineKeyboardButton(text=f"Marginals: {marg}", callback_data=f"rss_pipe_marg_{chat_id}")],
        [InlineKeyboardButton(text=f"Watermark: {wm}", callback_data=f"rss_pipe_wm_{chat_id}"),
         InlineKeyboardButton(text=f"Replacements: {repl}", callback_data=f"rss_pipe_repl_{chat_id}")],
        [InlineKeyboardButton(text="◁ Back", callback_data=f"rss_chat_{chat_id}")]
    ])

    text = (f"🔗 <b>Pipeline Settings</b>\n\n"
            f"Toggle the processing steps applied to every RSS post in this chat.")

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

async def _toggle_pipeline(callback: CallbackQuery, key: str, default: bool):
    chat_id = int(callback.data.split("_")[3])
    settings = await db.get_settings(chat_id) or {}
    current = settings.get(key, default)
    await db.update_settings(chat_id, {key: not current})

    cb_clone = callback.model_copy(update={"data": f"rss_pipeline_set_{chat_id}"})
    await rss_pipeline_set_cb(cb_clone)
    try:
        await callback.answer()
    except Exception:
        pass

@router.callback_query(F.data.startswith("rss_pipe_bl_"))
async def pipe_bl(cb: CallbackQuery): await _toggle_pipeline(cb, "rss_use_blocklist", True)
@router.callback_query(F.data.startswith("rss_pipe_marg_"))
async def pipe_marg(cb: CallbackQuery): await _toggle_pipeline(cb, "rss_use_marginals", True)
@router.callback_query(F.data.startswith("rss_pipe_wm_"))
async def pipe_wm(cb: CallbackQuery): await _toggle_pipeline(cb, "rss_use_watermark", True)
@router.callback_query(F.data.startswith("rss_pipe_repl_"))
async def pipe_repl(cb: CallbackQuery): await _toggle_pipeline(cb, "rss_use_replacements", True)

# =============================================================================
# Add New Chat flow — lets owner add any chat the bot is already in
# =============================================================================

class RSSAddChatStates(StatesGroup):
    waiting_for_chat_id = State()

@router.callback_query(F.data == "rss_add_new_chat")
async def rss_add_new_chat_cb(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return

    # Show chats known to the bot but not currently linked to the RSS dashboard
    unlinked_chats = await db.get_unlinked_bot_chats()

    keyboard = []
    for chat in unlinked_chats[:20]:
        icon = "📢" if chat.get("type") == "channel" else "👥"
        keyboard.append([InlineKeyboardButton(
            text=f"{icon} {chat['title']}",
            callback_data=f"rss_quickadd_{chat['chat_id']}"
        )])

    if not unlinked_chats:
        keyboard.insert(0, [InlineKeyboardButton(
            text="ℹ️ All known chats are already linked", callback_data="rss_noop"
        )])

    keyboard.append([InlineKeyboardButton(text="✏️ Enter Chat ID manually", callback_data="rss_add_manual_id")])
    keyboard.append([InlineKeyboardButton(text="◁ Back", callback_data="rss_back_home")])

    await callback.message.edit_text(
        "➕ <b>Add New Chat to RSS</b>\n\n"
        "Select a previously unlinked chat, or enter a Chat ID manually:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "rss_noop")
async def rss_noop_cb(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "rss_back_home")
async def rss_back_home_cb(callback: CallbackQuery, bot: Bot):
    chats = _sort_chats(await db.get_all_bot_chats())
    kb = get_pagination_keyboard(
        chats, 0, 10, "rss_chat_",
        button_formatter=lambda i: InlineKeyboardButton(
            text=("📢 " if i.get("type") == "channel" else "👥 ") + i["title"],
            callback_data=f"rss_chat_{i['chat_id']}"
        )
    )
    kb.inline_keyboard.append([InlineKeyboardButton(text="➕ Add New Chat", callback_data="rss_add_new_chat")])
    await callback.message.edit_text("📡 <b>RSS Dashboard</b>\n\nSelect a chat to manage:", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("rss_quickadd_"))
async def rss_quickadd_cb(callback: CallbackQuery, bot: Bot):
    """User picked an unlinked chat — re-link it and navigate to its dashboard."""
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return
    chat_id = int(callback.data.split("_")[2])
    try:
        chat = await bot.get_chat(chat_id)
        await db.add_bot_chat(chat_id, chat.title, chat.username or "", chat.invite_link or "", chat.type)
    except Exception as e:
        await callback.answer(f"❌ {e}", show_alert=True)
        return

    # Navigate directly to that chat's dashboard
    cb_clone = callback.model_copy(update={"data": f"rss_chat_{chat_id}"})
    await rss_chat_selected_cb(cb_clone)


@router.callback_query(F.data == "rss_add_manual_id")
async def rss_add_manual_id_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.OWNER_ID:
        await callback.answer()
        return
    await state.set_state(RSSAddChatStates.waiting_for_chat_id)
    await callback.message.edit_text(
        "✏️ <b>Enter Chat ID</b>\n\n"
        "Send the numeric Chat ID (e.g. <code>-1001234567890</code>).",
        parse_mode="HTML",
        reply_markup=get_cancel_kb("rss_cancel_fsm")
    )
    await callback.answer()


@router.message(RSSAddChatStates.waiting_for_chat_id, Command("cancel"))
async def rss_add_chat_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.reply("Cancelled. Use /rss to return to the dashboard.")


@router.message(RSSAddChatStates.waiting_for_chat_id)
async def rss_add_chat_manual_input(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != config.OWNER_ID:
        return
    raw = message.text.strip()
    try:
        chat_id = int(raw)
    except ValueError:
        await message.reply("❌ That doesn't look like a valid Chat ID. Send a number like <code>-1001234567890</code>.\nTry again:", parse_mode="HTML", reply_markup=get_cancel_kb("rss_cancel_fsm"))
        return

    try:
        chat = await bot.get_chat(chat_id)
    except Exception as e:
        await message.reply(f"❌ Could not access that chat. Make sure the bot is a member.\n<code>{e}</code>\nTry again:", parse_mode="HTML", reply_markup=get_cancel_kb("rss_cancel_fsm"))
        return

    await db.add_bot_chat(chat_id, chat.title, chat.username or "", chat.invite_link or "", chat.type)
    await state.clear()
    icon = "📢" if chat.type == "channel" else "👥"
    await message.reply(
        f"✅ {icon} <b>{chat.title}</b> added to RSS Dashboard.\n\nUse /rss to manage it.",
        parse_mode="HTML"
    )
