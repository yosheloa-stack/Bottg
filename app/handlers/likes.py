"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

import html
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import texts
from app.services.likes import LikesApi
from app.utils import clean_game_id

router = Router(name="likes")
logger = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}


def _valid_like_uid(game_id: str) -> bool:
    """A Likes Painel API aceita UIDs numéricos de 8 a 11 dígitos."""
    return game_id.isdigit() and 8 <= len(game_id) <= 11


async def _do_send_like(message: Message, game_id: str, likes_api: LikesApi) -> None:
    status_msg = await message.reply("⏳ Enviando likes...")
    result = await likes_api.send_like(game_id)
    data = result.data if isinstance(result.data, dict) else {}
    error = (result.error or "").strip()

    if result.ok:
        sent = data.get("likes_sent")
        if sent is None:
            sent = 0

        nick = data.get("nickname") or "Jogador"
        await status_msg.edit_text(
            texts.LIKE_SUCCESS.format(
                game_id=game_id,
                nick=html.escape(str(nick)),
                enviadas=sent,
            )
        )
        return

    logger.warning(
        "Falha no envio de likes uid=%s status=%s error=%s data=%s",
        game_id,
        result.status,
        error,
        data,
    )

    if result.status == 400:
        await status_msg.edit_text(
            f"❌ {html.escape(error or 'Parâmetros inválidos para envio de likes.')}"
        )
        return

    if result.status in (401, 403):
        await status_msg.edit_text(
            "⚠️ O serviço de likes está temporariamente indisponível. "
            "Avise um administrador."
        )
        return

    if result.status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return

    if result.status == 409:
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(game_id=game_id, cooldown="")
        )
        return

    if result.status == 429:
        normalized = error.lower()
        if "quota" in normalized or "diári" in normalized or "diari" in normalized:
            await status_msg.edit_text(
                f"⚠️ {html.escape(error or 'Limite diário de likes atingido.')}"
            )
        else:
            await status_msg.edit_text(
                f"⏳ {html.escape(error or 'Limite de requisições por minuto atingido. Tente novamente em instantes.')}"
            )
        return

    if result.status == 503:
        await status_msg.edit_text(
            "⏳ Serviço de likes temporariamente indisponível. "
            "Tente novamente em instantes."
        )
        return

    if error and result.status:
        await status_msg.edit_text(f"❌ {html.escape(error)}")
        return

    await status_msg.edit_text(texts.GENERIC_ERROR)


@router.message(Command("like", "likes"))
async def cmd_like(
    message: Message, command: CommandObject, likes_api: LikesApi
) -> None:
    # Bloqueia no privado — likes só em grupos
    if message.chat.type not in GROUP_TYPES:
        await message.answer(texts.LIKE_PRIVATE_BLOCKED)
        return

    arg = (command.args or "").strip()
    if not arg:
        await message.reply("Use assim: <code>/like SEU_ID</code>")
        return

    game_id = clean_game_id(arg.split()[0])
    if not _valid_like_uid(game_id):
        await message.reply(
            "⚠️ ID inválido. Envie somente números, de 8 a 11 dígitos."
        )
        return

    await _do_send_like(message, game_id, likes_api)
