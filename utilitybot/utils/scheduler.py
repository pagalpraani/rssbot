# =============================================================================
# Module: Scheduler
# Path: utilitybot/utils/scheduler.py
# Description: Background polling loop that periodically asks the RSS service
#              to check every feed and dispatch new items. Per-feed frequency
#              is controlled by each feed's own configured interval (see
#              modules/rss/service.py: fetch_and_process_feeds) — this loop
#              just needs to tick at least as often as the shortest interval
#              a user is allowed to configure.
# =============================================================================

import asyncio
from typing import Optional
from aiogram import Bot
from ..utils.logger import get_logger

log = get_logger(__name__)

_bot_instance: Optional[Bot] = None
_poll_task: Optional[asyncio.Task] = None

# How often the loop wakes up to check for due feeds, in seconds.
POLL_INTERVAL_SECONDS = 60


def set_bot_instance(bot: Bot):
    """Sets the shared bot instance used by the RSS polling loop."""
    global _bot_instance
    _bot_instance = bot


async def _poll_loop():
    # Imported lazily to avoid a circular import at module load time
    # (rss.service imports from the database layer, which is fine, but
    # importing at the top of this file would run before the app has
    # finished wiring everything up).
    from ..modules.rss.service import fetch_and_process_feeds

    log.info(f"RSS polling loop started (checking every {POLL_INTERVAL_SECONDS}s).")
    while True:
        try:
            if _bot_instance is not None:
                await fetch_and_process_feeds(_bot_instance)
        except Exception as e:
            log.error(f"RSS polling cycle failed: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def start_rss_polling():
    """Starts the background RSS polling task. Call once on bot startup."""
    global _poll_task
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(_poll_loop())


def stop_rss_polling():
    """Cancels the background RSS polling task. Call on bot shutdown."""
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        _poll_task.cancel()
    _poll_task = None
