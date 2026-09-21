# =============================================================================
# Module: Logger
# Path: utilitybot/utils/logger.py
# Description: Utility functions and helpers for operations related to Logger.
# =============================================================================

import sys
import json
import logging
import asyncio
from datetime import datetime, timezone
from .. import config

# We cannot import LogManager here directly to avoid circular imports if LogManager imports logger
# But LogManager imports `db` and `config` and `aiogram`. `logger` only imports `config`.
# It should be fine to import LogManager inside the handler method to be safe.

class JsonFormatter(logging.Formatter):
    """
    Formats log records as JSON objects.
    """
    def format(self, record):
        log_object = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "event": record.getMessage(),
        }
        if record.exc_info:
            log_object["exc_info"] = self.formatException(record.exc_info)

        # Add extra fields if they exist
        if hasattr(record, 'extra_data'):
            log_object.update(record.extra_data)

        return json.dumps(log_object)

class TelegramLogHandler(logging.Handler):
    """
    Custom Logging Handler to forward critical logs to the Dev Channel via LogManager.
    """
    def emit(self, record):
        # We only care about ERROR and CRITICAL for Telegram
        if record.levelno < logging.ERROR:
            return

        try:
            from .log_manager import LogManager

            # Use asyncio to schedule the coroutine, as logging is synchronous
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    msg = record.getMessage()
                    if record.exc_info:
                        # Append exception info
                        msg += f"\n\nTraceback:\n{self.format(record)}"

                    # Truncate if too long for Telegram (4096 limit, keep safe buffer)
                    if len(msg) > 3000:
                        msg = msg[:3000] + "... (truncated)"

                    loop.create_task(
                        LogManager.log_dev(
                            level=record.levelname,
                            module=record.name,
                            message=msg
                        )
                    )
            except RuntimeError:
                # No running loop, skip
                pass
        except ImportError:
            pass
        except Exception:
            self.handleError(record)

def setup_logger():
    """
    Configures and returns a logger with a JSON formatter and Telegram Handler.
    """
    logger = logging.getLogger("CCSUniversityBot")
    logger.setLevel(config.LOG_LEVEL)

    # Prevent duplicate logs if already configured
    if logger.hasHandlers():
        logger.handlers.clear()

    # 1. Console Handler (JSON)
    console_handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. Telegram Handler (Global Dev Log)
    if config.DEV_LOG_CHANNEL:
        telegram_handler = TelegramLogHandler()
        telegram_handler.setLevel(logging.ERROR) # Only ERROR/CRITICAL
        # We don't need a formatter here, the handler constructs the message manually via LogManager
        # But we might need one for the traceback formatting inside `emit`
        telegram_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(telegram_handler)

    return logger

# Initialize the logger
log = setup_logger()

def get_logger(module_name: str):
    """
    Returns a logger for a specific module.
    """
    return logging.getLogger(f"CCSUniversityBot.{module_name}")
