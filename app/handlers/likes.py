"""Handler de envio de likes. Funciona SOMENTE em grupos (via /like)."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import texts
from app.services.autolike import AutoLikeApi
from app.utils import clean_game_id, extract_nick, is_valid_game_id

router = Router(name="likes")

GROUP_TYPES = {"group", "supergroup"}


async def _do_send_like(message: Message, game_id: str, autolike: AutoLikeApi) -> None:
    status_msg = await message.reply("⏳ Enviando likes...")
    result = await autolike.send_like(game_id)
    data = result.data if isinstance(result.data, dict) else {}

    if result.status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return

    if result.status == 429:
        cooldown = data.get("tempo") or data.get("cooldown") or ""
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(
                game_id=game_id, cooldown=(f"⏱ {cooldown}" if cooldown else "")
            )
        )
        return

    sucesso = data.get("sucesso", result.ok)
    if not sucesso:
        cooldown = data.get("tempo") or data.get("cooldown") or ""
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(
                game_id=game_id, cooldown=(f"⏱ {cooldown}" if cooldown else "")
            )
        )
        return

    enviadas = data.get("enviadas") or data.get("likes") or "?"
    nick = extract_nick(data) or "Jogador"
    await status_msg.edit_text(
        texts.LIKE_SUCCESS.format(game_id=game_id, nick=nick, enviadas=enviadas)
    )


@router.message(Command("like", "likes"))
async def cmd_like(
    message: Message, command: CommandObject, autolike: AutoLikeApi
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
    if not is_valid_game_id(game_id):
        await message.reply(texts.INVALID_ID)
        return

    await _do_send_like(message, game_id, autolike)
