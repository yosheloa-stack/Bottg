"""Ativação do Auto-Like Premium após confirmação do PIX."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app import texts
from app.config import Config
from app.database import Database
from app.keyboards.inline import back_home
from app.services.ffhub_shop import FFHubShopApi, parse_like_delivery
from app.utils import clean_game_id

router = Router(name="paid_autolike")


def _valid_uid(uid: str) -> bool:
    return uid.isdigit() and 8 <= len(uid) <= 11


@router.message(F.text)
async def receive_paid_autolike_uid(
    message: Message,
    db: Database,
    config: Config,
    ffhub_shop: FFHubShopApi,
) -> None:
    if not message.from_user or message.chat.type != "private":
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
    if not product or product.delivery not in {"ffhub_autolike", "ffhub_like_once"}:
        await db.update_order_status(order["id"], "failed")
        await message.reply(
            "⚠️ Não consegui localizar o produto deste pedido. "
            "Fale com o suporte.",
            reply_markup=back_home(),
        )
        return

    await db.set_order_game_id(order["id"], uid)

    if product.delivery == "ffhub_like_once":
        await db.update_order_status(order["id"], "paid")
        sending = await message.reply("💎 Enviando seus likes...")
        result = await ffhub_shop.send_paid_likes(uid)

        if not result.ok:
            await db.update_order_status(
                order["id"],
                "failed",
                str(result.data or result.error or ""),
            )
            error = (
                result.error
                or (result.data.get("erro") if isinstance(result.data, dict) else None)
                or (result.data.get("error") if isinstance(result.data, dict) else None)
                or "A FFHub não conseguiu concluir o envio."
            )
            await sending.edit_text(
                "⚠️ <b>Pagamento confirmado, mas o envio falhou.</b>\n\n"
                f"🆔 UID: <code>{uid}</code>\n"
                f"Erro: {error}\n\n"
                "O pedido ficou registrado para suporte."
            )
            return

        data = result.data if isinstance(result.data, dict) else {}
        parsed_like = parse_like_delivery(data)
        sent = parsed_like["sent"]
        sent_display = sent if sent is not None else "—"
        nick = parsed_like["nickname"]

        await db.update_order_status(order["id"], "delivered", str(data))
        await db.decrement_stock(order["product_code"])

        await sending.edit_text(
            "💚 <b>LIKE2 ENVIADO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Jogador: <b>{nick}</b>\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"❤️ Enviados: <b>+{sent_display}</b>\n\n"
            "✅ Pedido concluído com sucesso.",
            reply_markup=back_home(),
        )
        return

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
