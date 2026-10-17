"""Avisos públicos de compras e envios no grupo configurado pelo dono."""
from __future__ import annotations

import html
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database import Database
from app.utils import format_price


async def _target(db: Database) -> tuple[int, int | None] | None:
    raw_chat = (await db.get_owner_setting("ANNOUNCEMENT_CHAT_ID") or "").strip()
    if not raw_chat:
        return None
    try:
        chat_id = int(raw_chat)
    except ValueError:
        return None

    raw_thread = (await db.get_owner_setting("ANNOUNCEMENT_THREAD_ID") or "").strip()
    try:
        thread_id = int(raw_thread) if raw_thread else None
    except ValueError:
        thread_id = None
    return chat_id, thread_id


async def _user_label(db: Database, user_id: int) -> str:
    user = await db.get_user(int(user_id))
    if user:
        username = str(user.get("username") or "").strip().lstrip("@")
        if username:
            return f"@{html.escape(username)}"
        first_name = str(user.get("first_name") or "").strip()
        if first_name:
            return html.escape(first_name)
    return f"<code>{int(user_id)}</code>"


async def _acquire_markup(bot: Bot) -> InlineKeyboardMarkup | None:
    me = await bot.get_me()
    if not me.username:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Adquirir",
                    url=f"https://t.me/{me.username}?start=shop",
                )
            ]
        ]
    )


async def send_announcement(
    bot: Bot,
    db: Database,
    text: str,
) -> bool:
    target = await _target(db)
    if not target:
        return False

    chat_id, thread_id = target
    kwargs = {}
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id

    try:
        await bot.send_message(
            chat_id,
            text,
            reply_markup=await _acquire_markup(bot),
            **kwargs,
        )
        return True
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Falha ao enviar anúncio chat=%s thread=%s",
            chat_id,
            thread_id,
        )
        return False


async def announce_purchase(
    bot: Bot,
    db: Database,
    order: dict,
    product_title: str,
) -> None:
    if str(order.get("product_code") or "") == "balance_topup":
        return
    # Só anuncia compras feitas no privado do bot (chats de grupo têm ID negativo).
    payment_chat_id = order.get("payment_chat_id")
    if payment_chat_id is not None and int(payment_chat_id) < 0:
        return
    if not await db.mark_purchase_announced(int(order["id"])):
        return

    user = await _user_label(db, int(order["user_id"]))
    amount = Decimal(str(order.get("amount") or "0"))
    await send_announcement(
        bot,
        db,
        "🛒 <b>NOVA COMPRA REALIZADA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Cliente: <b>{user}</b>\n"
        f"📦 Produto: <b>{product_title}</b>\n"
        f"💰 Valor: <b>R$ {format_price(amount)}</b>\n\n"
        "✅ Pagamento confirmado.",
    )


async def announce_autolike_send(
    bot: Bot,
    db: Database,
    *,
    user_id: int,
    plan_name: str,
    uid: str,
    nickname: str,
    likes_sent: int,
) -> None:
    user = await _user_label(db, int(user_id))
    safe_nick = html.escape(str(nickname or "Jogador"))
    await send_announcement(
        bot,
        db,
        "💚 <b>ENVIO REALIZADO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Cliente: <b>{user}</b>\n"
        f"📦 Plano: <b>{html.escape(plan_name)}</b>\n"
        f"🎮 Nick: <b>{safe_nick}</b>\n"
        f"🆔 UID: <code>{html.escape(str(uid))}</code>\n"
        f"❤️ Likes enviados: <b>+{max(0, int(likes_sent))}</b>",
    )
