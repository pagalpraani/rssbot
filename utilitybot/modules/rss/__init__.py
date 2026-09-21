# utilitybot/modules/rss/__init__.py
from . import handlers
from . import service

from . import dashboard
from aiogram import Router
router = Router()
router.include_router(handlers.router)
router.include_router(dashboard.router)
__all__ = ["router", "service"]
