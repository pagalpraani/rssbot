# =============================================================================
# Module: Settings Cache
# Path: utilitybot/utils/settings_cache.py
# Description: In-memory TTL cache for per-chat settings and filters. Write-through:
#              every db.
# =============================================================================


import time
from typing import Dict, List, Optional, Tuple, Any

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


# ── Filters cache ──────────────────────────────────────────────────────────────
# {chat_id: (monotonic_ts, [filter_docs])}
_filters_cache: Dict[int, Tuple[float, List[dict]]] = {}
FILTERS_TTL  = 120    # filters change rarely; 2-min TTL is safe
_FILTERS_MAX = 5_000

def get_filters(chat_id: int) -> Optional[List[dict]]:
    entry = _filters_cache.get(chat_id)
    if entry and _MONO() - entry[0] < FILTERS_TTL:
        return entry[1]
    return None

def set_filters(chat_id: int, filters: List[dict]) -> None:
    if len(_filters_cache) >= _FILTERS_MAX and chat_id not in _filters_cache:
        _evict_oldest(_filters_cache)
    _filters_cache[chat_id] = (_MONO(), filters)

def invalidate_filters(chat_id: int) -> None:
    _filters_cache.pop(chat_id, None)


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


# ── Night-mode settings cache ──────────────────────────────────────────────────
# Caches the full night_mode document (whitelist, schedule, mode, paused_until).
# Invalidated on every update_night_settings / toggle_night_mode call.
# {chat_id: (monotonic_ts, settings_dict)}
_night_settings_cache: Dict[int, Tuple[float, dict]] = {}
NIGHT_SETTINGS_TTL  = 60     # 60 s — same as general settings
_NIGHT_SETTINGS_MAX = 2_000

def get_night_settings(chat_id: int) -> Optional[dict]:
    entry = _night_settings_cache.get(chat_id)
    if entry and _MONO() - entry[0] < NIGHT_SETTINGS_TTL:
        return entry[1]
    return None

def set_night_settings(chat_id: int, data: dict) -> None:
    if len(_night_settings_cache) >= _NIGHT_SETTINGS_MAX and chat_id not in _night_settings_cache:
        _evict_oldest(_night_settings_cache)
    _night_settings_cache[chat_id] = (_MONO(), data)

def invalidate_night_settings(chat_id: int) -> None:
    _night_settings_cache.pop(chat_id, None)


# ── Soft-delete (GDPR) cache ───────────────────────────────────────────────────
# {user_id: (monotonic_ts, is_deleted: bool)}
_soft_delete_cache: Dict[int, Tuple[float, bool]] = {}
SOFT_DELETE_TTL_CLEAN   = 300   # 5 min for non-deleted users
SOFT_DELETE_TTL_DELETED =  10   # 10 s for deleted (may reactivate quickly)
_SOFT_DELETE_MAX        = 10_000

def get_soft_deleted(user_id: int) -> Optional[bool]:
    entry = _soft_delete_cache.get(user_id)
    if entry:
        ttl = SOFT_DELETE_TTL_DELETED if entry[1] else SOFT_DELETE_TTL_CLEAN
        if _MONO() - entry[0] < ttl:
            return entry[1]
    return None

def set_soft_deleted(user_id: int, is_deleted: bool) -> None:
    if len(_soft_delete_cache) >= _SOFT_DELETE_MAX and user_id not in _soft_delete_cache:
        _evict_oldest(_soft_delete_cache)
    _soft_delete_cache[user_id] = (_MONO(), is_deleted)

def invalidate_soft_deleted(user_id: int) -> None:
    _soft_delete_cache.pop(user_id, None)


# ── Membership cache (Force Subscribe) ────────────────────────────────────────
# {(user_id, channel): (monotonic_ts, is_member: bool)}
_membership_cache: Dict[tuple, Tuple[float, bool]] = {}
MEMBERSHIP_TTL_MEMBER     = 30   # 30 s for members
MEMBERSHIP_TTL_NON_MEMBER = 15   # 15 s for non-members
_MEMBERSHIP_MAX           = 50_000

def get_membership(user_id: int, channel: str) -> Optional[bool]:
    entry = _membership_cache.get((user_id, channel))
    if entry:
        ttl = MEMBERSHIP_TTL_NON_MEMBER if not entry[1] else MEMBERSHIP_TTL_MEMBER
        if _MONO() - entry[0] < ttl:
            return entry[1]
    return None

def set_membership(user_id: int, channel: str, is_member: bool) -> None:
    key = (user_id, channel)
    if len(_membership_cache) >= _MEMBERSHIP_MAX and key not in _membership_cache:
        _evict_oldest(_membership_cache)
    _membership_cache[key] = (_MONO(), is_member)

def invalidate_membership(user_id: int, channel: str) -> None:
    _membership_cache.pop((user_id, channel), None)


# ── Night-mode enabled cache ───────────────────────────────────────────────────
# Fast-path: skip DB entirely when night mode is known off (the common case).
# {chat_id: (monotonic_ts, enabled: bool)}
_night_enabled_cache: Dict[int, Tuple[float, bool]] = {}
NIGHT_ENABLED_TTL  = 60    # invalidated on /nightmode on|off
_NIGHT_ENABLED_MAX = 2_000

def get_night_enabled(chat_id: int) -> Optional[bool]:
    entry = _night_enabled_cache.get(chat_id)
    if entry and _MONO() - entry[0] < NIGHT_ENABLED_TTL:
        return entry[1]
    return None

def set_night_enabled(chat_id: int, enabled: bool) -> None:
    if len(_night_enabled_cache) >= _NIGHT_ENABLED_MAX and chat_id not in _night_enabled_cache:
        _evict_oldest(_night_enabled_cache)
    _night_enabled_cache[chat_id] = (_MONO(), enabled)

def invalidate_night_enabled(chat_id: int) -> None:
    _night_enabled_cache.pop(chat_id, None)


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
