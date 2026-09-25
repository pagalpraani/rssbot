# =============================================================================
# Module: Settings Cache
# Path: utils/settings_cache.py
# Description: In-memory TTL cache for per-chat settings. Write-through: every db.
# =============================================================================


import time
from typing import Dict, Optional, Tuple, Any

_MONO = time.monotonic  # local alias avoids repeated global lookup


def _evict_oldest(d: dict) -> None:
    """Remove the single entry with the smallest (oldest) timestamp."""
    oldest_key = min(d, key=lambda k: d[k][0])
    del d[oldest_key]


# ── Settings cache ─────────────────────────────────────────────────────────────
# {chat_id: (monotonic_ts, settings_dict)}
_settings_cache: Dict[int, Tuple[float, dict]] = {}
SETTINGS_TTL  = 60    # seconds
_SETTINGS_MAX = 5_000

def get_settings(chat_id: int) -> Optional[dict]:
    entry = _settings_cache.get(chat_id)
    if entry and _MONO() - entry[0] < SETTINGS_TTL:
        return entry[1]
    return None

def set_settings(chat_id: int, data: Optional[dict]) -> None:
    if data is None:
        data = {}
    if len(_settings_cache) >= _SETTINGS_MAX and chat_id not in _settings_cache:
        _evict_oldest(_settings_cache)
    _settings_cache[chat_id] = (_MONO(), data)

def invalidate_settings(chat_id: int) -> None:
    _settings_cache.pop(chat_id, None)

def invalidate_settings_all() -> None:
    _settings_cache.clear()


# ── Chat info cache ────────────────────────────────────────────────────────────
# {chat_id: (monotonic_ts, Chat object)}
_chat_cache: Dict[int, Tuple[float, Any]] = {}
CHAT_TTL  = 300    # 5 min — title/type rarely change
_CHAT_MAX = 5_000

def get_chat(chat_id: int) -> Optional[Any]:
    entry = _chat_cache.get(chat_id)
    if entry and _MONO() - entry[0] < CHAT_TTL:
        return entry[1]
    return None

def set_chat(chat_id: int, chat: Any) -> None:
    if len(_chat_cache) >= _CHAT_MAX and chat_id not in _chat_cache:
        _evict_oldest(_chat_cache)
    _chat_cache[chat_id] = (_MONO(), chat)

def invalidate_chat(chat_id: int) -> None:
    _chat_cache.pop(chat_id, None)


# ── Member count cache ────────────────────────────────────────────────────────
# Separate from settings and chat caches. Used by formatter.py {count} and
# greetings goodbye suppression. TTL matches the settings cache (60 s).
# {chat_id: (monotonic_ts, count: int)}
_member_count_cache: Dict[int, Tuple[float, int]] = {}
MEMBER_COUNT_TTL  = 60
_MEMBER_COUNT_MAX = 5_000

def get_member_count(chat_id: int) -> Optional[int]:
    entry = _member_count_cache.get(chat_id)
    if entry and _MONO() - entry[0] < MEMBER_COUNT_TTL:
        return entry[1]
    return None

def set_member_count(chat_id: int, count: int) -> None:
    if len(_member_count_cache) >= _MEMBER_COUNT_MAX and chat_id not in _member_count_cache:
        _evict_oldest(_member_count_cache)
    _member_count_cache[chat_id] = (_MONO(), count)

def invalidate_member_count(chat_id: int) -> None:
    _member_count_cache.pop(chat_id, None)


# ── Bot self-info cache ────────────────────────────────────────────────────────
# bot.get_me() never changes at runtime. Fetched once at startup.
_bot_self = None

def get_bot_self():
    return _bot_self

def set_bot_self(user) -> None:
    global _bot_self
    _bot_self = user
