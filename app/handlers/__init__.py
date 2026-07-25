"""Registro central dos routers de handlers."""
from aiogram import Dispatcher

from app.handlers import admin, common, info, likes, store


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(common.router)
    dp.include_router(likes.router)
    dp.include_router(info.router)
    dp.include_router(store.router)
    dp.include_router(admin.router)
