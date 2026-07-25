"""Handler de consulta de informações do jogador."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import texts
from app.keyboards.inline import back_home, cancel_only
from app.services.autolike import AutoLikeApi
from app.states import InfoFlow
from app.ui import edit_screen
from app.utils import clean_game_id, extract_nick, is_valid_game_id

router = Router(name="info")


def _format_info(game_id: str, data: dict) -> str:
    nick = extract_nick(data) or "—"
    # Tenta extrair campos comuns de forma tolerante
    src = data
    player = data.get("player") or data.get("jogador") or data.get("basicInfo")
    if isinstance(player, dict):
        src = {**data, **player}

    def g(*keys, default="—"):
        for k in keys:
            if src.get(k) not in (None, ""):
                return src[k]
        return default

    return (
        f"🔎 <b>Informações do Jogador</b>\n\n"
        f"👤 Nick: <b>{nick}</b>\n"
        f"🎮 ID: <code>{game_id}</code>\n"
        f"⭐ Nível: <b>{g('level', 'nivel', 'nível')}</b>\n"
        f"❤️ Likes: <b>{g('likes', 'curtidas')}</b>\n"
        f"🏆 Ranking: <b>{g('ranking', 'rank')}</b>\n"
        f"🛡️ Clã: <b>{g('clan', 'cla', 'clã', 'guild')}</b>\n"
        f"🌍 Região: <b>{g('region', 'regiao', 'região', default='BR')}</b>"
    )


async def _do_info(message: Message, game_id: str, autolike: AutoLikeApi) -> None:
    status_msg = await message.answer("⏳ Consultando...")
    result = await autolike.info_player(game_id)
    if result.status == 404:
        await status_msg.edit_text(texts.LIKE_NOT_FOUND)
        return
    if not result.ok or not isinstance(result.data, dict):
        await status_msg.edit_text(texts.GENERIC_ERROR)
        return
    await status_msg.edit_text(_format_info(game_id, result.data))


@router.message(Command("info"))
async def cmd_info(
    message: Message, command: CommandObject, state: FSMContext, autolike: AutoLikeApi
) -> None:
    arg = (command.args or "").strip()
    if not arg:
        if message.chat.type == "private":
            await state.set_state(InfoFlow.waiting_id)
            await message.answer(texts.ASK_INFO_ID, reply_markup=cancel_only())
        else:
            await message.reply("Use assim: <code>/info SEU_ID</code>")
        return
    game_id = clean_game_id(arg.split()[0])
    if not is_valid_game_id(game_id):
        await message.reply(texts.INVALID_ID)
        return
    await _do_info(message, game_id, autolike)


@router.callback_query(F.data == "menu:info")
async def cb_info(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(InfoFlow.waiting_id)
    await edit_screen(query, texts.ASK_INFO_ID, cancel_only())
    await query.answer()


@router.message(InfoFlow.waiting_id)
async def info_receive_id(
    message: Message, state: FSMContext, autolike: AutoLikeApi
) -> None:
    game_id = clean_game_id(message.text or "")
    if not is_valid_game_id(game_id):
        await message.answer(texts.INVALID_ID, reply_markup=cancel_only())
        return
    await state.clear()
    await _do_info(message, game_id, autolike)
    await message.answer("Use /start para voltar ao menu.", reply_markup=back_home())
