"""Fallback para callbacks antigos ou desconhecidos."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.config import Config
from app.database import Database
from app.keyboards.inline import main_menu
from app import texts
from app.ui import edit_screen

router = Router(name="fallback")


@router.callback_query()
async def unknown_callback(
    query: CallbackQuery,
    state: FSMContext,
    config: Config,
    db: Database,
) -> None:
    """Nunca deixa botão desconhecido preso no loading."""
    try:
        await query.answer("Abrindo menu atualizado.")
    except Exception:
        pass

    try:
        await state.clear()
        await db.clear_customer_pending_input(query.from_user.id)
        balance_cents = await db.get_balance_cents(query.from_user.id)
        reseller = await db.is_reseller(query.from_user.id)
        balance = balance_cents / 100

        text = (
            texts.WELCOME.format(shop_name=config.shop_name)
            + "\n\n"
            + f"👛 Saldo: <b>R$ {balance:.2f}</b>\n"
            + f"🤝 Revenda: <b>{'Ativa ✅' if reseller else 'Não ativa'}</b>"
        )
        await edit_screen(query, text, main_menu(False))
    except Exception:
        try:
            await query.message.answer(
                "ℹ️ Esse botão pertence a uma versão antiga. "
                "Envie /start para abrir o menu atualizado."
            )
        except Exception:
            pass
