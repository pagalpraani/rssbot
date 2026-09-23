# =============================================================================
# Module: Config
# Path: utilitybot/config.py
# Description: Configuration loader parsing environment variables and defining global
#              settings.
# =============================================================================

import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_env_variable(var_name: str, is_int: bool = False, default=None):
    """
    Retrieves and validates an environment variable.

    Args:
        var_name (str): The name of the environment variable.
        is_int (bool): Whether to cast the variable to an integer.
        default: The default value to return if the variable is not found.

    Returns:
        The value of the environment variable.

    Raises:
        SystemExit: If the environment variable is not set and no default is provided.
    """
    value = os.getenv(var_name, default)
    if value is None:
        sys.exit(f"Error: Environment variable {var_name} not set. Please create a .env file and add it.")

    if is_int:
        try:
            return int(value)
        except ValueError:
            sys.exit(f"Error: Environment variable {var_name} must be an integer.")

    return value

# --- Bot Configuration ---
BOT_TOKEN = get_env_variable("BOT_TOKEN")
OWNER_ID = get_env_variable("OWNER_ID", is_int=True)

# --- Database Configuration ---
MONGO_URI = get_env_variable("MONGO_URI")
DB_NAME = get_env_variable("DB_NAME", default="RSSFeedBot")

# --- Logging Configuration ---
LOG_LEVEL = get_env_variable("LOG_LEVEL", default="INFO").upper()
DEV_LOG_CHANNEL = get_env_variable("DEV_LOG_CHANNEL", is_int=True, default=0)

# --- RSS Media Relay ---
# Cloudflare Worker that proxies oversized Telegram media downloads.
RSS_RELAY_BASE = get_env_variable(
    "RSS_RELAY_BASE",
    default="https://media-relay.gurjar56.workers.dev"
)

# --- RSS Feed Item Cache ---
# How many "already posted" item records to keep per feed, Persistently.
RSS_PROCESSED_CACHE_CAP = get_env_variable("RSS_PROCESSED_CACHE_CAP", is_int=True, default=1000)

# Safety cap: the max number of items actually POSTED to a chat in a single
# feed-check cycle. Protects against flooding a chat when a feed is linked
# for the first time (or has built up a large backlog) — anything older than
# the newest N unseen items is silently marked as seen instead of being sent.
RSS_MAX_NEW_ITEMS_PER_CYCLE = get_env_variable("RSS_MAX_NEW_ITEMS_PER_CYCLE", is_int=True, default=30)
