# =============================================================================
# Module: Parser
# Path: utils/parser.py
# Description: Utility functions and helpers for operations related to Parser.
# =============================================================================

import re
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Tuple, Optional

# Import shared button helpers so style support is identical across all pipelines.
from utils.formatter import BUTTON_STYLES, _parse_button_token, _make_button


_BUTTON_PATTERN = re.compile(r'\[([^\]]+)\]\((buttonurl(?:#[^:)]+)?://[^)]+)\)')

def parse_message_text(text: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Parses a message text to extract custom buttons,
    returning the cleaned text and an InlineKeyboardMarkup.

    Syntax
    ------
    No style (app default)::

        [Visit](buttonurl://example.com)

    Coloured buttons (Bot API 9.4 ``style`` field)::

        [Delete](buttonurl#danger://example.com)       ← red
        [Confirm](buttonurl#success://example.com)     ← green
        [Learn](buttonurl#primary://example.com)       ← blue

    Row placement – append ``:same`` after the URL::

        [A](buttonurl://one.com)
        [B](buttonurl#primary://two.com:same)
    """
    # Fast-fail: skip processing if no brackets or buttonurl exist
    if "[" not in text or "buttonurl" not in text:
        return text, None

    # Match buttonurl://  OR  buttonurl#<style>://
    lines = text.split('\n')
    cleaned_lines = []
    button_rows = []
    current_row = []

    for line in lines:
        match = _BUTTON_PATTERN.match(line.strip())
        if match:
            label     = match.group(1)
            raw_token = match.group(2)

            url, style, same_row = _parse_button_token(raw_token)

            btn = _make_button(text=label, style=style, url=url)

            if same_row and current_row:
                current_row.append(btn)
            else:
                if current_row:
                    button_rows.append(current_row)
                current_row = [btn]
        else:
            if current_row:
                button_rows.append(current_row)
                current_row = []
            cleaned_lines.append(line)

    if current_row:
        button_rows.append(current_row)

    cleaned_text = '\n'.join(cleaned_lines)
    markup = InlineKeyboardMarkup(inline_keyboard=button_rows) if button_rows else None

    return cleaned_text, markup
