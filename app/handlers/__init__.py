"""Registro central dos routers de handlers."""
import logging

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

from app import backup, groups
from app.user_tracker import UserTrackerMiddleware

from app.handlers import (
    admin,
    binance,
    common,
    fallback,
    info,
    likes,
    owner_autolike,
    paid_autolike,
    payments,
    store,
    support,
    vip,
    vip2,
)


logger = logging.getLogger(__name__)


async def _on_error(event: ErrorEvent) -> bool:
    """Nunca deixa o usuário sem resposta quando um comando quebra."""
    logger.error(
        "Erro não tratado no update %s",
        event.update.update_id,
        exc_info=event.exception,
    )
    message = event.update.message
    if message is not None and (message.text or "").startswith("/"):
        try:
            await message.answer(
                "⚠️ Não consegui processar esse comando agora. "
                "Tente novamente em instantes."
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao avisar erro no chat %s", message.chat.id)
    return True


def register_handlers(dp: Dispatcher) -> None:
    # Qualquer erro em comando vira aviso no chat, em vez de silêncio.
    dp.errors.register(_on_error)
    # Registra os grupos onde o bot está (para o /grupos do dono).
    dp.message.outer_middleware(groups.GroupTrackerMiddleware())
    # Salva @username/nome de todo cliente que interage com o bot.
    user_tracker = UserTrackerMiddleware()
    dp.message.outer_middleware(user_tracker)
    dp.callback_query.outer_middleware(user_tracker)
    dp.include_router(groups.router)
    # Suporte IA primeiro: o estado de atendimento tem prioridade sobre os demais fluxos.
    dp.include_router(support.router)
    dp.include_router(backup.router)
    dp.include_router(common.router)
    # Controle de pagamentos (VIP, VIP2, aluguel) — só donos.
    dp.include_router(payments.router)
    # Comandos administrativos por texto têm prioridade sobre estados de compra.
    dp.include_router(admin.router)
    dp.include_router(binance.router)
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
