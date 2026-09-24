# =============================================================================
# Module: Modules Package
# Path: modules/__init__.py
# Description: Registers every active module. Only modules that exist on disk
#              for the RSS-focused bot are listed here.
# =============================================================================

from . import core
from . import misc
from . import blocklist
from . import replacements
from . import marginals
from . import logging
from . import rss
from . import watermark

__all__ = [
    "core",
    "misc",
    "blocklist",
    "replacements",
    "marginals",
    "logging",
    "rss",
    "watermark",
]
