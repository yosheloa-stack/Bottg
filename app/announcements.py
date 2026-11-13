"""Avisos públicos de compras e envios no grupo configurado pelo dono."""
from __future__ import annotations

import html
import json
import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database import Database
from app.utils import format_price

logger = logging.getLogger(__name__)


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
            ],
            [
                InlineKeyboardButton(
                    text="➕ Adicionar em grupo",
                    url=f"https://t.me/{me.username}?startgroup=true",
                )
            ],
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


def _purchase_quantity(order: dict, delivery: str) -> str:
    """O que o cliente escolheu: likes por dia e dias, passe, revenda, aluguel."""
    days = int(order.get("plan_days") or 0)
    days_text = f"{days} dia{'s' if days != 1 else ''}"
    if delivery == "autosystem_autolike":
        return f"220 likes por dia • {days_text}" if days else "220 likes por dia"
    if delivery == "ffhub_autolike":
        base = "500 a 1.000 likes por dia"
        return f"{base} • {days_text}" if days else base
    if delivery == "ffhub_like_once":
        return "1 envio de likes"
    if delivery == "passe":
        return "1 Passe de Elite"
    if delivery == "reseller_access":
        return f"Acesso de revenda • {days_text}" if days else "Acesso de revenda"
    if delivery == "vip_access":
        return f"VIP • {days_text}" if days else "VIP"
    if delivery == "balance_topup":
        return "Recarga de saldo"
    if delivery == "bot_rental":
        return f"Aluguel por {days_text}" if days else "Aluguel do bot"
    return ""


def _order_meta(order: dict) -> dict:
    try:
        data = json.loads(order.get("metadata") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _purchase_text(
    db: Database, order: dict, product_title: str, delivery: str, *, for_owner: bool
) -> str:
    from app.autolike_report import purchase_layout_text

    method = str(order.get("paid_with") or "pix").lower()
    meta = _order_meta(order) if method == "binance" else {}
    return purchase_layout_text(
        method=method,
        client_label=await _user_label(db, int(order["user_id"])),
        product=str(product_title),
        quantity=_purchase_quantity(order, delivery),
        amount_brl=format_price(Decimal(str(order.get("amount") or "0"))),
        usdt=str(meta.get("usdt") or "").strip(),
        # Conta Binance, pedido, UID e ID do cliente: só para os donos.
        binance_user=str(meta.get("binance_user") or "").strip() if for_owner else "",
        order_id=int(order["id"]) if for_owner else None,
        client_id=int(order["user_id"]) if for_owner else None,
        uid=str(order.get("game_id") or "").strip() if for_owner else "",
    )


async def notify_owners_payment(
    bot: Bot,
    config,
    db: Database,
    order: dict,
    product_title: str,
    delivery: str = "",
) -> None:
    """Avisa cada dono no privado, deixando claro se foi PIX, Binance ou Saldo."""
    from app.autolike_report import send_fancy

    text = await _purchase_text(db, order, product_title, delivery, for_owner=True)
    for owner_id in getattr(config, "admin_ids", []) or []:
        try:
            await send_fancy(bot, db, int(owner_id), text)
        except Exception:  # noqa: BLE001
            logger.warning("Não consegui avisar o dono %s do pedido %s", owner_id, order.get("id"))


async def announce_purchase(
    bot: Bot,
    db: Database,
    order: dict,
    product_title: str,
    delivery: str = "",
    config=None,
) -> None:
    # Cada pedido é avisado uma vez só (webhook, monitor e "Já paguei").
    if not await db.mark_purchase_announced(int(order["id"])):
        return

    if config is not None:
        await notify_owners_payment(bot, config, db, order, product_title, delivery)

    if str(order.get("product_code") or "") == "balance_topup":
        return
    # Só anuncia no grupo compras feitas no privado do bot (grupos têm ID negativo).
    payment_chat_id = order.get("payment_chat_id")
    if payment_chat_id is not None and int(payment_chat_id) < 0:
        return

    target = await _target(db)
    if not target:
        return
    from app.autolike_report import send_fancy

    chat_id, thread_id = target
    kwargs = {"reply_markup": await _acquire_markup(bot)}
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    text = await _purchase_text(db, order, product_title, delivery, for_owner=False)
    try:
        await send_fancy(bot, db, chat_id, text, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao anunciar compra do pedido %s", order.get("id"))


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
