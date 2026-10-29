"""Suporte: atendimento por IA (Square Cloud) com fallback para a equipe humana."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home
from app.states import SupportChat
from app.support_agent import SupportAgent
from app.ui import edit_screen

logger = logging.getLogger(__name__)

router = Router(name="support")

TELEGRAM_LIMIT = 4000


def _support_chat_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Encerrar atendimento", callback_data="support:end")],
        ]
    )


def _intro(agent: SupportAgent, user_id: int) -> str:
    if agent.is_admin(user_id):
        return (
            "🤖 <b>Suporte IA (modo dono)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Pergunte sobre qualquer pedido, nº de pagamento, ID do Telegram, "
            "UID do Free Fire ou falhas recentes.\n\n"
            "Ex.: <i>o que aconteceu com o pedido #120?</i>"
        )
    return (
        "🤖 <b>Suporte</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Oi! Sou a atendente virtual. Me conta o que aconteceu com seu pedido "
        "ou pagamento (se tiver, mande o nº do pedido #).\n\n"
        "Escreva sua mensagem aqui embaixo 👇"
    )


def _human_support_text(config: Config) -> str:
    return (
        "🆘 <b>Suporte</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Precisa de ajuda com um pedido ou pagamento?\n"
        f"Fale com nossa equipe: {config.support_username}\n\n"
        "Tenha em mãos o número do seu pedido (#)."
    )


@router.callback_query(F.data == "menu:support")
async def cb_support(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    support_agent: SupportAgent,
) -> None:
    try:
        await query.answer()
    except Exception:  # noqa: BLE001
        pass
    if not support_agent.configured or query.message.chat.type != "private":
        await edit_screen(query, _human_support_text(config), back_home())
        return
    await state.clear()
    await state.set_state(SupportChat.chatting)
    await state.update_data(support_history=[])
    await query.message.answer(_intro(support_agent, query.from_user.id), reply_markup=_support_chat_menu())


@router.message(Command("suporte"), F.chat.type == "private")
# Deep link https://t.me/BOT?start=suporte (botão do aviso de limite semanal do /like).
@router.message(CommandStart(deep_link=True, magic=F.args == "suporte"), F.chat.type == "private")
async def cmd_support(
    message: Message,
    state: FSMContext,
    config: Config,
    support_agent: SupportAgent,
) -> None:
    if not support_agent.configured:
        await message.answer(_human_support_text(config), reply_markup=back_home())
        return
    await state.clear()
    await state.set_state(SupportChat.chatting)
    await state.update_data(support_history=[])
    await message.answer(_intro(support_agent, message.from_user.id), reply_markup=_support_chat_menu())


@router.callback_query(F.data == "support:end")
async def cb_support_end(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await query.answer("Atendimento encerrado")
    except Exception:  # noqa: BLE001
        pass
    await query.message.answer(
        "✅ Atendimento encerrado. Se precisar, é só tocar em 🆘 Suporte de novo.",
        reply_markup=back_home(),
    )


@router.message(SupportChat.chatting, F.chat.type == "private", F.text.regexp(r"^(?!/)"))
async def support_message(
    message: Message,
    state: FSMContext,
    support_agent: SupportAgent,
    db: Database,
) -> None:
    # Pedido pago esperando o UID: o número vai para a ativação, não para a IA.
    text = (message.text or "").strip()
    if text.isdigit() and 8 <= len(text) <= 11 and message.from_user:
        if await db.get_awaiting_id_order(message.from_user.id):
            await state.clear()
            raise SkipHandler

    data = await state.get_data()
    if data.get("support_busy"):
        await message.answer("⏳ Ainda estou analisando sua mensagem anterior, só um instante.")
        return
    await state.update_data(support_busy=True)
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        reply, history = await support_agent.answer(
            message.from_user.id,
            list(data.get("support_history") or []),
            (message.text or "")[:2000],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Erro no atendimento IA user=%s", message.from_user.id)
        await state.update_data(support_busy=False)
        await message.answer("⚠️ Tive um problema para responder agora. Tente de novo em instantes.")
        return

    await state.update_data(support_history=history, support_busy=False)
    # Texto puro: a resposta da IA não passa pelo parser HTML do Telegram.
    for start in range(0, len(reply), TELEGRAM_LIMIT):
        chunk = reply[start : start + TELEGRAM_LIMIT]
        is_last = start + TELEGRAM_LIMIT >= len(reply)
        await message.answer(
            chunk,
            parse_mode=None,
            reply_markup=_support_chat_menu() if is_last else None,
        )
