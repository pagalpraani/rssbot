# =============================================================================
# Module: Keyboards
# Path: utilitybot/utils/keyboards.py
# Description: Shared generic InlineKeyboard builders used across multiple modules.
#              Module-specific keyboards stay in their own module's keyboards.py.
# =============================================================================

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_cancel_kb(callback_data: str = "cancel") -> InlineKeyboardMarkup:
    """Generic single-button Cancel keyboard. callback_data defaults to 'cancel'."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data=callback_data)]]
    )


def get_confirm_cancel_kb(confirm_data: str, cancel_data: str) -> InlineKeyboardMarkup:
    """Generic Yes/No confirmation keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yes", callback_data=confirm_data),
        InlineKeyboardButton(text="❌ No", callback_data=cancel_data),
    ]])
