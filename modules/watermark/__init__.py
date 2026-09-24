# =============================================================================
# Module: Watermark Init
# Path: modules/watermark/__init__.py
# =============================================================================

from . import handlers

router = handlers.router
__all__ = ["router"]
