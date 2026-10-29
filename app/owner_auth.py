"""Autorização de dono para mensagens privadas e comandos em grupos."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import Message

from app.config import Config

logger = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}


async def resolve_owner_id(
    message: Message,
    bot: Bot,
    config: Config,
) -> int | None:
    """Retorna o ID de um dono autorizado para a mensagem.

    No privado ou quando o Telegram entrega o usuário real, valida diretamente
    pelo from_user. Em grupos, quando a mensagem chega em nome do próprio chat
    (sender_chat == chat), verifica se um dos donos configurados é administrador
    real daquele grupo.
    """
    if message.from_user and message.from_user.id in config.admin_ids:
        return int(message.from_user.id)

    sent_as_group = (
        message.chat.type in GROUP_TYPES
        and message.sender_chat is not None
        and message.sender_chat.id == message.chat.id
    )
    if not sent_as_group:
        return None

    try:
        admins = await bot.get_chat_administrators(message.chat.id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Falha ao consultar administradores do grupo chat=%s",
            message.chat.id,
        )
        return None

    admin_ids = {
        int(member.user.id)
        for member in admins
        if getattr(member, "user", None) is not None
    }
    # A mensagem anônima pode ser de qualquer admin anônimo do grupo. Só vale
    # como dono quando todos os admins anônimos do grupo são donos.
    anonymous_ids = {
        int(member.user.id)
        for member in admins
        if getattr(member, "user", None) is not None
        and getattr(member, "is_anonymous", False)
    }
    owner_ids = {int(x) for x in config.admin_ids}
    if not anonymous_ids or not anonymous_ids <= owner_ids:
        logger.info(
            "Mensagem anônima ignorada como dono: há admins anônimos que não são donos | chat=%s",
            message.chat.id,
        )
        return None

    for owner_id in config.admin_ids:
        if int(owner_id) in admin_ids:
            logger.info(
                "Dono reconhecido via sender_chat | chat=%s owner=%s",
                message.chat.id,
                owner_id,
            )
            return int(owner_id)

    return None


async def group_has_owner_admin(
    message: Message,
    bot: Bot,
    config: Config,
) -> bool:
    """True quando o grupo pertence/é administrado por um dos donos configurados."""
    if message.chat.type not in GROUP_TYPES:
        return False
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Falha ao consultar admins para licença do grupo chat=%s",
            message.chat.id,
        )
        return False

    owner_ids = {int(x) for x in config.admin_ids}
    return any(
        getattr(member, "user", None) is not None
        and int(member.user.id) in owner_ids
        for member in admins
    )
