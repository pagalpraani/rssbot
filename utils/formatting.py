# =============================================================================
# Module: Formatting
# Path: utils/formatting.py
# Description: Core sanitization utilities for message formatting. IMPORTANT: - This
#              sanitizer is for MarkdownV2 safety.
# =============================================================================


import re

# MarkdownV2 special characters (Telegram spec)
# NOTE: '>' is intentionally EXCLUDED
_MD_V2_SPECIALS = r'[_*\[\]()~`#+\-=|{}.!]'

# ⚡ Bolt Optimization: Pre-compile regexes for fast-fail substring search and rapid substitution
_SANITIZE_FAST_FAIL = re.compile(_MD_V2_SPECIALS)
_SANITIZE_PATTERN = re.compile(rf'(?<!\\)({_MD_V2_SPECIALS})')

_SANITIZE_FINAL_FAST_FAIL = re.compile(r'\\\\')
_SANITIZE_FINAL_PATTERN = re.compile(r'\\\\([_*\[\]()~`#+\-=|{}.!])')

def sanitize_text(text: str) -> str:
    """
    Escapes MarkdownV2 special characters in untrusted text.

    - Safe for usernames, titles, names, placeholders
    - Does NOT escape '>' so blockquotes can be detected
    - Does NOT modify already-escaped characters
    """

    if not text or not _SANITIZE_FAST_FAIL.search(text):
        return text

    return _SANITIZE_PATTERN.sub(
        r'\\\1',
        text,
    )


def sanitize_final_text(text: str) -> str:
    """
    Final sanitizer for MarkdownV2 messages.

    This assumes:
    - Structural decisions (quotes/entities) are already made
    - Text is going out via parse_mode=MarkdownV2
    """

    if not text or not _SANITIZE_FINAL_FAST_FAIL.search(text):
        return text

    # Normalize accidental double escapes
    return _SANITIZE_FINAL_PATTERN.sub(r'\\\1', text)
