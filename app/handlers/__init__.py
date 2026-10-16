"""Registro central dos routers de handlers."""
from aiogram import Dispatcher

from app.handlers import admin, common, info, likes, owner_autolike, paid_autolike, store, vip, vip2


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(common.router)
    # Comandos administrativos por texto têm prioridade sobre estados de compra.
    dp.include_router(admin.router)
    dp.include_router(likes.router)
    dp.include_router(info.router)
    dp.include_router(store.router)
    dp.include_router(vip.router)
    dp.include_router(vip2.router)
    dp.include_router(owner_autolike.router)
    # Deve ficar por último: captura apenas UID de pedidos já pagos.
    dp.include_router(paid_autolike.router)
