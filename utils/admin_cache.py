# =============================================================================
# Module: Admin Cache
# Path: utils/admin_cache.py
# Description: Utility functions and helpers for operations related to Admin Cache.
# =============================================================================

import time
from typing import Dict, Set, Tuple, Optional

# Cache format: {chat_id: (monotonic_timestamp, {admin_id_1, admin_id_2, ...})}
# Uses time.monotonic() — immune to NTP/DST clock jumps.
_admin_cache: Dict[int, Tuple[float, Set[int]]] = {}
CACHE_TTL  = 300   # 5 minutes — admin lists change very rarely
_CACHE_MAX = 2_000  # hard cap; evict oldest on overflow


def get_admin_ids(chat_id: int) -> Optional[Set[int]]:
    """Returns cached admin IDs if still valid, else None."""
    entry = _admin_cache.get(chat_id)
    if entry and time.monotonic() - entry[0] < CACHE_TTL:
        return entry[1]
    return None


def set_admin_ids(chat_id: int, admin_ids: Set[int]) -> None:
    """Store a fresh set of admin IDs."""
    if len(_admin_cache) >= _CACHE_MAX and chat_id not in _admin_cache:
        # Evict single oldest entry instead of clearing everything
        oldest = min(_admin_cache, key=lambda k: _admin_cache[k][0])
        del _admin_cache[oldest]
    _admin_cache[chat_id] = (time.monotonic(), admin_ids)


def clear_cache() -> None:
    """Clear the entire admin cache (e.g. on bot restart)."""
    _admin_cache.clear()


def clear_chat_cache(chat_id: int) -> None:
    """Invalidate cache for a single chat (e.g. after admin change)."""
    _admin_cache.pop(chat_id, None)
