# =============================================================================
# Module: Logging Init
# Path: modules/logging/__init__.py
# =============================================================================

from aiogram import Router
from . import handlers

router = Router()
router.include_router(handlers.router)
