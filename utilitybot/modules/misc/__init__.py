# utilitybot/modules/misc/__init__.py

from aiogram import Router
from .handlers import router as handlers_router
from .chat_report import router as chat_report_router

router = Router()
router.include_router(handlers_router)
router.include_router(chat_report_router)

__all__ = ["router"]
