"""Apaga mensagens do bot em grupos depois de alguns segundos."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}
DEFAULT_DELAY = 20

# Mantém referência às tarefas para o asyncio não descartá-las antes da hora.
_pending: set[asyncio.Task] = set()


async def _delete_later(bot: Bot, chat_id: int, message_ids: list[int], delay: int) -> None:
    await asyncio.sleep(delay)
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:  # noqa: BLE001
            # Sem permissão de apagar ou mensagem já removida: segue em frente.
            logger.debug("Não apaguei msg=%s chat=%s", message_id, chat_id)


def auto_delete(bot: Bot, *messages: Message | None, delay: int = DEFAULT_DELAY) -> None:
    """Agenda a remoção das mensagens, somente quando estão em grupo."""
    found = [m for m in messages if m is not None and m.chat.type in GROUP_TYPES]
    if not found:
        return
    task = asyncio.create_task(
        _delete_later(bot, found[0].chat.id, [m.message_id for m in found], delay)
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)
