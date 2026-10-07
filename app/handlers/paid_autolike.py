"""Ativação do Auto-Like Premium após confirmação do PIX."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app import texts
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home
from app.utils import clean_game_id

router = Router(name="paid_autolike")


def _valid_uid(uid: str) -> bool:
    return uid.isdigit() and 8 <= len(uid) <= 11


@router.message(F.text)
async def receive_paid_autolike_uid(
    message: Message,
    db: Database,
    config: Config,
) -> None:
    if not message.from_user:
        return

    order = await db.get_awaiting_id_order(message.from_user.id)
    if not order:
        return

    uid = clean_game_id(message.text or "")
    if not _valid_uid(uid):
        await message.reply(
            "⚠️ <b>UID inválido.</b>\n"
            "Envie somente o ID numérico da conta, com 8 a 11 dígitos."
        )
        return

    product = config.products.get(order["product_code"])
    if not product or product.delivery != "ffhub_autolike":
        await db.update_order_status(order["id"], "failed")
        await message.reply(
            "⚠️ Não consegui localizar o plano deste pedido. "
            "Fale com o suporte.",
            reply_markup=back_home(),
        )
        return

    await db.set_order_game_id(order["id"], uid)
    await db.create_ffhub_autolike_subscription(
        order_id=order["id"],
        user_id=message.from_user.id,
        game_id=uid,
        days_total=product.days,
    )
    await db.update_order_status(order["id"], "active")

    await message.reply(
        texts.PREMIUM_AUTOLIKE_ACTIVATED.format(
            game_id=uid,
            days=product.days,
        ),
        reply_markup=back_home(),
    )
