# =============================================================================
# Module: Core
# Path: utilitybot/modules/core/help.py
# Description: Provides core logic and data structures for help.py.
# Scope: channel | group
# =============================================================================

from aiogram import Router, F, Bot, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from ...utils.permissions import owner_only
from ...utils.help_registry import HelpRegistry

router = Router()

# --- Help Content Constants ---

HELP_TEXT_MARKDOWN = (
    "<b>Markdown Formatting Guide</b>\n\n"
    "You can format your messages using standard Markdown syntax to make them look professional.\n\n"
    "<b>Basic Styles:</b>\n"
    "• <b>Bold</b>: <code>*text*</code> → <b>text</b>\n"
    "• <i>Italic</i>: <code>_text_</code> → <i>text</i>\n"
    "• <u>Underline</u>: <code>__text__</code> → <u>text</u>\n"
    "• <s>Strikethrough</s>: <code>~text~</code> → <s>text</s>\n"
    "• <tg-spoiler>Spoiler</tg-spoiler>: <code>||text||</code> → <tg-spoiler>text</tg-spoiler>\n\n"
    "<b>Special Syntax:</b>\n"
    "• <code>> quote</code> → Single line quote\n\n"
    "• <b>Multiline Quotes:</b>\n"
    "  <code>**&gt; Title\n&gt; Line 1\n&gt; Last Line</code>\n"
    "  <i>Creates a collapsible quote block.</i>\n\n"
    "<b>Code:</b>\n"
    "• Inline Code: <code>`text`</code> → <code>text</code>\n"
    "• Code Block: <code>```text```</code>\n"
    "• Code with Language:\n"
    "<code>```python\nprint('Hello')\n```</code>\n\n"
    "<b>Links & Buttons:</b>\n"
    "• Text Link: <code>[Google](google.com)</code>\n"
    "• Standard Button: <code>[Visit](buttonurl://example.com)</code>\n"
    "• Colored Buttons:\n"
    "  Blue: <code>[Join](buttonurl#primary://t.me/c)</code>\n"
    "  Green: <code>[Yes](buttonurl#success://example.com)</code>\n"
    "  Red: <code>[No](buttonurl#danger://example.com)</code>\n"
    "• Stacked Button: Append <code>:same</code> to add it to the previous row.\n"
    "  <i>Example:</i> <code>[Yes](buttonurl#success://link:same)</code>\n"
    "• Note Link: <code>[Read Rules](buttonurl://#rules)</code>\n"
)

HELP_TEXT_FILLINGS = (
    "<b>Fillings (Variables) Guide</b>\n\n"
    "Use these placeholders to insert dynamic context into your messages.\n\n"
    "<b>User Information:</b>\n"
    "• <code>{first}</code>: User's first name.\n"
    "• <code>{last}</code>: User's last name.\n"
    "• <code>{fullname}</code>: User's full name.\n"
    "• <code>{username}</code>: User's username (@handle).\n"
    "• <code>{mention}</code>: Clickable mention of the user.\n"
    "• <code>{id}</code>: User's Telegram ID.\n\n"
    "<b>Chat Information:</b>\n"
    "• <code>{chatname}</code>: Title of the chat.\n"
    "• <code>{count}</code>: Current member count.\n"
    "• <code>{date}</code>: Current date and time.\n"
    "• <code>{pinned}</code>: Link to the pinned message.\n"
    "• <code>{rules}</code>: Link to chat rules (if set).\n\n"
    "<b>Message Flags (Control Behavior):</b>\n"
    "Put these tags anywhere in your message to enable features:\n"
    "• <code>{preview}</code>: Enable link preview.\n"
    "• <code>{preview:top}</code>: Show link preview above text.\n"
    "• <code>{nonotif}</code>: Send silently (no notification).\n"
    "• <code>{protect}</code>: Protect content (no forwarding).\n"
    "• <code>{mediaspoiler}</code>: Mark media as spoiler.\n"
    "• <code>{pin}</code>: Pin the message after sending.\n"
    "• <code>{nomarginals}</code>: Skip adding marginals.\n"
    "• <code>{noheader}</code>, <code>{nofooter}</code>: Skip header/footer.\n"
    "• <code>{nogap}</code>: Remove gap between marginals.\n"
)

HELP_TEXT_RANDOM = (
    "<b>Random Content Guide</b>\n\n"
    "You can make the bot send different variations of a message randomly. This is useful for welcome messages or fun responses.\n\n"
    "<b>Syntax:</b>\n"
    "Use <code>%%%</code> to separate different content variations.\n\n"
    "<b>Example:</b>\n"
    "<code>Hello there! %%% Hi! %%% Welcome!</code>\n\n"
    "<b>How it works:</b>\n"
    "When the message is processed, the bot will randomly select <b>one</b> of the segments separated by <code>%%%</code> to send."
)


# Icons for module help view titles — keyed by help registry key
HELP_MODULE_ICONS = {
    "blocklist":    "🚫",
    "formatting":   "✏️",
    "logging":      "📋",
    "marginals":    "📐",
    "misc":         "🔧",
    "dev_misc":     "🔧",
    "replacements": "♻️",
    "rss":          "📡",
    "watermark":    "💧",
}

