"""Handler de envio de likes. Funciona em privado e em grupos."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.keyboards.inline import back_home, cancel_only
from app.services.autolike import AutoLikeApi
from app.states import LikeFlow
from app.utils import clean_game_id, extract_nick, is_valid_game_id

router = Router(name="likes")


async def _do_send_like(message: Message, game_id: str, autolike: AutoLikeApi) -> None:
    status_msg = await message.answer("⏳ Enviando likes...")
    result = await autolike.send_like(game_id)
    data = result.data if isinstance(result.data, dict) else {}

    if result.status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return

    if result.status == 429:
        cooldown = data.get("tempo") or data.get("cooldown") or ""
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(
                game_id=game_id,
                cooldown=(f"⏱ {cooldown}" if cooldown else ""),
            )
        )
        return

    sucesso = data.get("sucesso", result.ok)
    if not sucesso:
        # ID já recebeu no ciclo de 24h (status 200 + sucesso: false)
        cooldown = data.get("tempo") or data.get("cooldown") or ""
        await status_msg.edit_text(
            texts.LIKE_ALREADY.format(
                game_id=game_id,
                cooldown=(f"⏱ {cooldown}" if cooldown else ""),
            )
        )
        return

    enviadas = data.get("enviadas") or data.get("likes") or "?"
    nick = extract_nick(data) or "Jogador"
    await status_msg.edit_text(
        texts.LIKE_SUCCESS.format(game_id=game_id, nick=nick, enviadas=enviadas)
    )


# ---- Comando /like <id> (funciona em grupos e privado) ----
@router.message(Command("like", "likes"))
async def cmd_like(
    message: Message, command: CommandObject, state: FSMContext, autolike: AutoLikeApi
) -> None:
    arg = (command.args or "").strip()
    if not arg:
        # Sem argumento: em privado inicia o fluxo interativo
        if message.chat.type == "private":
            await state.set_state(LikeFlow.waiting_id)
            await message.answer(texts.ASK_LIKE_ID, reply_markup=cancel_only())
        else:
            await message.reply("Use assim: <code>/like SEU_ID</code>")
        return

    game_id = clean_game_id(arg.split()[0])
    if not is_valid_game_id(game_id):
        await message.reply(texts.INVALID_ID)
        return
    await _do_send_like(message, game_id, autolike)


# ---- Fluxo por botão (privado) ----
@router.callback_query(F.data == "menu:like")
async def cb_like(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LikeFlow.waiting_id)
    await query.message.edit_text(texts.ASK_LIKE_ID, reply_markup=cancel_only())
    await query.answer()


@router.message(LikeFlow.waiting_id)
async def like_receive_id(
    message: Message, state: FSMContext, autolike: AutoLikeApi
) -> None:
    game_id = clean_game_id(message.text or "")
    if not is_valid_game_id(game_id):
        await message.answer(texts.INVALID_ID, reply_markup=cancel_only())
        return
    await state.clear()
    await _do_send_like(message, game_id, autolike)
    await message.answer("Use /start para voltar ao menu.", reply_markup=back_home())
