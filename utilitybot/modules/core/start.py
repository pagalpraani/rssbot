# =============================================================================
# Module: Core
# Path: utilitybot/modules/core/start.py
# Description: /start command and welcome menu for the RSS Feed Reader bot.
# Scope: private
# =============================================================================

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from ...utils.logger import get_logger
import html

log = get_logger(__name__)

router = Router()


def get_welcome_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Open RSS Dashboard", callback_data="start_open_rss")],
        [InlineKeyboardButton(text="❓ Help", callback_data="start_open_help")],
        [InlineKeyboardButton(text="ℹ️ About", callback_data="about")],
    ])


WELCOME_TEXT = (
    "Hello {name} 👋\n\n"
    "I'm your <b>RSS Feed Reader Bot</b> 📡\n"
    "I watch your feeds and post new items straight to your channels or groups, "
    "with optional watermarking, blocklist filtering, headers/footers, and text replacements.\n\n"
    "Add me as an admin to a channel or group, then run <code>/rss</code> there to link a feed.\n\n"
    "Use <code>/help</code> for the full command list."
)


@router.message(Command("start", prefix="!/"), F.chat.type == "private")
async def start_command(message: Message):
    name = html.escape(message.from_user.first_name)
    await message.reply(
        WELCOME_TEXT.format(name=name),
        reply_markup=get_welcome_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "start_menu")
async def back_to_start(callback: CallbackQuery):
    name = html.escape(callback.from_user.first_name)
    try:
        await callback.message.edit_text(
            WELCOME_TEXT.format(name=name),
            reply_markup=get_welcome_kb(),
            parse_mode="HTML",
        )
    finally:
        await callback.answer()


@router.callback_query(F.data == "start_open_rss")
async def start_open_rss(callback: CallbackQuery):
    try:
        await callback.answer(
            "Run /rss inside the channel or group you want to manage.",
            show_alert=True,
        )
    finally:
        pass


@router.callback_query(F.data == "start_open_help")
async def start_open_help(callback: CallbackQuery):
    try:
        await callback.answer("Use /help to see the full command list in PM.", show_alert=True)
    finally:
        pass


@router.callback_query(F.data == "about")
async def about_callback(callback: CallbackQuery):
    text = (
        "<b>ℹ️ About this bot</b>\n\n"
        "📡 Reads RSS/Atom feeds and posts new items to your channels or groups.\n"
        "💧 Optional watermarking for images and PDFs.\n"
        "🚫 Blocklist filtering for unwanted keywords.\n"
        "📄 Marginals — automatic headers and footers.\n"
        "🔀 Text replacements on posted content.\n\n"
        "Run <code>/rss</code> inside a linked chat to get started."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◁ Back", callback_data="start_menu")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    finally:
        await callback.answer()
