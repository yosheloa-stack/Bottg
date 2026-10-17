"""Registro central dos routers de handlers."""
from aiogram import Dispatcher

from app import backup

from app.handlers import admin, common, fallback, info, likes, owner_autolike, paid_autolike, store, support, vip, vip2


def register_handlers(dp: Dispatcher) -> None:
    # Suporte IA primeiro: o estado de atendimento tem prioridade sobre os demais fluxos.
    dp.include_router(support.router)
    dp.include_router(backup.router)
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
    # Último: encerra loading de qualquer botão antigo/desconhecido.
    dp.include_router(fallback.router)