# Register Formatting Help (Intro)
HelpRegistry.register(
    "Formatting",
    "formatting",
    "<b>Formatting Guide</b>\n\n"
    "The bot supports advanced formatting options to create rich and dynamic messages.\n\n"
    "Select a topic below to learn more:",
    supported_chat_types=["group", "supergroup", "channel"]
)


def get_main_selection_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Group", callback_data="help_menu:group")
    builder.button(text="📢 Channel", callback_data="help_menu:channel")
    builder.adjust(2)
    # Developer on its own row (secondary)
    builder.row(types.InlineKeyboardButton(text="👨‍💻 Developer", callback_data="help_menu:dev"))
    return builder.as_markup()

def get_module_list_keyboard(chat_type: str):
    builder = InlineKeyboardBuilder()
    modules = HelpRegistry.get_all(chat_type=chat_type)

    for name, key in modules:
        builder.button(text=name, callback_data=f"help_view:{chat_type}:{key}")
    builder.adjust(2)

    builder.row(types.InlineKeyboardButton(text="◁ Back", callback_data="help_main"))
    return builder.as_markup()

def get_formatting_keyboard(chat_type_context: str = "group"):
    builder = InlineKeyboardBuilder()
    builder.button(text="Markdown", callback_data=f"help_fmt_markdown:{chat_type_context}")
    builder.button(text="Fillings", callback_data=f"help_fmt_fillings:{chat_type_context}")
    builder.button(text="Random Content", callback_data=f"help_fmt_random:{chat_type_context}")
    builder.adjust(2)
    builder.row(types.InlineKeyboardButton(text="◁ Back", callback_data=f"help_menu:{chat_type_context}"))
    return builder.as_markup()

@router.message(Command("help", prefix="!/"), F.chat.type.in_({"private", "group", "supergroup"}))
@owner_only
async def help_command(message: types.Message, bot: Bot):
    help_text = "<b>❓ Help Center</b>\n\nSelect a category to see the available commands."
    try:
        await bot.send_message(
            message.from_user.id,
            help_text,
            parse_mode="HTML",
            reply_markup=get_main_selection_keyboard()
        )
        if message.chat.type != "private":
            await message.reply("Help menu sent to your PM.", parse_mode="HTML")
    except Exception:
        await message.reply("Please start me in PM first to access the help menu.", parse_mode="HTML")

@router.callback_query(F.data == "help_main")
@owner_only
async def help_main_callback(query: types.CallbackQuery):
    help_text = "<b>❓ Help Center</b>\n\nSelect a category to see the available commands."
    try:
        await query.message.edit_text(help_text, parse_mode="HTML", reply_markup=get_main_selection_keyboard())
    finally:
        await query.answer()

@router.callback_query(F.data.startswith("help_menu:"))
@owner_only
async def help_menu_callback(query: types.CallbackQuery):
    chat_type_filter = query.data.split(":")[1]

    if chat_type_filter == "group":
        title = "Group"
    elif chat_type_filter == "channel":
        title = "Channel"
    else:
        title = "Developer"

    text = f"<b>{title} Modules</b>\nSelect a module:"

    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=get_module_list_keyboard(chat_type_filter))
    finally:
        await query.answer()

@router.callback_query(F.data.startswith("help_view:"))
@owner_only
async def help_view_callback(query: types.CallbackQuery):
    # help_view:chat_type:key
    _, chat_type_filter, key = query.data.split(":")

    # Special handling for Formatting to show submenu
    try:
        if key == "formatting":
            help_text = HelpRegistry.get_help_text(key)
            icon = HELP_MODULE_ICONS.get(key, "")
            if icon:
                help_text = help_text.replace("<b>", f"<b>{icon} ", 1)
            await query.message.edit_text(help_text, parse_mode="HTML", reply_markup=get_formatting_keyboard(chat_type_filter))
            return

        help_text = HelpRegistry.get_help_text(key)

        # Prepend module icon to the first bold title in the help text
        icon = HELP_MODULE_ICONS.get(key, "")
        if icon:
            help_text = help_text.replace("<b>", f"<b>{icon} ", 1)

        builder = InlineKeyboardBuilder()
        builder.button(text="◁ Back", callback_data=f"help_menu:{chat_type_filter}")

        await query.message.edit_text(help_text, parse_mode="HTML", reply_markup=builder.as_markup())
    finally:
        await query.answer()

# --- Formatting Sub-Menu Callbacks ---

@router.callback_query(F.data.startswith("help_fmt_"))
@owner_only
async def help_fmt_callback(query: types.CallbackQuery):
    # help_fmt_markdown:chat_type
    data_parts = query.data.split(":")
    action = data_parts[0]
    chat_type_filter = data_parts[1] if len(data_parts) > 1 else "group"

    text = ""
    if action == "help_fmt_markdown":
        text = HELP_TEXT_MARKDOWN
    elif action == "help_fmt_fillings":
        text = HELP_TEXT_FILLINGS
    elif action == "help_fmt_random":
        text = HELP_TEXT_RANDOM

    builder = InlineKeyboardBuilder()
    builder.button(text="◁ Back", callback_data=f"help_view:{chat_type_filter}:formatting")

    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    finally:
        await query.answer()
