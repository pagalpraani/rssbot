# =============================================================================
# Module: Blocklist
# Path: modules/blocklist/utils.py
# Description: Utility functions and helpers for operations related to Blocklist.
# =============================================================================

import re
from typing import Dict, Optional, Tuple

# Compiled blocklist cache: { chat_id: (mode, combined_pattern | None, [keywords]) }
# Keyed by chat_id so each chat has its own compiled pattern.
# Invalidated when the blocklist settings are updated.
_blocklist_cache: Dict[int, Tuple[str, Optional[re.Pattern], list]] = {}
_MAX_CACHE = 500


def invalidate_blocklist_cache(chat_id: int) -> None:
    """Call this whenever blocklist settings change for a chat."""
    _blocklist_cache.pop(chat_id, None)


def _build_pattern(keywords: list, mode: str) -> Optional[re.Pattern]:
    """Compiles all keywords into a single combined alternation pattern."""
    if not keywords:
        return None
    escaped = [re.escape(kw) for kw in keywords]
    if mode == "whole_word":
        combined = "|".join(rf"\b{e}\b" for e in escaped)
    else:
        combined = "|".join(escaped)
    return re.compile(combined, re.IGNORECASE)


def check_text_blocking(text: str, settings: dict, chat_id: int = 0) -> Optional[str]:
    """
    Checks if the text matches any blocklist keyword.
    Returns the first matched keyword string, or None.

    Compiles all keywords into a single regex per chat and caches it,
    so re.compile() is only called when the blocklist changes.
    """
    if not settings.get("blocklist_enabled", True):
        return None

    blocklist: list = settings.get("blocklist", [])
    if not blocklist:
        return None

    mode: str = settings.get("blocklist_mode", "whole_word")

    # Cache lookup (by chat_id when provided, else skip cache)
    if chat_id:
        cached = _blocklist_cache.get(chat_id)
        if cached and cached[0] == mode and cached[2] == blocklist:
            pattern = cached[1]
        else:
            pattern = _build_pattern(blocklist, mode)
            if len(_blocklist_cache) >= _MAX_CACHE:
                # Evict one arbitrary entry rather than clearing all
                _blocklist_cache.pop(next(iter(_blocklist_cache)))
            _blocklist_cache[chat_id] = (mode, pattern, blocklist)
    else:
        pattern = _build_pattern(blocklist, mode)

    if pattern is None:
        return None

    m = pattern.search(text)
    if m:
        return m.group(0)
    return None
